import copy
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from ladezentrale.charging import ChargingSession
from ladezentrale.config import ConfigStore, DEFAULT
from ladezentrale.secrets_store import SecretStore
from ladezentrale.service import Service
from ladezentrale.skoda import IntegrationError, SkodaAdapter
from ladezentrale.smartfox_web import SmartfoxWeb, SmartfoxControlError
from ladezentrale.instance import InstanceLock
from ladezentrale.power import PowerGuard
from test_power_background import FakePower

VIN = "TMBJB9NY5RF999999"


def xml(mode=2, firmware="EM3  00.01.16.08", power="2.93 <span>kW</span>", state=2, info=8, current=11.5, ttl=30):
    return (f'<values><value id="version">{firmware}</value><value id="hidCcMode1">{mode}</value>'
            f'<value id="cc1Power"><![CDATA[{power}]]></value><value id="hidCcMplusMode">[1,0,0,0,0]</value>'
            f'<value id="hidCcSt1">{state}</value><value id="hidCc1Info">{info}</value>'
            f'<value id="hidCcControlCurrent">[{current},0,0,0,0]</value><value id="hidCcConnTimeout1">{ttl}</value></values>').encode()


class SmartfoxWebTests(unittest.TestCase):
    def setUp(self):
        self.config = copy.deepcopy(DEFAULT["smartfox"])

    def test_read_mode_and_physical_power_from_vendor_display(self):
        result = SmartfoxWeb(lambda *args: xml()).read(self.config)
        self.assertEqual(result["mode"], "A+")
        self.assertEqual(result["power_kw"], 2.93)
        self.assertFalse(result["control_available"])

    def test_writes_require_explicit_opt_in(self):
        paths = []
        def request(host, path, timeout): paths.append(path); return xml(0)
        web = SmartfoxWeb(request)
        with self.assertRaises(SmartfoxControlError): web.set_mode(self.config, "A+")
        self.assertEqual(paths, ["/values.xml"])

    def test_already_a_plus_requires_no_write(self):
        paths = []
        def request(host, path, timeout): paths.append(path); return xml(2)
        result = SmartfoxWeb(request).set_mode(self.config, "A+")
        self.assertEqual(result["mode"], "A+")
        self.assertEqual(paths, ["/values.xml"])

    def test_firmware_mismatch_and_malformed_xml_block_control(self):
        for body in (xml(firmware="EM3 00.01.01.00"), b"<bad", b'<!DOCTYPE x [<!ENTITY a "b">]><values/>'):
            with self.subTest(body=body), self.assertRaises(SmartfoxControlError):
                SmartfoxWeb(lambda *args: body, True).set_mode(self.config, "M+")

    def test_opted_in_exact_modes_and_readback(self):
        mode, paths = [0], []
        def request(host, path, timeout):
            paths.append(path)
            if path.startswith("/setswcc.cgi"):
                mode[0] = 4 if "mode=4&" in path else 2
                return b"OK"
            return xml(mode[0])
        web = SmartfoxWeb(request, True)
        self.assertEqual(web.set_mode(self.config, "M+")["mode"], "M+")
        self.assertEqual(web.set_mode(self.config, "A+")["mode"], "A+")
        self.assertIn("/setswcc.cgi?num=0&mode=4&value=100", paths)
        self.assertIn("/setswcc.cgi?num=0&mode=2&value=100", paths)
        with self.assertRaises(ValueError): web.set_mode(self.config, "OFF")

    def test_unconfirmed_mode_never_reports_success(self):
        web = SmartfoxWeb(lambda *args: xml(0), True)
        with self.assertRaises(SmartfoxControlError): web.set_mode(self.config, "M+")

    def test_start_waits_for_live_readiness_and_settling_without_writes(self):
        clock, paths = [0], []
        def request(host, path, timeout):
            paths.append(path)
            return xml(4, current=0 if clock[0] < 2 else 16)
        def sleep(seconds): clock[0] += seconds
        result = SmartfoxWeb(request, True, lambda:clock[0], sleep).wait_ready(self.config)
        self.assertGreaterEqual(clock[0],3)
        self.assertTrue(result["ready_for_start"])
        self.assertEqual(set(paths),{"/values.xml"})

    def test_missing_current_or_stale_device_never_becomes_ready(self):
        for body in (xml(4,current=0), xml(4,ttl=2), xml(4,state=5), xml(4,info=22)):
            clock=[0]
            def sleep(seconds): clock[0] += seconds
            web=SmartfoxWeb(lambda *args:body,True,lambda:clock[0],sleep)
            with self.subTest(body=body), self.assertRaises(SmartfoxControlError):
                web.wait_ready(self.config,timeout=4)

    def test_authentication_error_or_lost_mplus_blocks_start(self):
        for body in (xml(4,info=14),xml(4,state=4),xml(2)):
            with self.subTest(body=body), self.assertRaises(SmartfoxControlError):
                SmartfoxWeb(lambda *args:body,True).wait_ready(self.config)


class InstanceTests(unittest.TestCase):
    def test_same_data_directory_cannot_run_two_monitors(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = InstanceLock(directory)
            try:
                with self.assertRaises(ValueError): InstanceLock(directory)
            finally:
                lock.close()
            replacement = InstanceLock(directory)
            replacement.close()

    def test_independent_data_directories_are_allowed(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            first, second = InstanceLock(a), InstanceLock(b)
            second.close()
            first.close()


class FakeWeb:
    def __init__(self, effects):
        self.mode, self.effects, self.allow_control = "A+", effects, True
        self.fail_mplus = False
        self.ready_calls, self.fail_ready = 0, False
    def read(self, config):
        return {"available": True, "mode": self.mode, "control_available": self.allow_control,
                "control_reason": None if self.allow_control else "Nicht freigegeben",
                "status_code":2, "telemetry_available":True,"power_kw":0.0,"read_at":datetime.now(timezone.utc).isoformat()}
    def set_mode(self, config, mode):
        if self.mode != mode:
            if not self.allow_control or (mode == "M+" and self.fail_mplus):
                raise SmartfoxControlError("Moduswechsel gesperrt")
            self.effects.append(mode)
            self.mode = mode
        return self.read(config)
    def wait_ready(self, config):
        self.ready_calls += 1
        if self.fail_ready:
            raise SmartfoxControlError("Ladebereitschaft fehlt")
        return self.read(config)


class ChargingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = ConfigStore(self.directory.name)
        self.config.value["skoda"]["vin"] = VIN
        self.secrets = SecretStore(Path(self.directory.name))
        self.secrets.set("test-key-for-local-tests")
        self.effects = []
        self.web = FakeWeb(self.effects)
        self.data = {"vehicle": {"vin": VIN, "charging": {"carCapturedTimestamp": datetime.now(timezone.utc).isoformat(),
                     "status": {"state": "CHARGING", "battery": {"stateOfChargeInPercent": 40}}},
                     "operations": [{"name": "startCharging"}, {"name": "stopCharging"}]}, "errors": []}
        self.post_code = 202
        self.stop_event = threading.Event()
        def request(method, path, key):
            if method == "POST":
                self.effects.append("start" if path.endswith("/start") else "stop")
                if path.endswith("/stop"): self.stop_event.set()
                return self.post_code, {}, b""
            return 200, {}, json.dumps(self.data).encode()
        class SF:
            def read(self, config): return {"available": True, "registers": []}
        self.service = Service(self.config, self.secrets, SF(), SkodaAdapter(request), self.web, PowerGuard(FakePower()))
        self.addCleanup(self.service.stop_worker)
        self.service.status()

    def force(self, target=80):
        return self.service.force_charge(target, "manual-session-id-00001")

    def update_soc(self, soc, state="CHARGING"):
        charging = self.data["vehicle"]["charging"]
        charging["carCapturedTimestamp"] = datetime.now(timezone.utc).isoformat()
        charging["status"]["state"] = state
        charging["status"]["battery"]["stateOfChargeInPercent"] = soc
        self.service.next_due["skoda"] = 0
        self.service.charging.next_attempt = 0
        self.service.command_after = 0

    def test_mplus_is_confirmed_before_vehicle_start(self):
        result = self.force()
        self.assertEqual(self.effects, ["M+", "start"])
        self.assertEqual(result["status"], "monitoring")
        self.assertEqual(result["target_percent"], 80)
        self.assertEqual(self.web.ready_calls,1)

    def test_no_vehicle_post_until_wallbox_readiness(self):
        self.web.fail_ready=True
        with self.assertRaises(IntegrationError): self.force()
        self.assertEqual(self.effects,["M+","A+"])
        self.assertEqual(self.service.cache["smartfox"]["charger"]["mode"],"A+")

    def test_confirmed_mplus_replaces_pre_switch_cache(self):
        self.force()
        self.service.charging.next_attempt=0
        result=self.service.status()
        self.assertEqual(result["smartfox"]["charger"]["mode"],"M+")
        self.assertNotIn("auf A+ gewechselt",result["charging"]["message"])

    def prepare_retry(self):
        self.force(90)
        self.service.command_after=0
        self.service.charging.value["last_start_epoch"]=time.time()-601
        self.service.cache["skoda"]["state"]="CHARGING_INTERRUPTED"
        self.service.last_command["status"]="unconfirmed"

    def test_unconfirmed_start_requires_explicit_new_request_and_preserves_target(self):
        self.prepare_retry()
        self.service.charging.next_attempt=0
        state=self.service.status()["charging"]
        self.assertTrue(state["can_retry_start"])
        self.assertTrue(state["can_force"])
        self.assertEqual(self.effects.count("start"),1)
        with self.assertRaises(IntegrationError):
            self.service.force_charge(95,"retry-with-other-target-01")
        result=self.service.force_charge(90,"explicit-retry-start-0001")
        self.assertEqual(result["target_percent"],90)
        self.assertEqual(self.effects.count("start"),2)
        self.service.force_charge(90,"explicit-retry-start-0001")
        self.assertEqual(self.effects.count("start"),2)

    def test_retry_blocked_while_charging_disconnected_stale_or_stopping(self):
        self.prepare_retry()
        baseline=copy.deepcopy(self.service.cache)
        for changes in ({"power_kw":1.3},{"status_code":3},{"telemetry_available":False},
                        {"read_at":(datetime.now(timezone.utc)-timedelta(seconds=60)).isoformat()}):
            self.service.cache=copy.deepcopy(baseline)
            self.service.cache["smartfox"]["charger"].update(changes)
            with self.subTest(changes=changes): self.assertFalse(self.service.charging.can_retry_start())
        self.service.cache=baseline
        self.service.charging.value["stop_requested"]=True
        self.assertFalse(self.service.charging.can_retry_start())

    def test_retry_needs_cooldown_and_evidence_or_ten_minute_timeout(self):
        self.prepare_retry()
        self.service.charging.value["last_start_epoch"]=time.time()-10
        self.assertFalse(self.service.charging.can_retry_start())
        self.service.charging.value["last_start_epoch"]=time.time()-60
        self.service.cache["skoda"]["captured_at"]=(datetime.now(timezone.utc)-timedelta(seconds=90)).isoformat()
        self.assertFalse(self.service.charging.can_retry_start())
        self.service.cache["skoda"]["captured_at"]=datetime.now(timezone.utc).isoformat()
        self.assertTrue(self.service.charging.can_retry_start())

    def test_unverified_smartfox_prevents_partial_vehicle_start(self):
        self.web.allow_control = False
        with self.assertRaises(IntegrationError): self.force()
        self.assertEqual(self.effects, [])
        self.assertEqual(self.service.charging.value["status"], "idle")

    def test_failed_mode_switch_does_not_start_car(self):
        self.web.fail_mplus = True
        with self.assertRaises(IntegrationError): self.force()
        self.assertNotIn("start", self.effects)
        self.assertEqual(self.web.mode, "A+")

    def test_car_rejection_restores_a_plus(self):
        self.post_code = 422
        with self.assertRaises(IntegrationError): self.force()
        self.assertEqual(self.effects, ["M+", "start", "A+"])
        self.assertEqual(self.service.charging.value["status"], "error")

    def test_invalid_and_already_reached_targets_never_write(self):
        for target in (0, 101, True, "80", 30, 40):
            with self.subTest(target=target), self.assertRaises((ValueError, IntegrationError)): self.force(target)
        self.assertEqual(self.effects, [])

    def test_target_stop_restores_a_plus_but_waits_for_new_vehicle_confirmation(self):
        self.force()
        self.update_soc(80)
        result = self.service.status()
        self.assertEqual(self.effects, ["M+", "start", "stop", "A+"])
        self.assertEqual(result["charging"]["status"], "stop_pending")
        self.service.status()
        self.assertEqual(self.effects.count("stop"), 1)
        self.update_soc(80, "READY_FOR_CHARGING")
        result = self.service.status()
        self.assertEqual(result["charging"]["status"], "complete")

    def test_monitor_continues_with_no_browser_requests(self):
        self.force()
        self.update_soc(81)
        self.service.start_worker()
        self.assertTrue(self.stop_event.wait(timeout=4))
        with self.service.lock:
            self.assertIn("A+", self.effects)

    def test_ninety_percent_background_stop_holds_and_releases_power_request(self):
        self.force(90)
        self.assertTrue(self.service.power.state()["requested"])
        self.update_soc(90)
        self.service.start_worker()
        self.assertTrue(self.stop_event.wait(timeout=4))
        self.update_soc(90, "READY_FOR_CHARGING")
        result = self.service.status()
        self.assertEqual(result["charging"]["status"], "complete")
        self.assertFalse(result["monitoring"]["power"]["requested"])
        self.assertEqual(self.effects[-2:], ["stop", "A+"])

    def test_app_exit_is_blocked_while_monitoring(self):
        self.force(90)
        with self.assertRaises(ValueError):
            self.service.prepare_shutdown()
        self.assertFalse(self.service.stopping)

    def test_user_stop_below_target(self):
        self.force(90)
        result = self.service.stop_charge("manual-stop-id-0000001")
        self.assertEqual(result["status"], "stop_pending")
        self.assertEqual(self.effects[-2:], ["stop", "A+"])

    def test_new_session_does_not_inherit_previous_stop_intent(self):
        self.force(80)
        self.update_soc(80)
        self.service.status()
        self.update_soc(80, "READY_FOR_CHARGING")
        self.service.status()
        self.service.force_charge(90, "new-manual-session-0002")
        self.service.charging.next_attempt = 0
        self.service.status()
        self.assertEqual(self.effects.count("stop"), 1)
        self.assertFalse(self.service.charging.value.get("stop_requested", False))
        self.assertEqual(self.service.charging.value["status"], "monitoring")

    def test_uncertain_stop_can_complete_from_new_stopped_telemetry(self):
        self.force()
        self.post_code = 503
        with self.assertRaises(IntegrationError):
            self.service.stop_charge("uncertain-stop-test-02")
        self.update_soc(50, "READY_FOR_CHARGING")
        self.service.status()
        self.assertEqual(self.effects.count("stop"), 1)
        self.assertEqual(self.web.mode, "A+")
        self.assertEqual(self.service.charging.value["status"], "complete")

    def test_stale_data_pauses_start_but_allows_supported_stop(self):
        self.force()
        self.service.cache["skoda"]["stale"] = True
        self.service.command_after = 0
        self.assertFalse(self.service.can_command("start"))
        self.assertTrue(self.service.can_command("stop"))
        self.service.charging.next_attempt = 0
        self.service.charging.tick()
        self.assertEqual(self.service.charging.value["status"], "paused")
        self.assertNotIn("stop", self.effects)

    def stale_soc(self, minutes=30):
        captured = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        self.data["vehicle"]["charging"]["carCapturedTimestamp"] = captured
        self.data["vehicle"]["charging"]["status"]["state"] = "CHARGING_INTERRUPTED"
        self.service.next_due["skoda"] = 0
        self.service.status()

    def stale_start(self):
        self.stale_soc()
        return self.service.force_charge(90, "explicit-stale-start-0001", allow_stale=True)

    def expire_freshness_deadline(self):
        self.service.command_after = 0
        self.service.charging.save(fresh_soc_deadline=time.time()-1)
        self.service.charging.next_attempt = 0

    def test_stale_start_requires_explicit_consent_and_preserves_old_timestamp(self):
        self.stale_soc()
        before = self.service.cache["skoda"]["captured_at"]
        self.assertTrue(self.service.charging.state()["can_force_with_stale"])
        self.assertFalse(self.service.charging.state()["can_force"])
        with self.assertRaises(IntegrationError): self.force(90)
        self.assertEqual(self.effects, [])
        result = self.service.force_charge(90, "explicit-stale-start-0001", allow_stale=True)
        self.assertTrue(result["awaiting_fresh_soc"])
        self.assertAlmostEqual(result["fresh_soc_deadline"]-result["last_start_epoch"], 300)
        self.assertEqual(self.effects, ["M+", "start"])
        self.assertTrue(self.service.cache["skoda"]["stale"])
        self.assertEqual(self.service.cache["skoda"]["captured_at"], before)
        self.service.force_charge(90, "explicit-stale-start-0001", allow_stale=True)
        self.assertEqual(self.effects.count("start"), 1)

    def test_stale_exception_never_overrides_missing_old_future_or_unsupported_data(self):
        self.stale_soc()
        baseline = copy.deepcopy(self.service.cache["skoda"])
        for changes in ({"captured_at":None}, {"captured_at":(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()},
                        {"captured_at":(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat()},
                        {"available":False}, {"soc":None}, {"operations":["stopCharging"]},
                        {"operations":["startCharging"]}):
            self.service.cache["skoda"] = {**baseline, **changes}
            with self.subTest(changes=changes):
                self.assertFalse(self.service.charging.can_start_with_stale())
                with self.assertRaises(IntegrationError):
                    self.service.force_charge(90,"blocked-stale-start-0001",allow_stale=True)
                self.assertEqual(self.effects, [])

    def test_stale_exception_requires_recent_api_read_valid_key_and_no_cooldown(self):
        self.stale_soc()
        self.service.last_success["skoda"] -= 301
        self.assertFalse(self.service.charging.can_start_with_stale())
        self.service.last_success["skoda"] = time.monotonic()
        self.service.car.key_expires_at = (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
        self.assertFalse(self.service.charging.can_start_with_stale())
        self.service.car.key_expires_at = None
        self.service.car.blocked_until = time.monotonic()+60
        self.assertFalse(self.service.charging.can_start_with_stale())
        self.service.car.blocked_until = 0
        self.service.command_after = time.monotonic()+30
        self.assertFalse(self.service.charging.can_start_with_stale())

    def test_stale_exception_requires_live_noncharging_wallbox(self):
        self.stale_soc()
        baseline = copy.deepcopy(self.service.cache["smartfox"]["charger"])
        for changes in ({"power_kw":2.1}, {"status_code":3}, {"telemetry_available":False},
                        {"control_available":False}, {"available":False},
                        {"read_at":(datetime.now(timezone.utc)-timedelta(seconds=60)).isoformat()}):
            self.service.cache["smartfox"]["charger"] = {**baseline, **changes}
            with self.subTest(changes=changes):
                self.assertFalse(self.service.charging.can_start_with_stale())
                with self.assertRaises(IntegrationError):
                    self.service.force_charge(90,"blocked-wallbox-start-01",allow_stale=True)
        self.assertEqual(self.effects, [])

    def test_explicit_retry_with_old_soc_retains_target_and_starts_once(self):
        self.prepare_retry()
        self.stale_soc()
        self.assertTrue(self.service.charging.state()["can_retry_start_with_stale"])
        with self.assertRaises(IntegrationError):
            self.service.force_charge(95,"different-stale-target-01",allow_stale=True)
        result = self.service.force_charge(90,"explicit-stale-retry-001",allow_stale=True)
        self.assertTrue(result["awaiting_fresh_soc"])
        self.assertEqual(self.effects.count("start"),2)
        self.service.command_after=0
        self.service.charging.value["last_start_epoch"]-=601
        self.assertFalse(self.service.charging.can_retry_start(allow_stale=True))

    def test_five_minute_deadline_requests_stop_and_restores_a_plus(self):
        self.stale_start()
        self.service.charging.next_attempt=0
        self.service.status()
        self.assertNotIn("stop",self.effects)
        self.expire_freshness_deadline()
        result = self.service.status()
        self.assertEqual(self.effects,["M+","start","stop","A+"])
        self.assertEqual(result["charging"]["status"],"stop_pending")
        self.assertFalse(result["charging"]["awaiting_fresh_soc"])
        self.assertIn("unbestätigt",result["charging"]["message"])
        self.service.charging.next_attempt=0
        self.service.status()
        self.assertEqual(self.effects.count("stop"),1)

    def test_fresh_post_start_soc_clears_deadline_and_target_still_stops(self):
        self.stale_start()
        self.update_soc(45)
        result = self.service.status()
        self.assertFalse(result["charging"]["awaiting_fresh_soc"])
        self.expire_freshness_deadline()
        self.service.status()
        self.assertNotIn("stop",self.effects)
        self.update_soc(90)
        self.service.status()
        self.assertEqual(self.effects[-2:],["stop","A+"])

    def test_pre_start_report_cannot_clear_freshness_deadline(self):
        self.stale_start()
        self.update_soc(45)
        self.data["vehicle"]["charging"]["carCapturedTimestamp"]=(datetime.now(timezone.utc)-timedelta(seconds=60)).isoformat()
        self.service.status()
        self.assertTrue(self.service.charging.value["awaiting_fresh_soc"])
        self.expire_freshness_deadline()
        self.service.status()
        self.assertIn("stop",self.effects)

    def test_stop_rejection_at_deadline_still_restores_a_plus_without_replay(self):
        self.stale_start()
        self.post_code=503
        self.expire_freshness_deadline()
        result=self.service.status()
        self.assertEqual(self.effects[-2:],["stop","A+"])
        self.assertEqual(result["charging"]["status"],"paused")
        self.assertTrue(result["charging"]["stop_requested"])
        self.assertFalse(result["charging"]["can_force_with_stale"])
        self.service.charging.next_attempt=0
        self.service.status()
        self.assertEqual(self.effects.count("stop"),1)

    def test_rate_blocked_stop_at_deadline_still_restores_a_plus(self):
        self.stale_start()
        self.expire_freshness_deadline()
        self.service.car.blocked_until=time.monotonic()+60
        result=self.service.status()
        self.assertEqual(self.effects,["M+","start","A+"])
        self.assertTrue(result["charging"]["stop_requested"])
        self.assertIn("unbestätigt",result["charging"]["message"])

    def test_offline_vehicle_at_deadline_still_ends_mplus(self):
        self.stale_start()
        self.expire_freshness_deadline()
        self.service.cache["skoda"]["available"]=False
        result=self.service.status()
        self.assertEqual(self.effects,["M+","start","A+"])
        self.assertTrue(result["charging"]["stop_requested"])
        self.assertTrue(result["charging"]["auto_restored"])

    def test_failed_a_plus_restoration_is_retried_without_repeating_stop(self):
        self.stale_start()
        self.expire_freshness_deadline()
        self.web.allow_control=False
        result=self.service.status()
        self.assertFalse(result["charging"]["auto_restored"])
        self.assertIn("Rückkehr zu A+ steht aus",result["charging"]["message"])
        self.web.allow_control=True
        self.service.charging.next_attempt=0
        result=self.service.status()
        self.assertTrue(result["charging"]["auto_restored"])
        self.assertEqual(self.effects.count("stop"),1)
        self.assertEqual(self.web.mode,"A+")

    def test_uncertain_stale_start_keeps_deadline_after_rollback(self):
        self.post_code=503
        result=self.stale_start()
        self.assertEqual(result["status"],"paused")
        self.assertTrue(result["awaiting_fresh_soc"])
        self.assertEqual(self.effects,["M+","start","A+"])
        self.expire_freshness_deadline()
        self.service.status()
        self.assertTrue(self.service.charging.value["stop_requested"])
        self.assertEqual(self.effects.count("start"),1)

    def test_restart_preserves_expired_deadline_without_replaying_start(self):
        self.stale_start()
        self.expire_freshness_deadline()
        self.service.charging=ChargingSession(self.service,self.web)
        self.assertTrue(self.service.charging.value["awaiting_fresh_soc"])
        self.service.status()
        self.assertEqual(self.effects,["M+","start","stop","A+"])

    def test_stale_noncharging_report_never_confirms_stop(self):
        self.stale_start()
        self.expire_freshness_deadline()
        self.service.status()
        self.service.cache["skoda"].update(state="READY_FOR_CHARGING",stale=True,
                                          captured_at=(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat())
        self.service.charging.next_attempt=0
        self.service.charging.tick()
        self.assertEqual(self.service.charging.value["status"],"stop_pending")

    def test_new_stale_session_after_previous_completed_stop_is_allowed(self):
        self.force(80)
        self.update_soc(80)
        self.service.status()
        self.update_soc(80,"READY_FOR_CHARGING")
        self.service.status()
        self.stale_soc()
        self.assertTrue(self.service.charging.can_start_with_stale())
        self.service.force_charge(90,"new-stale-session-00001",allow_stale=True)
        self.assertFalse(self.service.charging.value.get("stop_requested",False))

    def test_restart_preserves_monitor_without_repeating_start(self):
        self.force()
        recovered = ChargingSession(self.service, self.web)
        self.assertEqual(recovered.value["status"], "monitoring")
        self.assertEqual(recovered.value["target_percent"], 80)
        self.assertEqual(self.effects.count("start"), 1)

    def test_config_and_key_removal_blocked_during_monitoring(self):
        self.force()
        with self.assertRaises(ValueError): self.service.save_config(self.config.value)
        with self.assertRaises(ValueError): self.service.clear_key()
        self.service.set_key("replacement-test-key", False)
        self.assertEqual(self.service.charging.value["status"], "monitoring")

    def test_idle_a_is_normalized_to_a_plus(self):
        self.web.mode = "A"
        self.service.next_due["smartfox"] = 0
        self.service.charging.next_attempt = 0
        self.service.status()
        self.assertEqual(self.effects, ["A+"])

    def test_normalization_never_writes_without_opt_in(self):
        self.web.mode, self.web.allow_control = "A", False
        self.service.next_due["smartfox"] = 0
        self.service.charging.next_attempt = 0
        result = self.service.status()
        self.assertTrue(result["charging"]["normalization_pending"])
        self.assertEqual(self.effects, [])

    def test_fin_bound_session_never_controls_another_car(self):
        self.force()
        self.config.value["skoda"]["vin"] = "TMBJB9NY5RF999998"
        self.service.charging.next_attempt = 0
        self.service.charging.tick()
        self.assertEqual(self.service.charging.value["status"], "error")
        self.assertEqual(self.effects, ["M+", "start"])

    def test_interrupted_start_returns_to_a_plus_without_restarting(self):
        self.web.mode = "M+"
        self.service.charging.save(status="starting", target_percent=80, vin=VIN)
        self.service.charging = ChargingSession(self.service, self.web)
        self.update_soc(40, "READY_FOR_CHARGING")
        self.service.status()
        self.assertEqual(self.effects, ["A+"])
        self.assertEqual(self.service.charging.value["status"], "error")

    def test_uncertain_stop_needs_new_charging_telemetry_before_retry(self):
        self.force()
        self.post_code = 503
        with self.assertRaises(IntegrationError): self.service.stop_charge("uncertain-stop-id-0001")
        self.service.charging.next_attempt = 0
        self.service.charging.tick()
        self.assertEqual(self.effects.count("stop"), 1)
        self.post_code = 202
        self.update_soc(41, "CHARGING")
        self.service.status()
        self.assertEqual(self.effects.count("stop"), 2)
