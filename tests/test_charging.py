import copy
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from citifoxcharger.charging import ChargingSession
from citifoxcharger.config import ConfigStore, DEFAULT
from citifoxcharger.secrets_store import SecretStore
from citifoxcharger.service import Service
from citifoxcharger.skoda import IntegrationError, SkodaAdapter
from citifoxcharger.smartfox_web import SmartfoxWeb, SmartfoxControlError
from citifoxcharger.instance import InstanceLock
from citifoxcharger.power import PowerGuard
from test_power_background import FakePower

VIN = "TMBJB9NY5RF999999"


def xml(mode=2, firmware="EM3  00.01.16.08", power="2.93 <span>kW</span>", state=2, info=8, current=11.5, ttl=30, phase=1, phase_seconds=""):
    return (f'<values><value id="version">{firmware}</value><value id="hidCcMode1">{mode}</value>'
            f'<value id="cc1Power"><![CDATA[{power}]]></value><value id="hidCcMplusMode">[1,0,0,0,0]</value>'
            f'<value id="hidCcSt1">{state}</value><value id="hidCc1Info">{info}</value>'
            f'<value id="hidCcControlCurrent">[{current},0,0,0,0]</value><value id="hidCcConnTimeout1">{ttl}</value>'
            f'<value id="hidCc1Phase">{phase}</value><value id="hidCc1TempTime">{phase_seconds}</value></values>').encode()


class SmartfoxWebTests(unittest.TestCase):
    def setUp(self):
        self.config = copy.deepcopy(DEFAULT["smartfox"])

    def test_read_mode_and_physical_power_from_vendor_display(self):
        result = SmartfoxWeb(lambda *args: xml()).read(self.config)
        self.assertEqual(result["mode"], "A+")
        self.assertEqual(result["power_kw"], 2.93)
        self.assertFalse(result["control_available"])

    def test_phase_switching_is_verified_info_six_not_ready_even_with_current(self):
        result=SmartfoxWeb(lambda *args:xml(4,info=6,current=16,phase=3,phase_seconds=120)).read(self.config)
        self.assertTrue(result['phase_switching'])
        self.assertFalse(result['ready_for_start'])
        self.assertEqual(result['phase_switch_seconds'],120)
        self.assertEqual(result['phase_count'],3)

    def test_phase_timer_validation_and_unrecognized_phase_count(self):
        for timer in ('','-1','NaN','12.5','99999','3601'):
            with self.subTest(timer=timer):
                result=SmartfoxWeb(lambda *args:xml(4,info=6,phase=2,phase_seconds=timer)).read(self.config)
                self.assertIsNone(result['phase_switch_seconds'])
                self.assertIsNone(result['phase_count'])
                self.assertTrue(result['phase_switching'])

    def test_old_connection_or_other_info_does_not_report_phase_switching(self):
        for body in (xml(4,info=6,ttl=2,phase_seconds=120),xml(4,info=8,phase_seconds=120)):
            with self.subTest(body=body):
                result=SmartfoxWeb(lambda *args:body).read(self.config)
                self.assertFalse(result['phase_switching'])
                self.assertIsNone(result['phase_switch_seconds'])

    def test_phase_switching_returns_to_the_backend_without_blocking_wait(self):
        from unittest.mock import Mock
        sleep=Mock()
        result=SmartfoxWeb(lambda *args:xml(4,info=6),True,lambda:0,sleep).wait_ready(self.config)
        self.assertTrue(result['phase_switching'])
        sleep.assert_not_called()

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
        self.fail_heater = False
        self.phase_switching,self.phase_seconds=False,120
    def read(self, config):
        return {"available": True, "mode": self.mode, "control_available": self.allow_control,
                "control_reason": None if self.allow_control else "Nicht freigegeben",
                "status_code":2, "telemetry_available":True,"power_kw":0.0,"read_at":datetime.now(timezone.utc).isoformat(),
                "phase_switching":self.phase_switching,"phase_switch_seconds":self.phase_seconds if self.phase_switching else None,
                "phase_count":1 if self.phase_switching else 3,"ready_for_start":not self.phase_switching,
                "info_code":6 if self.phase_switching else 8}
    def set_mode(self, config, mode):
        if self.mode != mode:
            if not self.allow_control or (mode == "M+" and self.fail_mplus):
                raise SmartfoxControlError('Mode change blocked')
            self.effects.append(mode)
            self.mode = mode
        return self.read(config)
    def wait_ready(self, config):
        self.ready_calls += 1
        if self.fail_ready:
            raise SmartfoxControlError("Ready to chargeschaft fehlt")
        return self.read(config)
    def set_charger_mode(self,config,mode):
        return self.set_mode(config,mode)
    def set_heater_mode(self,config,device,mode):
        self.effects.append(device+"-"+mode)
        if self.fail_heater: raise SmartfoxControlError('Heater mode unconfirmed')
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

    def phase_start(self):
        self.web.phase_switching=True
        return self.force()

    def phase_tick(self):
        self.service.next_due['smartfox']=0
        self.service.charging.next_attempt=0
        return self.service.status()

    def phase_ready(self):
        self.web.phase_switching=False
        self.phase_tick()
        self.service.charging.save(phase_ready_since_epoch=time.time()-4)
        return self.phase_tick()

    def test_phase_start_returns_pending_without_a_vehicle_command_and_is_idempotent(self):
        result=self.phase_start()
        self.assertEqual(result['status'],'starting')
        self.assertTrue(result['phase_start_pending'])
        self.assertTrue(result['can_stop'])
        self.assertIn('120 s',result['message'])
        self.force()
        self.assertEqual(self.effects,['M+'])

    def test_phase_start_waits_for_a_new_stable_ready_report_then_starts_once(self):
        self.phase_start();self.web.phase_switching=False
        self.phase_tick()
        self.service.charging.next_attempt=0;self.service.status()
        self.assertNotIn('start',self.effects)
        self.service.charging.save(phase_ready_since_epoch=time.time()-4)
        result=self.phase_tick()
        self.assertEqual(result['charging']['status'],'monitoring')
        self.assertFalse(result['charging']['phase_start_pending'])
        self.phase_tick();self.phase_tick()
        self.assertEqual(self.effects.count('start'),1)

    def test_phase_wait_can_be_stopped_and_never_resumes_a_start(self):
        self.phase_start()
        self.service.stop_charge('stop-during-phase-0001')
        self.web.phase_switching=False
        self.phase_tick()
        self.assertNotIn('start',self.effects)
        self.assertEqual(self.web.mode,'OFF')
        self.assertFalse(self.service.charging.value['phase_start_pending'])

    def test_phase_wait_is_not_replayed_after_backend_restart(self):
        self.phase_start()
        self.service.charging=ChargingSession(self.service,self.web)
        self.phase_tick()
        self.assertFalse(self.service.charging.value['phase_start_pending'])
        self.assertNotIn('start',self.effects)

    def test_phase_wait_has_a_fixed_deadline_and_secures_off_on_timeout(self):
        self.phase_start()
        self.service.charging.save(phase_start_deadline_epoch=time.time()-1)
        result=self.phase_tick()
        self.assertEqual(self.web.mode,'OFF')
        self.assertEqual(result['charging']['stop_cause'],'phase_wait')
        self.assertNotIn('start',self.effects)

    def test_target_reached_while_waiting_cancels_start(self):
        self.phase_start();self.update_soc(85)
        self.phase_tick()
        self.assertEqual(self.web.mode,'OFF')
        self.assertNotIn('start',self.effects)

    def test_lost_vehicle_access_while_waiting_cancels_start(self):
        self.phase_start()
        self.service.cache['skoda'].update(available=False,operations=[])
        self.phase_tick()
        self.assertEqual(self.web.mode,'OFF')
        self.assertNotIn('start',self.effects)

    def test_lost_mplus_during_wait_cancels_start(self):
        self.phase_start();self.web.mode='A+'
        self.phase_tick()
        self.assertEqual(self.web.mode,'OFF')
        self.assertNotIn('start',self.effects)

    def test_old_local_telemetry_cannot_finish_phase_preparation(self):
        self.phase_start()
        self.service.next_due['smartfox']=time.monotonic()+300
        self.service.cache['smartfox']['charger'].update(phase_switching=False,ready_for_start=True,
            read_at=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat())
        self.service.charging.next_attempt=0
        result=self.service.status()
        self.assertTrue(result['charging']['phase_start_pending'])
        self.assertIn('fresh Smartfox',result['charging']['message'])
        self.assertNotIn('start',self.effects)

    def test_uncertain_phase_start_does_not_replay_the_vehicle_command(self):
        self.phase_start();self.post_code=503
        self.phase_ready();self.phase_tick();self.phase_tick()
        self.assertEqual(self.effects.count('start'),1)
        self.assertEqual(self.web.mode,'OFF')
        self.assertFalse(self.service.charging.value['phase_start_pending'])

    def update_soc(self, soc, state="CHARGING"):
        charging = self.data["vehicle"]["charging"]
        charging["carCapturedTimestamp"] = datetime.now(timezone.utc).isoformat()
        charging["status"]["state"] = state
        charging["status"]["battery"]["stateOfChargeInPercent"] = soc
        self.service.next_due["skoda"] = 0
        self.service.charging.next_attempt = 0
        self.service.command_after = 0

    def test_no_device_effects_or_new_reads_after_restart_intent(self):
        self.force()
        before=copy.deepcopy(self.service.charging.value)
        self.effects.clear()
        self.service.stopping=True
        for action in (lambda:self.force(),lambda:self.service.stop_charge('stop-during-restart-001'),
                       lambda:self.service.command('start','start-during-restart-001'),self.service.refresh_now):
            with self.assertRaises(IntegrationError):action()
        self.assertEqual(self.effects,[])
        self.assertEqual(self.service.charging.value,before)

    def test_new_target_report_stops_without_waiting_for_housekeeping_tick(self):
        self.force(82)
        self.update_soc(82)
        self.service.charging.next_attempt=time.monotonic()+10
        result=self.service.status()
        self.assertEqual(self.effects,['M+','start','OFF','stop'])
        self.assertEqual(result['charging']['stop_cause'],'target')
        self.assertEqual(result['charging']['stop_trigger_soc'],82)
        self.assertEqual(result['charging']['stop_trigger_captured_at'],result['skoda']['captured_at'])
        self.assertEqual(result['charging']['stop_trigger_read_at'],result['skoda']['read_at'])

    def test_known_target_secures_off_before_a_new_cloud_read(self):
        self.force(82)
        self.service.cache['skoda']['soc']=82
        self.service.charging.next_attempt=time.monotonic()+10
        self.service.next_due['skoda']=0
        original=self.service.car.request
        def read_after_off(method,path,key):
            if method=='GET':
                self.assertEqual(self.web.mode,'OFF')
            return original(method,path,key)
        self.service.car.request=read_after_off
        self.service.status()
        self.assertEqual(self.effects,['M+','start','OFF','stop'])

    def test_target_fast_path_has_no_effect_after_restart_intent(self):
        self.force(82)
        self.service.cache['skoda']['soc']=82
        self.service.stopping=True
        self.assertFalse(self.service.charging.stop_if_target_reached())
        self.assertEqual(self.effects,['M+','start'])

    def stopped_without_cloud_confirmation(self):
        self.force()
        self.service.command_after=0
        self.service.stop_charge('stop-before-quit-00001')
        self.service.charging.save(stop_intent_epoch=time.time()-10)

    def test_shutdown_after_zero_power_stop_does_not_wait_forever_for_cloud_confirmation(self):
        self.stopped_without_cloud_confirmation()
        self.assertEqual(self.service.charging.value['status'],'stop_pending')
        self.assertTrue(self.service.charging.state()['can_shutdown'])
        before=list(self.effects)
        self.service.prepare_shutdown()
        self.assertEqual(self.effects,before)
        self.assertTrue(self.service.stopping)
        self.assertEqual(self.service.charging.value['status'],'closed')
        self.assertFalse(self.service.charging.value['vehicle_stop_confirmed'])
        self.assertEqual(self.service.last_command['status'],'accepted')
        recovered=ChargingSession(self.service,self.web)
        self.assertFalse(recovered.active)
        self.assertEqual(recovered.value['target_percent'],80)

    def test_shutdown_still_rejects_charge_mplus_errors_and_old_local_telemetry(self):
        self.stopped_without_cloud_confirmation()
        from unittest.mock import patch
        normal=self.web.read(self.config.value['smartfox'])
        for change in ({'mode':'M+'},{'power_kw':.2},{'status_code':3},{'status_code':4},
                       {'telemetry_available':False},{'available':False},{'power_kw':float('nan')},
                       {'read_at':(datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat()}):
            with self.subTest(change=change),patch.object(self.web,'read',return_value={**normal,**change}):
                with self.assertRaises(ValueError):self.service.prepare_shutdown()
                self.assertFalse(self.service.stopping)

    def test_shutdown_requires_stop_intent_and_a_short_settling_period(self):
        self.force()
        self.assertFalse(self.service.charging.state()['can_shutdown'])
        with self.assertRaises(ValueError):self.service.prepare_shutdown()
        self.service.command_after=0
        self.service.stop_charge('stop-before-quit-00002')
        with self.assertRaises(ValueError):self.service.prepare_shutdown()
        self.service.charging.save(stop_intent_epoch=time.time()-10,auto_restored=False)
        with self.assertRaises(ValueError):self.service.prepare_shutdown()

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

    def test_82_percent_goal_stops_on_86_even_when_report_is_stale(self):
        self.force(82)
        self.service.cache['skoda'].update(soc=86,stale=True,
            captured_at=(datetime.now(timezone.utc)-timedelta(minutes=30)).isoformat())
        self.service.charging.next_attempt=0
        self.service.charging.tick()
        self.assertEqual(self.effects,['M+','start','OFF','stop'])
        self.assertTrue(self.service.charging.value['stop_guard'])
        self.assertEqual(self.service.charging.value['target_percent'],82)

    def test_82_goal_locally_stops_on_86_with_cloud_unavailable_or_rate_blocked(self):
        for unavailable in (False, True):
            with self.subTest(unavailable=unavailable):
                self.service.charging.save(replace=True,status='monitoring',target_percent=82,vin=VIN)
                self.web.mode='M+'
                self.effects.clear()
                self.service.cache['skoda'].update(soc=86,stale=True,available=not unavailable)
                self.service.car.blocked_until=time.monotonic()+300
                self.service.charging.next_attempt=0
                self.service.charging.tick()
                self.assertEqual(self.effects,['OFF'])
                self.assertTrue(self.service.charging.value['stop_requested'])
                self.assertTrue(self.service.charging.value['auto_restored'])

    def test_local_stop_is_secured_before_even_a_failing_cloud_call(self):
        self.force(82)
        original=self.service.car.request
        def request(method,path,key):
            if method=='POST' and path.endswith('/stop'):
                self.assertEqual(self.web.mode,'OFF')
                raise IntegrationError('network','Ausgang unbekannt',uncertain=True)
            return original(method,path,key)
        self.service.car.request=request
        self.update_soc(82)
        result=self.service.status()
        self.assertEqual(self.web.mode,'OFF')
        self.assertTrue(result['charging']['stop_requested'])
        self.assertTrue(result['charging']['stop_guard'])
        self.assertEqual(self.effects.count('start'),1)

    def test_later_soc_age_expiry_does_not_leave_manual_charging_running(self):
        self.force(82)
        self.service.cache['skoda'].update(soc=75,stale=True,
            captured_at=(datetime.now(timezone.utc)-timedelta(minutes=16)).isoformat())
        self.service.charging.next_attempt=0
        self.service.charging.tick()
        self.assertEqual(self.effects,['M+','start','OFF','stop'])
        self.assertIn('without current vehicle data',self.service.charging.value['stop_reason'])

    def test_configured_long_display_freshness_does_not_extend_charging_blindly(self):
        self.force(82)
        self.config.value['skoda']['stale_seconds']=3600
        self.service.cache['skoda'].update(soc=75,stale=False,
            captured_at=(datetime.now(timezone.utc)-timedelta(minutes=16)).isoformat())
        self.service.charging.next_attempt=0
        self.service.charging.tick()
        self.assertIn('OFF',self.effects)

    def test_temporary_data_loss_recovers_without_stopping_or_restarting(self):
        self.force(82)
        self.service.cache['skoda']['available']=False
        self.service.charging.next_attempt=0
        self.service.charging.tick()
        self.assertEqual(self.service.charging.value['status'],'paused')
        self.service.cache['skoda'].update(available=True,stale=False)
        self.service.charging.next_attempt=0
        self.service.charging.tick()
        self.assertEqual(self.service.charging.value['status'],'monitoring')
        self.assertIsNone(self.service.charging.value['data_missing_since'])
        self.assertEqual(self.effects,['M+','start'])

    def test_missing_data_deadline_survives_restart_and_stops_without_cloud(self):
        self.force(82)
        self.service.cache['skoda'].update(available=False,captured_at=None)
        self.service.charging.next_attempt=0
        self.service.charging.tick()
        self.assertNotIn('OFF',self.effects)
        self.service.charging.save(data_missing_since=time.time()-301)
        self.service.charging=ChargingSession(self.service,self.web)
        self.service.charging.tick()
        self.assertEqual(self.effects,['M+','start','OFF'])
        self.assertTrue(self.service.charging.value['stop_requested'])

    def test_automatic_stop_holds_off_until_newer_vehicle_stop_confirmation(self):
        self.force(82)
        self.update_soc(82)
        self.service.status()
        self.service.charging.next_attempt=0
        self.service.status()
        self.assertEqual(self.web.mode,'OFF')
        with self.assertRaises(IntegrationError):
            self.service.set_charger_mode('A+','release-during-stop-0001')
        self.update_soc(82,'READY_FOR_CHARGING')
        result=self.service.status()
        self.assertEqual(result['charging']['status'],'complete')
        self.assertFalse(result['charging']['stop_guard'])
        self.assertEqual(self.web.mode,'A+')
        self.assertEqual(self.effects,['M+','start','OFF','stop','A+'])

    def test_guard_reasserts_off_after_external_mode_change(self):
        self.force(82)
        self.update_soc(82)
        self.service.status()
        self.web.mode='M+'
        self.service.next_due['smartfox']=0
        self.service.charging.next_attempt=0
        self.service.status()
        self.assertEqual(self.web.mode,'OFF')
        self.assertEqual(self.effects.count('stop'),1)

    def test_locally_closed_guarded_session_keeps_off_after_restart(self):
        self.force(82)
        self.update_soc(82)
        self.service.status()
        self.service.charging.save(stop_intent_epoch=time.time()-10)
        self.service.prepare_shutdown()
        self.assertEqual(self.service.charging.value['status'],'closed')
        self.service.charging=ChargingSession(self.service,self.web)
        self.service.charging.tick()
        self.assertEqual(self.web.mode,'OFF')
        self.assertEqual(self.effects.count('start'),1)

    def test_local_guard_retry_does_not_wait_for_cloud_backoff(self):
        self.force(82)
        self.web.allow_control=False
        self.post_code=503
        self.update_soc(82)
        self.service.status()
        self.assertGreater(self.service.charging.next_attempt,time.monotonic())
        self.assertFalse(self.service.charging.value['auto_restored'])
        self.web.allow_control=True
        self.service.charging.tick()
        self.assertEqual(self.web.mode,'OFF')
        self.assertEqual(self.effects.count('stop'),1)

    def test_future_vehicle_timestamp_does_not_release_guard_early(self):
        self.force(82)
        self.update_soc(82)
        self.service.status()
        self.service.cache['skoda'].update(state='READY_FOR_CHARGING',stale=False,
            captured_at=(datetime.now(timezone.utc)+timedelta(minutes=2)).isoformat())
        self.service.charging.next_attempt=0
        self.service.charging.tick()
        self.assertEqual(self.web.mode,'OFF')
        self.assertEqual(self.service.charging.value['status'],'stop_pending')

    def test_target_stop_restores_a_plus_but_waits_for_new_vehicle_confirmation(self):
        self.force()
        self.update_soc(80)
        result = self.service.status()
        self.assertEqual(self.effects, ["M+", "start", "OFF", "stop"])
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
            self.assertIn("OFF", self.effects)

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
        self.assertEqual(self.effects[-2:], ["OFF", "stop"])
        self.assertTrue(result['stop_guard'])
        self.update_soc(40,'READY_FOR_CHARGING')
        self.service.status()
        self.assertEqual(self.web.mode,'A+')

    def test_unreachable_vehicle_keeps_local_stop_available_and_allows_checked_shutdown(self):
        self.force()
        self.service.cache['skoda'].update(available=False,stale=True,operations=[])
        self.service.next_due['skoda']=time.monotonic()+300
        status=self.service.status()
        self.assertFalse(status['controls']['stop'])
        self.assertTrue(status['charging']['can_stop'])
        with self.assertRaises(IntegrationError):self.service.stop_charge('offline-manual-stop-001')
        self.assertEqual(self.web.mode,'OFF')
        self.assertTrue(self.service.charging.value['stop_requested'])
        self.service.charging.save(stop_intent_epoch=time.time()-10)
        self.service.prepare_shutdown()
        self.assertTrue(self.service.stopping)
        self.assertEqual(self.service.charging.value['status'],'closed')
        self.assertFalse(self.service.charging.value['vehicle_stop_confirmed'])

    def test_manual_stop_secures_off_before_even_a_failing_vehicle_read(self):
        self.force()
        self.service.next_due['skoda']=0
        def offline(*args):
            self.assertEqual(self.web.mode,'OFF')
            raise IntegrationError('network','Simulated offline vehicle')
        self.service.car.read=offline
        with self.assertRaises(IntegrationError):self.service.stop_charge('offline-first-off-0001')
        self.assertEqual(self.effects,['M+','start','OFF'])

    def test_guarded_shutdown_rejects_a_plus_even_at_zero_power(self):
        self.stopped_without_cloud_confirmation()
        self.web.mode='A+'
        with self.assertRaises(ValueError):self.service.prepare_shutdown()
        self.assertFalse(self.service.stopping)

    def test_local_stop_is_disabled_during_backend_shutdown(self):
        self.force()
        self.service.stopping=True
        self.assertFalse(self.service.charging.state()['can_stop'])

    def test_failed_off_never_claims_stopped_or_allows_exit(self):
        from unittest.mock import patch
        self.force()
        self.service.cache['skoda'].update(available=False,operations=[])
        with patch.object(self.web,'set_charger_mode',side_effect=SmartfoxControlError('Simulated local failure')):
            with self.assertRaisesRegex(IntegrationError,'Off is unconfirmed'):
                self.service.stop_charge('offline-both-fail-0001')
        self.assertFalse(self.service.charging.value['auto_restored'])
        with self.assertRaises(ValueError):self.service.prepare_shutdown()

    def test_recovery_command_works_over_http_with_an_unreachable_vehicle(self):
        from citifoxcharger.server import LocalServer
        from citifoxcharger.lifecycle import LocalControl
        self.force()
        self.service.cache['skoda'].update(available=False,stale=True,operations=[])
        self.service.next_due['skoda']=time.monotonic()+300
        server=LocalServer(0,self.service)
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.02},daemon=True)
        thread.start()
        try:
            result=LocalControl(server.server_port).stop_charging()
            self.assertIn('Smartfox Off is confirmed',result)
            self.assertEqual(self.web.mode,'OFF')
            self.assertTrue(self.service.charging.value['stop_requested'])
            self.assertEqual(self.service.charging.value['user_charger_mode'],'OFF')
            self.assertFalse(self.service.stopping)
        finally:
            server.shutdown();server.server_close();thread.join(2)

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

    def test_five_minute_deadline_secures_local_off_before_cloud_stop(self):
        self.stale_start()
        self.service.charging.next_attempt=0
        self.service.status()
        self.assertNotIn("stop",self.effects)
        self.expire_freshness_deadline()
        result = self.service.status()
        self.assertEqual(self.effects,["M+","start","OFF","stop"])
        self.assertEqual(result["charging"]["status"],"stop_pending")
        self.assertFalse(result["charging"]["awaiting_fresh_soc"])
        self.assertIn("Vehicle stop confirmation is pending",result["charging"]["message"])
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
        self.assertEqual(self.effects[-2:],["OFF","stop"])

    def test_pre_start_report_cannot_clear_freshness_deadline(self):
        self.stale_start()
        self.update_soc(45)
        self.data["vehicle"]["charging"]["carCapturedTimestamp"]=(datetime.now(timezone.utc)-timedelta(seconds=60)).isoformat()
        self.service.status()
        self.assertTrue(self.service.charging.value["awaiting_fresh_soc"])
        self.expire_freshness_deadline()
        self.service.status()
        self.assertIn("stop",self.effects)

    def test_stop_rejection_at_deadline_keeps_local_off_without_replay(self):
        self.stale_start()
        self.post_code=503
        self.expire_freshness_deadline()
        result=self.service.status()
        self.assertEqual(self.effects[-2:],["OFF","stop"])
        self.assertEqual(result["charging"]["status"],"paused")
        self.assertTrue(result["charging"]["stop_requested"])
        self.assertFalse(result["charging"]["can_force_with_stale"])
        self.service.charging.next_attempt=0
        self.service.status()
        self.assertEqual(self.effects.count("stop"),1)

    def test_rate_blocked_stop_at_deadline_still_secures_local_off(self):
        self.stale_start()
        self.expire_freshness_deadline()
        self.service.car.blocked_until=time.monotonic()+60
        result=self.service.status()
        self.assertEqual(self.effects,["M+","start","OFF"])
        self.assertTrue(result["charging"]["stop_requested"])
        self.assertIn("Vehicle stop confirmation is pending",result["charging"]["message"])

    def test_offline_vehicle_at_deadline_still_ends_mplus(self):
        self.stale_start()
        self.expire_freshness_deadline()
        self.service.cache["skoda"]["available"]=False
        result=self.service.status()
        self.assertEqual(self.effects,["M+","start","OFF"])
        self.assertTrue(result["charging"]["stop_requested"])
        self.assertTrue(result["charging"]["auto_restored"])

    def test_failed_local_off_is_retried_without_repeating_stop(self):
        self.stale_start()
        self.expire_freshness_deadline()
        self.web.allow_control=False
        result=self.service.status()
        self.assertFalse(result["charging"]["auto_restored"])
        self.assertIn("Wallbox Off is unconfirmed",result["charging"]["message"])
        self.web.allow_control=True
        self.service.charging.next_attempt=0
        result=self.service.status()
        self.assertTrue(result["charging"]["auto_restored"])
        self.assertEqual(self.effects.count("stop"),1)
        self.assertEqual(self.web.mode,"OFF")

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
        self.assertEqual(self.effects,["M+","start","OFF","stop"])

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

    def test_target_can_be_raised_during_charging_without_another_start(self):
        self.force(80)
        result=self.service.change_target(95)
        self.assertEqual(result["target_percent"],95)
        self.assertTrue(result["can_adjust_target"])
        self.assertEqual(self.effects,["M+","start"])
        self.assertEqual(ChargingSession(self.service,self.web).value["target_percent"],95)

    def test_lower_target_reached_by_fresh_soc_requests_stop(self):
        self.force(90)
        result=self.service.change_target(35)
        self.assertEqual(result["status"],"stop_pending")
        self.assertEqual(self.effects,["M+","start","OFF","stop"])
        self.assertFalse(result["can_adjust_target"])

    def test_lower_target_reached_by_old_soc_stops_without_extending_deadline(self):
        self.stale_start()
        deadline=self.service.charging.value["fresh_soc_deadline"]
        result=self.service.change_target(35)
        self.assertEqual(result["fresh_soc_deadline"],deadline)
        self.assertEqual(self.effects,["M+","start","OFF","stop"])
        self.update_soc(41)
        self.service.status()
        self.assertEqual(self.effects[-2:],["OFF","stop"])

    def test_target_validation_and_stopping_session_cannot_be_reopened(self):
        with self.assertRaises(IntegrationError): self.service.change_target(90)
        self.force(90)
        for value in (True,0,101,"95"):
            with self.subTest(value=value),self.assertRaises(ValueError): self.service.change_target(value)
        self.service.stop_charge("manual-stop-target-0001")
        with self.assertRaises(IntegrationError): self.service.change_target(95)
        self.assertEqual(self.effects.count("start"),1)

    def test_restart_requires_persisted_key_and_resumes_same_target_without_start(self):
        self.stale_start()
        with self.assertRaises(ValueError): self.service.prepare_restart()
        self.secrets.set("test-key-for-local-tests",persist=True)
        deadline=self.service.charging.value["fresh_soc_deadline"]
        self.service.car.quota_remaining="1"
        self.service.car.quota_reset_at=self.service.car.clock()+900
        self.service.car.blocked_until=self.service.car.clock()+30
        self.service.prepare_restart()
        self.assertTrue(self.service.stopping)
        self.service.next_due["skoda"]=0
        def unexpected_read(*args): raise AssertionError("No reads while shutting down")
        self.service.car.read=unexpected_read
        self.service.status()
        recovered=Service(self.config,self.secrets,self.service.sf,SkodaAdapter(),self.web,PowerGuard(FakePower()))
        self.addCleanup(recovered.stop_worker)
        self.assertEqual(recovered.charging.value["target_percent"],90)
        self.assertEqual(recovered.charging.value["fresh_soc_deadline"],deadline)
        self.assertEqual(recovered.car.quota_remaining,"1")
        self.assertGreater(recovered.car.blocked_until,recovered.car.clock())
        self.assertGreater(recovered.car.quota_reset_at,recovered.car.clock()+890)
        self.assertEqual(recovered.cache["skoda"]["captured_at"],self.service.cache["skoda"]["captured_at"])
        self.assertTrue(recovered.cache["skoda"]["stale"])
        recovered.car.blocked_until=0
        result=recovered.status()
        self.assertEqual(result["skoda"]["soc"],40)
        self.assertTrue(result["controls"]["stop"])
        self.assertEqual(self.effects,["M+","start"])

    def test_config_and_key_removal_blocked_during_monitoring(self):
        self.force()
        with self.assertRaises(ValueError): self.service.save_config(self.config.value)
        with self.assertRaises(ValueError): self.service.clear_key()
        self.service.set_key("replacement-test-key", False)
        self.assertEqual(self.service.charging.value["status"], "monitoring")

    def test_explicit_charger_off_persists_and_is_not_normalized_to_a_plus(self):
        self.service.set_charger_mode("OFF","charger-off-test-00001")
        self.service.next_due["smartfox"]=0
        self.service.charging.next_attempt=0
        result=self.service.status()
        self.assertEqual(self.web.mode,"OFF")
        self.assertEqual(result["charging"]["user_charger_mode"],"OFF")
        recovered=ChargingSession(self.service,self.web)
        self.assertEqual(recovered.normal_mode,"OFF")
        self.assertEqual(self.effects,["OFF"])

    def test_explicit_off_during_session_survives_target_stop_and_completion(self):
        self.force()
        self.service.set_charger_mode("OFF","charger-off-test-00002")
        self.update_soc(80)
        self.service.status()
        self.update_soc(80,"READY_FOR_CHARGING")
        result=self.service.status()
        self.assertEqual(result["charging"]["status"],"complete")
        self.assertEqual(self.effects,["M+","start","OFF","stop"])
        self.assertEqual(self.web.mode,"OFF")
        self.assertIn("Off",result["charging"]["message"])

    def test_m_plus_requires_monitored_target_and_active_change_never_restarts_car(self):
        with self.assertRaises(IntegrationError):self.service.set_charger_mode("M+","charger-mode-test-0001")
        self.force(82)
        self.service.command_after=0
        self.service.set_charger_mode("A+","charger-mode-test-0002")
        self.service.set_charger_mode("M+","charger-mode-test-0003")
        self.service.set_charger_mode("M+","charger-mode-test-0003")
        self.assertEqual(self.effects,["M+","start","A+","M+"])
        self.assertEqual(self.service.charging.value["target_percent"],82)

    def test_heater_command_is_idempotent_and_does_not_restart_vehicle(self):
        self.force(90)
        result=self.service.set_heater_mode("heater","manual","heater-change-id-00001")
        self.assertEqual(result["status"],"confirmed")
        self.service.set_heater_mode("heater","manual","heater-change-id-00001")
        self.assertEqual(self.effects,["M+","start","heater-manual"])
        self.assertEqual(self.web.mode,"M+")
        with self.assertRaises(ValueError):self.service.set_heater_mode("heater2","manual","heater-change-id-00001")

    def test_uncertain_heater_command_is_not_automatically_repeated(self):
        self.web.fail_heater=True
        with self.assertRaises(IntegrationError):self.service.set_heater_mode("heater2","manual","heater-uncertain-00001")
        result=self.service.set_heater_mode("heater2","manual","heater-uncertain-00001")
        self.assertEqual(result["status"],"unconfirmed")
        self.assertEqual(self.effects,["heater2-manual"])

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

    def test_vin_bound_session_never_controls_another_car(self):
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
