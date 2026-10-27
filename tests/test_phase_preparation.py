"""Offline phase configuration and ordered-start regression tests; no devices."""
import copy
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from citifoxcharger.charging import ChargingSession
from citifoxcharger.smartfox_web import SmartfoxWeb, SmartfoxControlError
from citifoxcharger.config import DEFAULT
from test_charging import xml
import test_manual_energy as energy_tests


def vendor_form(auto=0, manual=2, maximum=16):
    return (f"var phaseSwitchModeA=JSON.parse('[{auto},0,0,0,0]'),"
            f"phaseSwitchModeM=JSON.parse('[{manual},0,0,0,0]');"
            "{id:'chargerType',register:'42600',value:'9'},"
            f"{{id:'chargerMaxCurrent',register:'42603',value:'{maximum}'}},"
            "{id:'carChargerPhaseSwitchModeA',register:'42628',value:phaseSwitchModeA[0]},"
            "{id:'carChargerPhaseSwitchModeM',register:'42845',value:phaseSwitchModeM[0]}").encode()


class PhaseAdapterTests(unittest.TestCase):
    def setUp(self):
        self.auto, self.manual, self.maximum = 0, 2, 16
        self.paths, self.ignore_write, self.drift = [], False, False
        self.config = copy.deepcopy(DEFAULT['smartfox'])
        def request(host, path, timeout):
            self.paths.append(path)
            if path == '/values.xml':
                return xml(power='0.00 <span>kW</span>')
            if path == '/carChargerModal.shtml':
                return vendor_form(self.auto, self.manual, self.maximum)
            if path.startswith('/setccx.cgi?'):
                values = parse_qs(urlsplit(path).query)
                if not self.ignore_write:
                    self.auto = int(values.get('42628', [self.auto])[0])
                    self.manual = int(values.get('42845', [self.manual])[0])
                if self.drift:
                    self.maximum = 12
                return b'OK'
            self.fail('Unexpected device path')
        self.web = SmartfoxWeb(request, allow_undocumented_control=True)

    def test_read_current_phase_policies_and_verified_current_limit(self):
        self.assertEqual(self.web.read_phase_settings(self.config),
                         {'auto_mode': 0, 'manual_mode': 2, 'max_current_a': 16})

    def test_narrow_write_restoration_and_no_redundant_command(self):
        self.web.set_phase_settings(self.config, auto_mode=2)
        self.web.set_phase_settings(self.config, auto_mode=2)
        self.web.set_phase_settings(self.config, auto_mode=0)
        self.assertEqual([p for p in self.paths if p.startswith('/setccx')],
                         ['/setccx.cgi?42628=2', '/setccx.cgi?42628=0'])
        self.assertEqual((self.auto, self.manual, self.maximum), (0, 2, 16))

    def test_write_requires_opt_in_and_matching_readback(self):
        self.web.allow_control = False
        with self.assertRaises(SmartfoxControlError):
            self.web.set_phase_settings(self.config, auto_mode=2)
        self.assertFalse(any(p.startswith('/setccx') for p in self.paths))
        self.web.allow_control, self.ignore_write = True, True
        with self.assertRaises(SmartfoxControlError):
            self.web.set_phase_settings(self.config, auto_mode=2)

    def test_detects_unrelated_current_limit_change(self):
        self.drift = True
        with self.assertRaises(SmartfoxControlError):
            self.web.set_phase_settings(self.config, auto_mode=2)

    def test_rejects_unknown_form_or_policy_values(self):
        for value in (True, -1, 3, '2'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.web.set_phase_settings(self.config, auto_mode=value)
        original = self.web.request
        self.web.request = lambda h, p, t: (vendor_form().replace(b"register:'42628'", b"register:'99999'")
                                           if p.endswith('.shtml') else original(h, p, t))
        with self.assertRaises(SmartfoxControlError):
            self.web.read_phase_settings(self.config)


class PhaseFlowTests(unittest.TestCase):
    # Reuse the energy-test clock and service, with an explicitly phase-capable adapter.
    refresh_local = energy_tests.ManualTests.refresh_local
    tick = energy_tests.ManualTests.tick
    start = energy_tests.ManualTests.start

    def setUp(self):
        energy_tests.ManualTests.setUp(self)
        self.settings = {'auto_mode': 0, 'manual_mode': 2, 'max_current_a': 16}
        self.reported_phase, self.fail_restore = 1, False
        original = self.case.web.read
        self.case.web.read = lambda cfg: {**original(cfg), 'phase_count': self.reported_phase}
        self.case.web.read_phase_settings = lambda cfg: dict(self.settings)
        def write(cfg, **changes):
            for key, value in changes.items():
                if value is None or self.settings[key] == value:
                    continue
                if self.fail_restore and key == 'auto_mode' and value != 2:
                    raise SmartfoxControlError('Simulated restoration failure')
                self.case.effects.append('phase-'+key+'-'+str(value))
                self.settings[key] = value
            return dict(self.settings)
        self.case.web.set_phase_settings = write
        self.refresh_local()

    def settle(self):
        self.reported_phase = 3
        self.case.web.phase_switching = False
        self.tick()
        self.tick()
        self.tick()

    def test_prepare_three_phases_then_start_then_mplus_and_restore_auto(self):
        self.start()
        self.assertEqual(self.case.effects, ['phase-auto_mode-2'])
        self.assertEqual(self.case.web.mode, 'A+')
        self.case.web.phase_switching = True
        self.tick()
        self.assertIn('120 s remaining', self.s.charging.value['message'])
        self.assertNotIn('start', self.case.effects)
        self.settle()
        self.assertEqual(self.case.effects,
                         ['phase-auto_mode-2', 'start', 'M+', 'phase-auto_mode-0'])
        self.assertEqual(self.settings['auto_mode'], 0)
        self.assertEqual(self.s.charging.value['start_attempts'], 1)
        self.assertGreaterEqual(self.s.charging.value['next_start_epoch'], self.now+179)
        for _ in range(5):
            self.tick()
        self.assertEqual(self.case.effects.count('start'), 1)

    def test_delayed_switch_restarts_stability_window(self):
        self.start()
        self.reported_phase = 3
        self.tick()
        self.tick()
        self.case.web.phase_switching = True
        self.tick()
        self.assertNotIn('start', self.case.effects)
        self.settle()
        self.assertEqual(self.case.effects.count('start'), 1)

    def test_existing_power_does_not_skip_the_explicit_user_start(self):
        self.power, self.local_state = 1.37, 3
        self.start()
        self.settle()
        self.assertEqual(self.case.effects.count('start'), 1)
        self.assertLess(self.case.effects.index('start'), self.case.effects.index('M+'))
        self.assertTrue(self.s.charging.state()['can_adjust_target'])

    def test_original_single_phase_policy_is_restored_exactly(self):
        self.settings['auto_mode'] = 1
        self.start()
        self.settle()
        self.assertEqual(self.settings['auto_mode'], 1)

    def test_original_manual_policy_is_restored_only_after_session_exit(self):
        self.settings['manual_mode'] = 1
        self.start()
        self.settle()
        self.assertEqual(self.settings['manual_mode'], 2)
        self.s.set_charger_mode('A+', 'explicit-normal-mode-001')
        self.tick(20)
        self.assertEqual(self.settings['manual_mode'], 1)
        self.assertFalse(self.s.charging.manual.phases.pending_restore)

    def test_no_confirmed_three_phases_means_no_start_or_mplus(self):
        self.start()
        self.reported_phase = None
        for _ in range(61):
            self.tick()
        self.assertNotIn('start', self.case.effects)
        self.assertNotIn('M+', self.case.effects)
        self.assertEqual(self.settings['auto_mode'], 0)
        self.assertEqual(self.s.charging.value['status'], 'error')

    def test_idle_a_plus_deferred_physical_switch_does_not_deadlock_manual_start(self):
        self.start()
        self.tick()
        self.assertTrue(self.s.charging.value['phase_preparation']['deferred_switch'])
        self.assertEqual(self.case.effects.count('start'), 1)
        self.assertEqual(self.case.web.mode, 'M+')

    def test_rejected_vehicle_start_does_not_release_mplus_or_stop_normal_a_plus(self):
        self.case.post_code = 422
        self.start()
        self.settle()
        self.assertNotIn('M+', self.case.effects)
        self.assertNotIn('OFF', self.case.effects)
        self.assertEqual(self.settings['auto_mode'], 0)
        for _ in range(30):
            self.tick()
        self.assertEqual(self.case.effects.count('start'), 1)

    def test_restart_during_preparation_restores_without_start_or_mplus(self):
        self.start()
        self.s.charging = ChargingSession(self.s, self.case.web)
        self.tick()
        self.assertEqual(self.settings['auto_mode'], 0)
        self.assertNotIn('start', self.case.effects)
        self.assertNotIn('M+', self.case.effects)

    def test_crash_after_accepted_start_never_replays_or_releases_mplus(self):
        class SimulatedCrash(BaseException):
            pass
        original = self.case.web.set_mode
        def write(cfg, mode):
            if mode == 'M+':
                raise SimulatedCrash()
            return original(cfg, mode)
        self.case.web.set_mode = write
        self.start()
        with self.assertRaises(SimulatedCrash):
            self.settle()
        self.s.charging = ChargingSession(self.s, self.case.web)
        for _ in range(25):
            self.tick()
        self.assertEqual(self.case.effects.count('start'), 1)
        self.assertNotIn('M+', self.case.effects)
        self.assertEqual(self.settings['auto_mode'], 0)

    def test_restoration_failure_stays_visible_and_retries_without_restarting_car(self):
        self.fail_restore = True
        self.start()
        self.settle()
        self.assertTrue(self.s.charging.manual.phases.pending_restore)
        self.assertFalse(self.s.charging.state()['can_shutdown'])
        self.fail_restore = False
        self.tick(20)
        self.assertEqual(self.settings['auto_mode'], 0)
        self.assertEqual(self.case.effects.count('start'), 1)

    def test_target_stop_still_wins_during_preparation(self):
        self.start(41)
        self.s.cache['skoda']['soc'] = 41
        self.tick()
        self.assertEqual(self.case.web.mode, 'OFF')
        self.assertNotIn('start', self.case.effects)

    def test_api_cooldown_queues_user_request_without_disabling_manual_start(self):
        self.s.car.blocked_until = self.now+900
        self.assertTrue(self.s.charging.state()['can_start_manual'])
        self.start()
        self.settle()
        for _ in range(70):
            self.tick()
        self.assertNotIn('M+', self.case.effects)
        self.assertNotIn('start', self.case.effects)
        self.assertTrue(self.s.charging.active)
        self.assertIn('Waiting for vehicle command access', self.s.charging.value['message'])
        self.tick(200)
        self.assertEqual(self.case.effects.count('start'), 1)
        self.assertEqual(self.case.web.mode, 'M+')

    def test_user_can_request_start_while_a_plus_is_still_switching(self):
        self.case.web.phase_switching = True
        self.refresh_local()
        self.assertTrue(self.s.charging.state()['can_start_manual'])
        self.start()
        self.assertNotIn('start', self.case.effects)
        self.settle()
        self.assertEqual(self.case.effects.count('start'), 1)

    def test_failed_mplus_readback_keeps_target_monitoring_active(self):
        original = self.case.web.set_mode
        def write(cfg, mode):
            result = original(cfg, mode)
            if mode == 'M+':
                raise SmartfoxControlError('Simulated lost mode response')
            return result
        self.case.web.set_mode = write
        self.start()
        self.settle()
        self.assertTrue(self.s.charging.active)
        self.assertEqual(self.case.effects.count('start'), 1)
        self.power, self.local_state = 7.2, 3
        self.tick()
        self.assertEqual(self.s.charging.value['manual_phase'], 'charging')

    def test_original_manual_policy_restores_while_off_before_normal_operation(self):
        self.settings['manual_mode'] = 1
        self.start()
        self.settle()
        self.s.stop_charge('explicit-stop-phase-001')
        self.tick(20)
        self.assertEqual(self.case.web.mode, 'OFF')
        self.assertEqual(self.settings['manual_mode'], 1)

    def test_foreign_vehicle_identity_never_receives_a_start_or_phase_write(self):
        self.start()
        before = list(self.case.effects)
        self.s.config.value['skoda']['vin'] = 'TMBJB9NY5RF999998'
        self.tick()
        self.assertEqual(self.case.effects, before)
        self.assertFalse(self.s.charging.active)

    def test_unavailable_optional_settings_form_retains_existing_manual_path(self):
        def unavailable(cfg):
            raise SmartfoxControlError('Simulated optional form unavailable')
        self.case.web.read_phase_settings = unavailable
        self.start()
        self.tick(4)
        self.assertEqual(self.case.effects, ['M+', 'start'])

    def test_invalid_saved_phase_record_never_writes_or_replays(self):
        self.start()
        self.s.charging.value['phase_preparation']['original_auto'] = 99
        before = list(self.case.effects)
        self.tick()
        self.assertEqual(self.case.effects, before)
        self.assertFalse(self.s.charging.state()['can_shutdown'])


if __name__ == '__main__':
    unittest.main()
