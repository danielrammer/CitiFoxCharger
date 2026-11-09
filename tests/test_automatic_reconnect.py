"""Automatic cable reconnection uses one monitor and simulated devices only."""
import copy
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
import test_manual_energy as energy_tests
from citifoxcharger.charging import ChargingSession
from citifoxcharger.charging_policy import ChargingPolicy
from citifoxcharger.diagnostics import report

WINDOW = '2026-11-09T22:00'


class AutomaticReconnectTests(unittest.TestCase):
    def setUp(self):
        self.f = energy_tests.ManualTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.s = self.f.s
        window_patch = patch.object(ChargingPolicy, 'window', return_value=WINDOW)
        self.window = window_patch.start()
        self.addCleanup(window_patch.stop)
        self.s.charging_policy.save(only=True, automatic=True, claimed=WINDOW, target=80)
        self.s.charging_policy.busy = True
        try:
            self.f.start(80)
        finally:
            self.s.charging_policy.busy = False
        self.f.tick(4)
        self.f.power = 7.2
        self.f.tick()
        self.f.tick()
        self.assertEqual(self.starts, 1)

    @property
    def starts(self):
        return self.f.case.effects.count('start')

    def unplug(self):
        self.f.power, self.f.local_state = 0, 0
        self.f.tick()

    def reconnect(self):
        self.f.local_state = 2
        for _ in range(5):
            self.f.tick()

    def test_reconnect_resumes_same_session_and_preserves_estimated_energy(self):
        original_id = self.s.charging.value['request_id']
        original_started = self.s.charging.value['started_at']
        self.unplug()
        self.assertEqual(self.s.charging.value['manual_phase'], 'disconnected')
        self.assertTrue(self.s.charging.active)
        self.assertFalse(self.s.charging.value.get('stop_requested'))
        self.assertEqual(self.f.case.web.mode, 'OFF')
        meter = copy.deepcopy(self.s.charging.value['energy_meter'])
        for _ in range(120):
            self.f.tick()
        self.assertEqual(self.starts, 1)
        self.assertEqual(self.s.charging.value['energy_meter']['total_kwh'], meter['total_kwh'])
        self.assertEqual(self.s.charging.value['energy_meter']['estimated_soc'], meter['estimated_soc'])
        self.reconnect()
        self.assertEqual(self.starts, 2)
        self.assertEqual(self.s.charging.value['request_id'], original_id)
        self.assertEqual(self.s.charging.value['started_at'], original_started)
        self.assertEqual(self.s.charging.value['energy_meter']['anchor_epoch'], meter['anchor_epoch'])
        self.assertEqual(self.s.charging.value['energy_meter']['total_kwh'], meter['total_kwh'])
        self.assertEqual(self.s.charging_policy.value['claimed'], WINDOW)
        self.assertEqual(self.s.charging.value['automatic_window'], WINDOW)
        self.assertNotIn('stop', self.f.case.effects)
        self.f.power = 7.2
        for _ in range(4):self.f.tick()
        self.assertEqual(self.starts, 2)
        self.assertGreater(self.s.charging.value['energy_meter']['total_kwh'], meter['total_kwh'])

    def test_manual_stop_suppresses_reconnect_until_the_next_window(self):
        self.unplug()
        self.s.stop_charge('explicit-stop-reconnect-0001')
        self.reconnect()
        self.s.charging_policy.next_attempt = 0
        self.s.charging_policy.tick()
        self.assertEqual(self.starts, 1)
        self.assertTrue(self.s.charging.value.get('stop_requested'))
        self.assertEqual(self.f.case.web.mode, 'OFF')
        self.assertEqual(self.s.charging_policy.value['claimed'], WINDOW)

    def test_explicit_off_ends_the_paused_session_without_restart(self):
        self.unplug()
        self.s.set_charger_mode('OFF', 'explicit-off-reconnect-0001')
        self.reconnect()
        self.s.charging_policy.next_attempt = 0
        self.s.charging_policy.tick()
        self.assertEqual(self.starts, 1)
        self.assertEqual(self.f.case.web.mode, 'OFF')
        self.assertTrue(self.s.charging.value.get('ended_by_user'))

    def test_expired_window_or_disabled_automatic_never_restarts(self):
        self.unplug()
        self.window.return_value = None
        self.reconnect()
        self.assertEqual(self.starts, 1)
        self.assertEqual(self.f.case.web.mode, 'OFF')
        self.s.charging_policy.next_attempt = 0
        self.s.charging_policy.tick()
        self.assertTrue(self.s.charging.value.get('stop_requested'))

    def test_disabling_automatic_while_unplugged_prevents_restart(self):
        self.unplug()
        self.s.charging_policy.save(automatic=False)
        self.reconnect()
        self.assertEqual(self.starts, 1)
        self.assertEqual(self.f.case.web.mode, 'OFF')
        self.s.charging_policy.next_attempt = 0
        self.s.charging_policy.tick()
        self.assertTrue(self.s.charging.value.get('stop_requested'))

    def test_manual_stop_allows_a_new_automatic_session_in_the_next_window(self):
        self.unplug()
        self.s.stop_charge('stop-until-next-window-0001')
        self.f.local_state = 2
        self.f.tick()
        self.s.cache['skoda'].update(state='READY_FOR_CHARGING', stale=False,
            captured_at=datetime.fromtimestamp(self.f.now, timezone.utc).isoformat())
        self.s.charging.tick()
        self.assertFalse(self.s.charging.active)
        self.s.charging_policy.next_attempt = 0
        self.s.charging_policy.tick()
        self.assertEqual(self.starts, 1)
        self.f.tick(40)
        self.window.return_value = '2026-11-10T22:00'
        self.s.charging_policy.next_attempt = 0
        self.s.charging_policy.tick()
        self.f.tick(4)
        self.assertTrue(self.s.charging.active)
        self.assertEqual(self.s.charging.value['automatic_window'], '2026-11-10T22:00')
        self.assertEqual(self.starts, 2)

    def test_estimated_target_during_pause_is_not_discarded_on_reconnect(self):
        self.unplug()
        meter = self.s.charging.value['energy_meter']
        meter.update(anchor_soc=80, estimated_soc=80, upper_soc=80)
        self.reconnect()
        self.assertEqual(self.starts, 1)
        self.assertEqual(self.s.charging.value.get('stop_cause'), 'estimated_target')
        self.assertEqual(self.f.case.web.mode, 'OFF')

    def test_repeated_physical_reconnections_have_distinct_attempts_not_new_monitors(self):
        identity = self.s.charging.value['request_id']
        for expected in (2, 3):
            self.unplug()
            self.reconnect()
            self.assertEqual(self.starts, expected)
            self.assertEqual(self.s.charging.value['request_id'], identity)
            self.f.power = 7.2
            self.f.tick(); self.f.tick()
        self.assertNotIn('stop', self.f.case.effects)

    def test_permission_fault_while_waiting_cancels_automatic_reconnection(self):
        self.unplug()
        original = self.f.case.web.read
        self.f.case.web.read = lambda config: {**original(config), 'info_code': 14}
        self.reconnect()
        self.assertEqual(self.starts, 1)
        self.assertEqual(self.s.charging.value.get('stop_cause'), 'charger_unavailable')
        self.assertEqual(self.f.case.web.mode, 'OFF')

    def test_target_report_while_unplugged_completes_instead_of_restarting(self):
        self.unplug()
        self.s.cache['skoda'].update(soc=80, captured_at=datetime.fromtimestamp(self.f.now, timezone.utc).isoformat())
        self.reconnect()
        self.assertEqual(self.starts, 1)
        self.assertEqual(self.s.charging.value.get('stop_cause'), 'target')
        self.assertEqual(self.f.case.web.mode, 'OFF')

    def test_backend_restart_while_unplugged_keeps_the_same_meter_and_wait(self):
        self.unplug()
        meter = copy.deepcopy(self.s.charging.value['energy_meter'])
        self.s.charging = ChargingSession(self.s, self.f.case.web)
        self.f.tick()
        self.assertEqual(self.starts, 1)
        self.assertEqual(self.s.charging.value['energy_meter']['total_kwh'], meter['total_kwh'])
        self.reconnect()
        self.assertEqual(self.starts, 2)

    def test_duplicate_or_stale_samples_cannot_prove_reconnection(self):
        self.unplug()
        self.f.local_state = 2
        self.f.tick()
        for _ in range(5):self.s.charging.tick()
        self.assertEqual(self.starts, 1)
        self.s.cache['smartfox']['charger']['read_at'] = datetime.fromtimestamp(self.f.now-60, timezone.utc).isoformat()
        self.s.charging.tick()
        self.assertIsNone(self.s.charging.value.get('reconnect_ready_since'))
        self.assertEqual(self.starts, 1)

    def test_wallbox_fault_or_missing_authorization_is_not_a_cable_pause(self):
        self.f.power, self.f.local_state = 0, 4
        self.f.tick()
        self.assertTrue(self.s.charging.value.get('stop_requested'))
        self.assertEqual(self.s.charging.value.get('stop_cause'), 'charger_unavailable')
        self.reconnect()
        self.assertEqual(self.starts, 1)

    def test_external_off_is_not_overridden_by_a_simultaneous_unplug(self):
        self.f.case.web.mode = 'OFF'
        self.unplug()
        self.assertTrue(self.s.charging.value.get('stop_requested'))
        self.assertEqual(self.s.charging.value.get('stop_cause'), 'charger_unavailable')
        self.reconnect()
        self.assertEqual(self.starts, 1)
        self.assertEqual(self.f.case.web.mode, 'OFF')

    def test_long_unknown_measurement_gap_does_not_reset_uncertainty(self):
        self.unplug()
        self.f.tick(7200)
        self.assertEqual(self.s.charging.value.get('stop_cause'), 'uncertainty_limit')
        self.assertEqual(self.starts, 1)
        self.assertEqual(self.f.case.web.mode, 'OFF')

    def test_manual_session_disconnect_retains_existing_stop_behavior(self):
        self.s.charging.save(automatic_window=None)
        self.unplug()
        self.assertTrue(self.s.charging.value.get('stop_requested'))
        self.assertEqual(self.s.charging.value.get('stop_cause'), 'charger_unavailable')
        self.reconnect()
        self.assertEqual(self.starts, 1)

    def test_diagnostics_identifies_disconnected_phase_without_private_identity(self):
        self.unplug()
        value = report(self.s.diagnostic_snapshot(), True)
        self.assertEqual(value['session']['manual_phase'], 'disconnected')
        self.assertNotIn(self.s.config.value['skoda']['vin'], str(value))

    def test_waiting_uses_existing_fresh_off_samples_without_extra_device_polls(self):
        self.unplug()
        with patch.object(self.f.case.web, 'read', wraps=self.f.case.web.read) as read:
            for _ in range(4):self.s.charging.tick()
        read.assert_not_called()
        self.assertEqual(self.starts, 1)


class PhaseAutomaticReconnectTests(unittest.TestCase):
    def setUp(self):
        import test_phase_preparation as phase_tests
        self.f = phase_tests.PhaseFlowTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.s = self.f.s
        window_patch = patch.object(ChargingPolicy, 'window', return_value=WINDOW)
        window_patch.start(); self.addCleanup(window_patch.stop)
        self.s.charging_policy.save(only=True, automatic=True, claimed=WINDOW, target=80)
        self.f.settings['manual_mode'] = 0
        self.s.charging_policy.busy = True
        try:self.f.start(80)
        finally:self.s.charging_policy.busy = False
        self.f.settle()
        self.f.power = 7.2
        self.f.tick(); self.f.tick()
        self.assertEqual(self.f.case.effects.count('start'), 1)

    def test_reconnection_restores_then_repeats_existing_phase_preparation(self):
        self.f.power, self.f.local_state = 0, 0
        self.f.tick()
        self.assertEqual(self.s.charging.value['manual_phase'], 'disconnected')
        for _ in range(3):self.f.tick()
        self.assertFalse(self.s.charging.manual.phases.pending_restore)
        self.assertEqual(self.f.settings['manual_mode'], 0)
        meter = copy.deepcopy(self.s.charging.value['energy_meter'])
        self.f.local_state = 2
        self.f.tick(); self.f.tick()
        self.assertEqual(self.f.case.effects.count('start'), 1)
        self.assertEqual(self.s.charging.value['manual_phase'], 'preparing')
        for _ in range(6):self.f.tick()
        self.assertEqual(self.f.case.effects.count('start'), 2)
        self.assertEqual(self.s.charging.manual.phases.record['original_manual'], 0)
        self.assertEqual(self.s.charging.value['energy_meter']['anchor_epoch'], meter['anchor_epoch'])
        self.assertEqual(self.s.charging.value['energy_meter']['total_kwh'], meter['total_kwh'])
        self.assertEqual(self.f.case.web.mode, 'M+')
