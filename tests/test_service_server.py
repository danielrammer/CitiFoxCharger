import copy
import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
from citifoxcharger.config import ConfigStore, DEFAULT, validate
from citifoxcharger.secrets_store import SecretStore
from citifoxcharger.server import LocalServer
from citifoxcharger.service import Service
from citifoxcharger.skoda import SkodaAdapter, IntegrationError

VIN = "TMBJB9NY5RF999999"
KEY = "local-test-api-key"


class FakeSmartfox:
    def __init__(self): self.calls = 0
    def read(self, config):
        self.calls += 1
        return {"available": True, "registers": [], "write_supported": False}


def vehicle_data():
    return {"vehicle": {"vin": VIN, "charging": {"carCapturedTimestamp": datetime.now(timezone.utc).isoformat(),
            "status": {"state": "READY_FOR_CHARGING", "battery": {"stateOfChargeInPercent": 50}}},
            "operations": [{"name": "startCharging"}, {"name": "stopCharging"}]}, "errors": []}


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = ConfigStore(self.temp.name)
        self.config.value["skoda"]["vin"] = VIN
        self.secrets = SecretStore(Path(self.temp.name))
        self.secrets.set(KEY)
        self.data, self.calls, self.post_status = vehicle_data(), [], 202
        def request(method, path, key):
            self.calls.append(method)
            if method == "POST":
                if self.post_status == "timeout":
                    raise IntegrationError("network", "Ausgang unbekannt", uncertain=True)
                return self.post_status, {}, b""
            return 200, {}, json.dumps(self.data).encode()
        self.sf = FakeSmartfox()
        self.adapter = SkodaAdapter(request)
        self.service = Service(self.config, self.secrets, self.sf, self.adapter)

    def test_read_cache_resists_ui_polling(self):
        for _ in range(5): self.service.status()
        self.assertEqual(self.sf.calls, 1)
        self.assertEqual(self.calls, ["GET"])

    def test_explicit_refresh_skips_cache_but_has_one_minute_spacing(self):
        self.service.status()
        self.service.refresh_now()
        self.assertEqual(self.calls, ["GET"])
        self.service.last_attempt["skoda"] -= 61
        self.service.manual_refresh_after = 0
        self.service.refresh_now()
        self.assertEqual(self.calls, ["GET", "GET"])

    def test_spare_quota_allows_faster_updates_while_charging(self):
        self.service.status()
        self.adapter.quota_remaining = "15"
        self.adapter.quota_reset_at = self.adapter.clock() + 180
        self.service.cache["skoda"]["state"] = "CHARGING"
        self.assertEqual(self.service.poll_interval("skoda"), 60)
        self.adapter.quota_remaining = "5"
        self.adapter.quota_reset_at = self.adapter.clock() + 3600
        self.assertGreaterEqual(self.service.poll_interval("skoda"), 1199)

    def test_quota_reserve_keeps_supported_stop_available(self):
        original = self.adapter.request
        def limited(method, path, key):
            status, headers, body = original(method, path, key)
            return status, {"RateLimit-Remaining":"2", "RateLimit-Reset":"180"}, body
        self.adapter.request = limited
        self.service.status()
        self.service.next_due["skoda"] = 0
        result = self.service.status()
        self.assertTrue(result["controls"]["stop"])
        self.assertFalse(result["controls"]["start"])
        self.assertEqual(result["skoda"]["code"], "quota_reserved")
        self.assertEqual(self.calls, ["GET"])

    def near_target_fixture(self):
        now=datetime.now(timezone.utc).isoformat()
        self.service.cache['skoda']={'soc':50,'available':True,'stale':False,'code':'ok','captured_at':now}
        self.service.cache['smartfox']={'charger':{'available':True,'telemetry_available':True,'power_kw':3.6,'read_at':now}}
        self.service.charging.save(replace=True,status='monitoring',vin=VIN,target_percent=52)
        self.adapter.quota_remaining='10'
        self.adapter.quota_reset_at=self.adapter.clock()+1800
        self.adapter.blocked_until=0
        self.service.last_attempt['skoda']=time.monotonic()-10
        self.service.next_due['skoda']=time.monotonic()+300

    def test_near_target_advances_at_most_two_reads_on_distinct_reports_and_persists_budget(self):
        from citifoxcharger.charging import ChargingSession
        self.near_target_fixture()
        self.service.prioritize_target_poll()
        self.assertAlmostEqual(self.service.next_due['skoda'],self.service.last_attempt['skoda']+60)
        self.assertEqual(self.service.charging.value['target_probe_count'],1)
        self.service.next_due['skoda']=time.monotonic()+300
        due=self.service.next_due['skoda']
        self.service.cache['skoda']['captured_at']=self.service.cache['skoda']['captured_at'].replace('+00:00','Z')
        self.service.prioritize_target_poll()
        self.assertEqual(self.service.next_due['skoda'],due)
        self.service.cache['skoda']['captured_at']=(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat()
        self.service.prioritize_target_poll()
        self.assertEqual(self.service.charging.value['target_probe_count'],2)
        self.service.charging=ChargingSession(self.service,None)
        self.service.next_due['skoda']=due
        self.service.cache['skoda']['captured_at']=(datetime.now(timezone.utc)+timedelta(seconds=2)).isoformat()
        self.service.prioritize_target_poll()
        self.assertEqual(self.service.next_due['skoda'],due)
        self.assertEqual(self.calls,[])

    def test_near_target_never_shortens_cooldown_or_spends_reserve_on_old_or_unconfirmed_data(self):
        changes=[lambda:self.service.cache['skoda'].update(captured_at=(datetime.now(timezone.utc)-timedelta(minutes=6)).isoformat()),
                 lambda:self.service.cache['skoda'].update(available=False),
                 lambda:self.service.cache['skoda'].update(code='quota_reserved'),
                 lambda:self.service.cache['smartfox']['charger'].update(power_kw=0),
                 lambda:self.service.cache['smartfox']['charger'].update(phase_switching=True),
                 lambda:self.service.charging.save(phase_start_pending=True),
                 lambda:self.service.charging.save(stop_requested=True),
                 lambda:self.service.charging.save(vin='TMBJB9NY5RF999998'),
                 lambda:setattr(self.adapter,'quota_remaining','5'),
                 lambda:setattr(self.adapter,'blocked_until',self.adapter.clock()+60),
                 lambda:setattr(self.adapter,'quota_reset_at',0)]
        for change in changes:
            with self.subTest(change=change):
                self.near_target_fixture();change()
                due=self.service.next_due['skoda']
                self.service.prioritize_target_poll()
                self.assertEqual(self.service.next_due['skoda'],due)
                self.assertNotIn('target_probe_count',self.service.charging.value)

    def test_near_target_does_not_reserve_acceleration_when_regular_poll_is_already_faster(self):
        self.near_target_fixture()
        self.service.next_due['skoda']=self.service.last_attempt['skoda']+60
        self.service.prioritize_target_poll()
        self.assertNotIn('target_probe_count',self.service.charging.value)

    def test_command_gated_by_capabilities_and_staleness(self):
        self.data["vehicle"]["operations"] = []
        self.service.status()
        with self.assertRaises(IntegrationError): self.service.command("start", "request-id-00001")
        self.assertNotIn("POST", self.calls)
        self.service.reset()
        self.data = vehicle_data()
        self.data["vehicle"]["charging"]["carCapturedTimestamp"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        with self.assertRaises(IntegrationError): self.service.command("start", "request-id-00002")
        self.assertNotIn("POST", self.calls)

    def test_rechecking_source_age_even_when_cache_is_not_due(self):
        self.service.status()
        self.assertTrue(self.service.can_command("start"))
        self.service.cache["skoda"]["captured_at"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.assertFalse(self.service.can_command("start"))
        self.assertTrue(self.service.status()["skoda"]["stale"])

    def test_expired_key_header_disables_commands_without_new_poll(self):
        self.service.status()
        self.assertTrue(self.service.can_command("start"))
        self.adapter.key_expires_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        self.assertFalse(self.service.can_command("start"))

    def test_accepted_is_not_observed_and_duplicate_is_not_posted(self):
        result = self.service.command("start", "request-id-00001")
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(self.service.cache["skoda"]["state"], "READY_FOR_CHARGING")
        duplicate = self.service.command("start", "request-id-00001")
        self.assertEqual(result, duplicate)
        self.assertEqual(self.calls.count("POST"), 1)
        with self.assertRaises(IntegrationError): self.service.command("start", "request-id-00002")

    def test_only_newer_matching_telemetry_confirms_command(self):
        self.service.command("start", "request-id-00001")
        cached = self.service.cache["skoda"]
        cached["state"] = "CHARGING"
        cached["captured_at"] = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        self.service.check_command_observation()
        self.assertEqual(self.service.last_command["status"], "accepted")
        cached["captured_at"] = datetime.now(timezone.utc).isoformat()
        self.service.check_command_observation()
        self.assertEqual(self.service.last_command["status"], "observed")

    def test_timeout_not_retried_and_state_not_faked(self):
        self.post_status = "timeout"
        result = self.service.command("start", "request-id-00001")
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(self.service.can_command("start"))
        self.service.command("start", "request-id-00001")
        self.assertEqual(self.calls.count("POST"), 1)

    def test_rate_limit_survives_config_and_key_changes(self):
        self.adapter.blocked_until = time.monotonic() + 100
        self.service.save_config(self.config.value)
        self.service.set_key(KEY, False)
        self.service.status()
        self.assertFalse(self.service.can_command("start"))
        self.assertEqual(self.calls, [])

    def test_clear_key_removes_status_and_secret(self):
        self.service.status()
        self.service.clear_key()
        result = self.service.status()
        self.assertEqual(result["skoda"]["code"], "not_configured")
        self.assertFalse(result["controls"]["start"])
        self.assertEqual(self.secrets.key, "")

    def test_upstream_read_failure_retains_old_soc_marked_stale(self):
        self.service.status()
        self.service.next_due["skoda"] = 0
        def offline(*args): raise IntegrationError("auth", "Key expired")
        self.adapter.request = offline
        result = self.service.status()
        self.assertEqual(result["skoda"]["soc"], 50)
        self.assertTrue(result["skoda"]["stale"])
        self.assertFalse(result["skoda"]["available"])
        self.assertFalse(result["controls"]["stop"])


class ConfigSecretTests(unittest.TestCase):
    def test_default_hardware_and_atomic_roundtrip(self):
        self.assertEqual(DEFAULT["smartfox"]["firmware"], "EM3 00.01.16.08")
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(directory)
            store.save(DEFAULT)
            self.assertEqual(ConfigStore(directory).value, DEFAULT)
            self.assertFalse(list(Path(directory).glob("*.tmp")))

    def test_invalid_public_loopback_and_hostname_rejected(self):
        for host in ("8.8.8.8", "127.0.0.1", "169.254.1.1", "localhost", "example.com", "192.168.0.81/path"):
            data = copy.deepcopy(DEFAULT)
            data["smartfox"]["host"] = host
            with self.subTest(host=host), self.assertRaises(ValueError): validate(data)

    def test_bad_config_does_not_overwrite_saved_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore(directory)
            store.save(DEFAULT)
            for modify in (lambda c:c["smartfox"].update(port=True), lambda c:c["skoda"].update(vin="bad"),
                           lambda c:c["smartfox"].update(profile="write_mode"), lambda c:c.update(secret="bad")):
                data = copy.deepcopy(DEFAULT)
                modify(data)
                with self.assertRaises(ValueError): store.save(data)
                self.assertEqual(ConfigStore(directory).value, DEFAULT)

    def test_memory_secret_does_not_enter_config_or_file(self):
        with tempfile.TemporaryDirectory() as directory:
            secrets = SecretStore(Path(directory))
            secrets.set(KEY)
            self.assertEqual(list(Path(directory).iterdir()), [])
            self.assertNotIn(KEY, json.dumps(secrets.status()))
            for bad in ("short", "white space key", "new\nline-key", "ä"*12):
                with self.assertRaises(ValueError): secrets.set(bad)

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI test")
    def test_windows_dpapi_roundtrip_and_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            secrets = SecretStore(Path(directory))
            secrets.set(KEY, persist=True)
            self.assertNotIn(KEY.encode(), secrets.path.read_bytes())
            self.assertEqual(SecretStore(Path(directory)).key, KEY)
            secrets.clear()
            self.assertFalse(secrets.path.exists())


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        config = ConfigStore(self.temp.name)
        self.service = Service(config, SecretStore(Path(self.temp.name)), FakeSmartfox())
        self.server = LocalServer(0, self.service)
        self.host = f"127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temp.cleanup()

    def call(self, path, data=None, **override):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        headers = {"Host":self.host, "Content-Type":"application/json", "Origin":"http://"+self.host,
                   "Cookie":"lz_session="+self.server.session, "X-CSRF-Token":self.server.csrf}
        headers.update(override)
        connection.request("GET" if data is None else "POST", path,
                           body=json.dumps(data) if data is not None else None, headers=headers)
        response = connection.getresponse()
        body, status, returned = response.read(), response.status, dict(response.headers)
        connection.close()
        return status, body, returned

    def test_diagnostics_are_authenticated_sanitized_and_do_not_poll_or_command(self):
        self.service.cache['skoda']={'soc':40,'vin':VIN,'message':KEY,'quota_remaining':'16'}
        self.service.charging.save(status='error',target_percent=83,stop_cause='request_quota')
        with patch.object(self.service,'status',side_effect=AssertionError('must not poll')):
            code,body,headers=self.call('/api/diagnostics')
        self.assertEqual(code,200)
        data=json.loads(body)
        self.assertEqual(data['session']['stop_cause'],'request_quota')
        self.assertEqual(data['session']['target_percent'],83)
        self.assertEqual(data['backend_version'],data['program_version'])
        self.assertNotIn(VIN,body.decode());self.assertNotIn(KEY,body.decode())
        self.assertIn('no-store',headers['Cache-Control'])
        self.assertEqual(self.call('/api/diagnostics',Cookie='')[0],401)

    def test_diagnostic_download_is_an_authenticated_json_attachment_without_device_calls(self):
        self.service.cache['skoda']={'soc':81,'vin':VIN,'message':KEY}
        with patch.object(self.service,'status',side_effect=AssertionError('must not poll')):
            code,body,headers=self.call('/api/diagnostics/download')
        self.assertEqual(code,200)
        self.assertEqual(headers['Content-Disposition'],'attachment; filename="CitiFoxCharger-Diagnostics.json"')
        self.assertIn('application/json',headers['Content-Type'])
        self.assertIn('no-store',headers['Cache-Control'])
        self.assertEqual(json.loads(body)['car']['soc'],81)
        self.assertNotIn(VIN,body.decode());self.assertNotIn(KEY,body.decode())
        self.assertEqual(self.call('/api/diagnostics/download',Cookie='')[0],401)
        self.assertNotIn('Content-Disposition',self.call('/api/diagnostics')[2])

    def test_static_and_session_security_headers(self):
        status, body, headers = self.call("/")
        self.assertEqual(status, 200)
        self.assertIn("CitiFoxCharger", body.decode())
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        status, body, headers = self.call("/api/session")
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])

    def test_brand_images_are_served_as_png_without_exposing_arbitrary_paths(self):
        for path in ('/icon.png','/logo.png','/charging-car.png'):
            status,body,headers=self.call(path)
            self.assertEqual(status,200)
            self.assertEqual(headers['Content-Type'],'image/png')
            self.assertTrue(body.startswith(b'\x89PNG\r\n\x1a\n'))
        for path in ('/brand/icon.png','/brand/../config.json'):
            self.assertEqual(self.call(path)[0],404)

    def test_release_metadata_in_health_has_no_private_paths(self):
        status,body,_=self.call('/health')
        self.assertEqual(status,200)
        release=json.loads(body)['release']
        self.assertEqual(set(release),{'version','git_hash','date'})
        self.assertNotIn(self.temp.name,body.decode())

    def test_rebinding_cross_origin_csrf_and_no_cookie_rejected(self):
        for path, data, headers, expected in [
            ("/api/config", None, {"Host":"evil.example"},403),
            ("/api/config", None, {"Origin":"https://evil.example"},403),
            ("/api/config", None, {"Cookie":""},401),
            ("/api/config", DEFAULT, {"X-CSRF-Token":"wrong"},403),
            ("/api/config", DEFAULT, {"Origin":""},403),
            ("/api/session", None, {"Sec-Fetch-Site":"cross-site"},403),
        ]:
            with self.subTest(headers=headers): self.assertEqual(self.call(path,data,**headers)[0],expected)

    def test_key_write_only_config_redacted_and_removable(self):
        status, body, _ = self.call("/api/key", {"key":KEY,"persist":False})
        self.assertEqual(status, 200)
        self.assertNotIn(KEY, body.decode())
        status, body, _ = self.call("/api/config")
        self.assertNotIn(KEY, body.decode())
        self.assertTrue(json.loads(body)["key"]["configured"])
        self.assertEqual(self.call("/api/key/clear", {})[0], 200)
        self.assertEqual(self.service.secrets.key, "")

    def test_smartfox_write_cannot_be_enabled_via_http(self):
        self.assertEqual(self.call("/api/smartfox/command", {"action":"manual"})[0],409)
        self.assertFalse(self.call("/api/status")[0] != 200)
        self.assertEqual(self.call("/api/config", {**DEFAULT,"enable_writes":True})[0],400)

    def test_skoda_commands_blocked_without_credentials(self):
        status, body, _ = self.call("/api/skoda/command", {"action":"start","request_id":"request-id-00001"})
        self.assertEqual(status,409)
        self.assertEqual(json.loads(body)["code"],"blocked")

    def test_limit_restore_requires_session_csrf_and_exact_request(self):
        body={'request_id':'restore-http-test-0001'}
        self.assertEqual(self.call('/api/charging/restore-limit',body,Cookie='')[0],401)
        self.assertEqual(self.call('/api/charging/restore-limit',body,**{'X-CSRF-Token':'wrong'})[0],403)
        self.assertEqual(self.call('/api/charging/restore-limit',{})[0],400)
        self.assertEqual(self.call('/api/charging/restore-limit',body)[0],409)

    def test_stale_start_flag_is_optional_boolean_and_scoped_to_manual_start(self):
        body={"target_percent":90,"request_id":"explicit-stale-test-0001"}
        for value in ("true",1,None,[],{}):
            with self.subTest(value=value):
                self.assertEqual(self.call("/api/charging/start",{**body,"allow_stale":value})[0],400)
        for value in (False,True):
            self.assertEqual(self.call("/api/charging/start",{**body,"allow_stale":value})[0],409)
        self.assertEqual(self.call("/api/charging/start",body)[0],409)
        self.assertEqual(self.call("/api/skoda/command",{"action":"start","request_id":body["request_id"],"allow_stale":True})[0],400)

    def test_background_shutdown_requires_local_session_and_no_monitor(self):
        self.assertEqual(self.call("/api/app/quit", {}, **{"X-CSRF-Token":"wrong"})[0],403)
        self.service.charging.save(status="monitoring",target_percent=90)
        self.assertEqual(self.call("/api/app/quit", {})[0],400)
        self.assertFalse(self.service.stopping)
        self.service.charging.save(status="complete")
        self.assertEqual(self.call("/api/app/quit", {})[0],200)

    def test_app_update_requires_csrf_empty_body_and_returns_a_job(self):
        self.assertEqual(self.call('/api/app/update',{},**{'X-CSRF-Token':'wrong'})[0],403)
        self.assertEqual(self.call('/api/app/update',{'directory':'arbitrary'})[0],400)
        with patch.object(self.server.updater,'launch',return_value={'job_id':'a'*32,'phase':'queued'}) as launch:
            status,body,_=self.call('/api/app/update',{})
            self.assertEqual(status,202)
            self.assertEqual(json.loads(body)['phase'],'queued')
            launch.assert_called_once_with(self.service)

    def test_pending_update_blocks_settings_but_keeps_emergency_stop_available(self):
        self.service.app_update_pending=True
        self.assertEqual(self.call('/api/key',{'key':KEY,'persist':False})[0],400)
        with patch.object(self.service,'stop_charge',return_value={'status':'stop_pending'}) as stop:
            self.assertEqual(self.call('/api/charging/stop',{'request_id':'update-emergency-stop-001'})[0],200)
            stop.assert_called_once()

    def test_phase_preparation_returns_to_http_and_can_be_stopped_without_start(self):
        from test_charging import ChargingTests
        fixture=ChargingTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.service=self.server.service=fixture.service
        fixture.web.phase_switching=True
        original=fixture.service.car.request
        def request(method,path,key,payload=None):
            if method=='PUT':return 202,{},b''
            return original(method,path,key)
        fixture.service.car.request=request
        fixture.data['vehicle']['operations'].append({'name':'setChargingLimit'})
        fixture.data['vehicle']['charging']['settings']={'targetStateOfChargeInPercent':80}
        fixture.service.next_due['skoda']=0
        status,body,_=self.call('/api/charging/start',{'target_percent':90,'request_id':'phase-http-start-00001'})
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['vehicle_limit']['phase'],'setting')
        self.assertEqual(fixture.effects,['OFF'])
        fixture.data['vehicle']['charging']['settings']['targetStateOfChargeInPercent']=90
        fixture.data['vehicle']['charging']['carCapturedTimestamp']=datetime.now(timezone.utc).isoformat()
        fixture.service.next_due['skoda']=0;fixture.service.update('skoda');fixture.service.charging.tick()
        self.assertTrue(fixture.service.charging.value['phase_start_pending'])
        self.assertEqual(fixture.effects,['OFF','M+'])
        status,body,_=self.call('/api/status')
        self.assertEqual(status,200)
        self.assertTrue(json.loads(body)['charging']['can_stop'])
        status,body,_=self.call('/api/charging/stop',{'request_id':'phase-http-stop-00001'})
        self.assertEqual(status,200)
        self.assertFalse(json.loads(body)['phase_start_pending'])
        fixture.web.phase_switching=False
        fixture.phase_tick()
        self.assertNotIn('start',fixture.effects)
        self.assertEqual(fixture.web.mode,'OFF')

    def test_quit_can_close_locally_stopped_session_without_claiming_car_confirmation(self):
        from test_charging import FakeWeb
        effects=[]
        self.service.web=FakeWeb(effects)
        self.service.charging.save(status='stop_pending',stop_requested=True,auto_restored=True,stop_intent_epoch=time.time()-10)
        self.assertEqual(self.call('/api/app/quit',{})[0],200)
        self.assertEqual(self.service.charging.value['status'],'closed')
        self.assertFalse(self.service.charging.value['vehicle_stop_confirmed'])
        self.assertEqual(effects,[])
        self.assertTrue(self.service.stopping)
        self.assertFalse(self.service.can_command("start"))

    def test_target_route_requires_csrf_valid_integer_and_active_session(self):
        self.assertEqual(self.call("/api/charging/target",{"target_percent":90},**{"X-CSRF-Token":"wrong"})[0],403)
        for value in (True,0,101,"90"):
            self.assertEqual(self.call("/api/charging/target",{"target_percent":value})[0],400)
        self.assertEqual(self.call("/api/charging/target",{"target_percent":90})[0],409)
        self.assertEqual(self.call("/api/charging/target",{"target_percent":90,"extra":1})[0],400)

    def test_restart_route_requires_session_and_keeps_exit_guard(self):
        self.assertEqual(self.call("/api/app/restart",{},**{"X-CSRF-Token":"wrong"})[0],403)
        self.service.charging.save(status="monitoring",target_percent=90)
        self.assertEqual(self.call("/api/app/restart",{})[0],400)
        self.assertFalse(self.server.restart_requested)
        self.service.charging.save(status="complete")
        self.assertEqual(self.call("/api/app/restart",{})[0],200)
        self.assertTrue(self.server.restart_requested)
        self.assertTrue(self.service.stopping)

    def test_heater_route_is_scoped_and_csrf_protected(self):
        body={"device":"heater","mode":"manual","request_id":"heater-mode-test-0001"}
        self.assertEqual(self.call("/api/smartfox/heater",body,**{"X-CSRF-Token":"wrong"})[0],403)
        self.assertEqual(self.call("/api/smartfox/heater",body)[0],409)
        for change in ({"device":"relay4"},{"mode":"timer"},{"request_id":"short"}):
            self.assertEqual(self.call("/api/smartfox/heater",{**body,**change})[0],400)

    def test_manual_restore_confirmation_requires_session_csrf_and_explicit_truth(self):
        body={'request_id':'manual-restored-http-0001','target_percent':80,'confirmed_at_vehicle':True}
        self.assertEqual(self.call('/api/charging/confirm-restored-limit',body,Cookie='')[0],401)
        self.assertEqual(self.call('/api/charging/confirm-restored-limit',body,**{'X-CSRF-Token':'wrong'})[0],403)
        for invalid in (False,'true',1,None):
            self.assertEqual(self.call('/api/charging/confirm-restored-limit',{**body,'confirmed_at_vehicle':invalid})[0],400)
        with patch.object(self.service.vehicle_limit,'confirm_restore_manually',return_value={'status':'error'}) as confirm:
            self.assertEqual(self.call('/api/charging/confirm-restored-limit',body)[0],200)
            confirm.assert_called_once_with(80,'manual-restored-http-0001')

    def test_charger_mode_route_rejects_other_modes_and_requires_csrf(self):
        body={"mode":"OFF","request_id":"charger-mode-test-0001"}
        self.assertEqual(self.call("/api/smartfox/charger-mode",body,**{"X-CSRF-Token":"wrong"})[0],403)
        self.assertEqual(self.call("/api/smartfox/charger-mode",body)[0],409)
        for mode in ("A","M","timer"):
            self.assertEqual(self.call("/api/smartfox/charger-mode",{**body,"mode":mode})[0],400)


if __name__ == "__main__": unittest.main()
