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


def xml(mode=2, firmware="EM3  00.01.16.08", power="2.93 <span>kW</span>"):
    return (f'<values><value id="version">{firmware}</value><value id="hidCcMode1">{mode}</value>'
            f'<value id="cc1Power"><![CDATA[{power}]]></value><value id="hidCcMplusMode">[1,0,0,0,0]</value></values>').encode()


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
    def read(self, config):
        return {"available": True, "mode": self.mode, "control_available": self.allow_control,
                "control_reason": None if self.allow_control else "Nicht freigegeben"}
    def set_mode(self, config, mode):
        if self.mode != mode:
            if not self.allow_control or (mode == "M+" and self.fail_mplus):
                raise SmartfoxControlError("Moduswechsel gesperrt")
            self.effects.append(mode)
            self.mode = mode
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
