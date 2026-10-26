import copy
import time
import threading
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
import test_charging
from citifoxcharger import charge_energy
from citifoxcharger.charging import ChargingSession
from citifoxcharger.diagnostics import report
from citifoxcharger.skoda import IntegrationError


class EnergyTests(unittest.TestCase):
    def test_integrates_actual_variable_power_and_zero_power_pause(self):
        meter = charge_energy.begin(35, 1000, 1000, 7.2)
        for t in range(1010, 1601, 10):
            charge_energy.observe(meter, t, 7.2)
        self.assertAlmostEqual(meter['total_kwh'], 1.2)
        self.assertAlmostEqual(meter['estimated_soc'], 35+1.2*.9/32.3*100)
        charge_energy.observe(meter, 1610, 0)
        before = meter['total_kwh']
        for t in range(1620, 1711, 10):
            charge_energy.observe(meter, t, 0)
        self.assertEqual(meter['total_kwh'], before)

    def test_delayed_soc_reanchors_at_capture_not_read_time_without_double_counting(self):
        meter = charge_energy.begin(35, 900, 1000, 7.2)
        for t in range(1010, 1201, 10):
            charge_energy.observe(meter, t, 7.2)
        charge_energy.observe(meter, 1210, 7.2, 36, 1105)
        self.assertAlmostEqual(meter['estimated_soc'], 36+7.2*105/3600*.9/32.3*100)
        anchor = meter['anchor_epoch']
        charge_energy.observe(meter, 1220, 7.2, 36, 1105)
        self.assertEqual(meter['anchor_epoch'], anchor)
        charge_energy.observe(meter, 1230, 7.2, 5, 800)
        self.assertEqual(meter['anchor_soc'], 36)

    def test_duplicates_future_capture_and_gaps_are_not_invented(self):
        meter = charge_energy.begin(40, 1000, 1000, 7)
        charge_energy.observe(meter, 1000, 7)
        self.assertEqual(meter['total_kwh'], 0)
        charge_energy.observe(meter, 1010, 7, 99, 99999)
        self.assertEqual(meter['anchor_soc'], 40)
        charge_energy.observe(meter, 1050, 7)
        self.assertTrue(meter['valid'])
        self.assertGreater(meter['upper_soc'], meter['estimated_soc'])
        self.assertEqual(meter['gap_seconds'], 40)


class ManualTests(unittest.TestCase):
    def setUp(self):
        self.case = test_charging.ChargingTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.s = self.case.service
        self.now = time.time()
        self.power = 0
        self.local_state = 2
        self.available = True
        original = self.case.web.read
        def read(config):
            return {**original(config), 'read_at':datetime.fromtimestamp(self.now, timezone.utc).isoformat(),
                    'power_kw':self.power, 'status_code':self.local_state,
                    'ready_for_start':not self.case.web.phase_switching and self.local_state == 2,
                    'available':self.available}
        self.case.web.read = read
        self.clock = patch('time.time', side_effect=lambda:self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.monotonic = patch('time.monotonic', side_effect=lambda:self.now)
        self.monotonic.start()
        self.addCleanup(self.monotonic.stop)
        self.s.car.clock = lambda:self.now
        self.refresh_local()

    def refresh_local(self):
        self.s.cache['smartfox']['charger'] = self.case.web.read(self.s.config.value['smartfox'])

    def tick(self, seconds=10):
        self.now += seconds
        self.refresh_local()
        self.s.charging.tick()

    def start(self, target=51):
        return self.s.start_manual_charging(target, 'manual-energy-request-001', True)

    def test_takeover_running_surplus_without_unnecessary_vehicle_command(self):
        self.power, self.local_state = 1.37, 3
        self.refresh_local()
        car = self.s.cache['skoda']
        car.update(stale=True, captured_at=datetime.fromtimestamp(self.now-3600, timezone.utc).isoformat())
        self.s.next_due['skoda'] = self.now+600
        self.assertTrue(self.s.charging.state()['can_start_manual'])
        result = self.start()
        self.assertEqual(result['manual_phase'], 'charging')
        self.assertEqual(self.case.effects, ['M+'])
        self.assertAlmostEqual(result['energy_estimate']['initial_report_age_seconds'], 3600, places=5)

    def test_start_progress_idempotency_and_confirmed_zero_power_retries(self):
        result = self.start()
        self.assertEqual(result['manual_phase'], 'waiting_power')
        self.assertIn('Start attempted', result['message'])
        self.tick(4)
        self.assertEqual(self.case.effects, ['M+', 'start'])
        self.start()
        for _ in range(19):self.tick()
        self.assertEqual(self.case.effects.count('start'), 2)
        for _ in range(40):self.tick()
        self.assertEqual(self.case.effects.count('start'), 3)
        self.assertEqual(self.s.charging.value['stop_cause'], 'start_timeout')

    def test_phase_pause_resumes_power_without_start_stop_or_a_plus(self):
        self.power = 7.2
        self.start()
        self.case.web.phase_switching = True
        self.power = 0
        for _ in range(20):self.tick()
        self.assertEqual(self.case.web.mode, 'M+')
        self.assertEqual(self.case.effects, ['M+'])
        self.case.web.phase_switching = False
        self.power = 7.2
        self.tick()
        self.assertEqual(self.s.charging.value['manual_phase'], 'charging')
        self.assertEqual(self.case.effects, ['M+'])

    def test_pause_after_long_successful_charge_does_not_reuse_startup_deadline(self):
        self.power=1.37
        self.start(84)
        for _ in range(400):self.tick()
        self.power=0
        self.tick()
        self.assertFalse(self.s.charging.value.get('stop_requested'))
        self.assertEqual(self.case.web.mode,'M+')
        for _ in range(9):self.tick()
        self.assertEqual(self.case.effects.count('start'),1)

    def test_power_flow_resets_retry_budget_for_a_later_interruption(self):
        self.start(84)
        self.tick(4)
        for _ in range(37):self.tick()
        self.assertEqual(self.case.effects.count('start'),3)
        self.power=7.2
        self.tick()
        self.tick()
        self.power=0
        for _ in range(20):self.tick()
        self.assertFalse(self.s.charging.value.get('stop_requested'))
        self.assertEqual(self.case.effects.count('start'),4)

    def test_short_power_spike_does_not_replenish_three_attempt_budget(self):
        self.start(84)
        self.tick(4)
        for _ in range(37):self.tick()
        self.power=7.2
        self.tick()
        self.power=0
        for _ in range(20):self.tick()
        self.assertEqual(self.case.effects.count('start'),3)
        self.assertEqual(self.s.charging.value['stop_cause'],'start_timeout')

    def test_previous_failed_stop_survives_a_new_successful_session_and_restart(self):
        self.power=7.2
        self.start(84)
        self.s.charging.save(status='complete', stop_cause='estimated_target', stop_trigger_soc=80,
                             completed_vehicle_soc=80, vehicle_stop_confirmed=True)
        self.s.charging.value['energy_meter']['estimated_soc']=84.01
        self.s.start_manual_charging(84,'second-manual-attempt-002',True)
        self.s.charging=ChargingSession(self.s,self.case.web)
        result=report(self.s.diagnostic_snapshot(),True)
        self.assertEqual(len(result['previous_sessions']),1)
        previous=result['previous_sessions'][0]
        self.assertEqual(previous['session']['target_percent'],84)
        self.assertEqual(previous['session']['completed_vehicle_soc'],80)
        self.assertEqual(previous['session']['stop_cause'],'estimated_target')
        self.assertEqual(previous['energy_estimate']['estimated_soc'],84.01)
        self.assertNotIn(self.case.config.value['skoda']['vin'],str(result))
        self.assertNotIn('test-key-for-local-tests',str(result))

    def test_session_history_is_bounded_and_resanitizes_nested_input(self):
        self.power=7.2
        self.start(84)
        for i in range(12):
            self.s.charging.save(status='complete')
            self.s.start_manual_charging(84,f'next-manual-attempt-{i}',True)
        self.assertEqual(len(self.s.charging.value['previous_sessions']),10)
        self.s.charging.value['previous_sessions'][0]['session'].update(
            vin='private-vin-marker',key='private-key-marker',previous_sessions=['private-history-marker'])
        result=report(self.s.diagnostic_snapshot(),True)
        self.assertEqual(len(result['previous_sessions']),10)
        for private in ('private-vin-marker','private-key-marker','private-history-marker'):
            self.assertNotIn(private,str(result))

    def test_stale_soc_and_exhausted_quota_do_not_end_power_proven_session(self):
        self.power = 7.2
        self.start(90)
        self.s.cache['skoda'].update(stale=True, available=False, code='rate_limit')
        self.s.car.quota_remaining = '0'
        self.s.car.blocked_until = self.s.car.clock()+3600
        for _ in range(120):self.tick()
        self.assertFalse(self.s.charging.value.get('stop_requested'))
        self.assertEqual(self.case.web.mode, 'M+')
        self.assertGreater(self.s.charging.state()['energy_estimate']['estimated_soc'], 40)

    def test_estimated_target_stops_local_first_even_without_cloud_access(self):
        self.power = 7.2
        self.start(41)
        self.s.car.blocked_until = self.s.car.clock()+3600
        for _ in range(25):
            self.tick()
            if self.s.charging.value.get('stop_requested'):break
        self.assertEqual(self.case.web.mode, 'OFF')
        self.assertEqual(self.s.charging.value['stop_cause'], 'estimated_target')
        self.assertNotIn('A+', self.case.effects)

    def test_new_vehicle_target_report_stops_before_energy_estimate(self):
        self.power = 1
        self.start(51)
        self.s.cache['skoda'].update(soc=51, captured_at=datetime.fromtimestamp(self.now, timezone.utc).isoformat())
        self.tick()
        self.assertEqual(self.s.charging.value['stop_cause'], 'target')
        self.assertEqual(self.case.web.mode, 'OFF')

    def test_mode_schedule_is_corrected_but_explicit_user_a_plus_ends_manual(self):
        self.power = 7
        self.start()
        self.case.web.mode = 'A+'
        self.tick()
        self.assertEqual(self.case.web.mode, 'M+')
        self.s.set_charger_mode('A+', 'explicit-mode-001')
        self.tick()
        self.assertFalse(self.s.charging.active)
        self.assertEqual(self.case.web.mode, 'A+')

    def test_short_data_gap_keeps_charging_and_tracks_uncertainty_separately(self):
        self.power = 7
        self.start()
        self.tick(50)
        self.assertFalse(self.s.charging.value.get('stop_requested'))
        meter=self.s.charging.state()['energy_estimate']
        self.assertGreater(meter['upper_soc'],meter['estimated_soc'])
        self.assertEqual(self.case.web.mode, 'M+')

    def test_unmeasured_energy_may_exceed_target_but_stops_at_plus_five_bound(self):
        self.power = 7
        self.start(41)
        self.tick(750)  # Worst-case estimate above target, but below target +5.
        self.assertFalse(self.s.charging.value.get('stop_requested'))
        self.tick(220)
        self.assertEqual(self.s.charging.value['stop_cause'], 'uncertainty_limit')
        self.assertEqual(self.case.web.mode, 'OFF')

    def test_missing_local_reads_do_not_immediately_stop_and_fresh_read_recovers(self):
        self.power=7
        self.start(60)
        self.available=False
        for _ in range(12):self.tick()
        self.assertFalse(self.s.charging.value.get('stop_requested'))
        self.available=True
        self.tick()
        self.assertEqual(self.case.web.mode,'M+')
        self.assertGreater(self.s.charging.state()['energy_estimate']['uncertain_kwh'],0)

    def test_new_capture_after_gap_resets_accumulated_uncertainty(self):
        self.power=7
        self.start(60)
        self.tick(120)
        self.s.cache['skoda'].update(soc=42,captured_at=datetime.fromtimestamp(self.now,timezone.utc).isoformat())
        self.tick()
        meter=self.s.charging.state()['energy_estimate']
        self.assertAlmostEqual(meter['upper_soc'],meter['estimated_soc'])
        self.assertGreaterEqual(meter['estimated_soc'],42)

    def test_restart_keeps_energy_and_does_not_duplicate_start(self):
        self.power = 7
        self.start()
        self.tick()
        before = self.s.charging.state()['energy_estimate']['total_kwh']
        self.s.charging = ChargingSession(self.s, self.case.web)
        self.tick()
        self.assertGreater(self.s.charging.state()['energy_estimate']['total_kwh'], before)
        self.assertEqual(self.case.effects, ['M+'])

    def test_target_change_and_repeated_button_do_not_reset_meter(self):
        self.power = 7
        self.start()
        self.tick()
        before = self.s.charging.state()['energy_estimate']['total_kwh']
        self.s.start_manual_charging(60, 'manual-change-002', True)
        self.assertEqual(self.s.charging.value['target_percent'], 60)
        self.assertEqual(self.s.charging.state()['energy_estimate']['total_kwh'], before)
        self.s.change_target(39)
        self.assertTrue(self.s.charging.value['stop_requested'])

    def test_pending_stop_disconnect_and_expired_key_block_start(self):
        for change in ('disconnect', 'key', 'stop'):
            with self.subTest(change=change):
                self.local_state = 1 if change == 'disconnect' else 2
                self.s.car.key_expires_at = '2000-01-01T00:00:00Z' if change == 'key' else None
                self.s.charging.value.update(status='paused' if change == 'stop' else 'idle', stop_requested=change == 'stop')
                self.refresh_local()
                self.assertFalse(self.s.charging.state()['can_start_manual'])
                with self.assertRaises(IntegrationError):self.start()

    def test_diagnostics_exposes_estimate_and_reason_but_not_history_or_credentials(self):
        self.power = 7
        self.start()
        result = report(self.s.diagnostic_snapshot(), True)
        self.assertTrue(result['energy_estimate']['valid'])
        self.assertEqual(result['session']['strategy'], 'smartfox_energy')
        self.assertNotIn('history', str(result))
        self.assertNotIn(self.case.config.value['skoda']['vin'], str(result))

    def test_quota_cooldown_keeps_start_clickable_and_queues_without_commands(self):
        self.s.car.quota_remaining = '0'
        self.s.car.blocked_until = self.s.car.clock()+300
        self.s.car.quota_reset_at = self.s.car.clock()+300
        self.assertTrue(self.s.charging.state()['can_start_manual'])
        result = self.start()
        self.assertGreaterEqual(result['start_eta_seconds'], 299)
        self.assertEqual(self.case.effects, ['M+'])

    def test_storage_failure_secures_off_without_waiting_for_a_cloud_call(self):
        self.power = 7
        self.start()
        with patch.object(self.s.charging, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.tick()
        self.assertEqual(self.case.web.mode, 'OFF')
        self.assertTrue(self.s.charging.value['stop_guard'])
        self.assertNotIn('stop', self.case.effects)

    def test_unexpected_monitor_failure_is_not_silently_left_running(self):
        self.power=7
        self.start()
        stopped=threading.Event()
        original=self.case.web.set_charger_mode
        def set_mode(config,mode):
            result=original(config,mode)
            if mode=='OFF':stopped.set()
            return result
        self.case.web.set_charger_mode=set_mode
        with patch.object(self.s.charging.manual,'_tick',side_effect=RuntimeError('unexpected')):
            self.s.start_worker()
            self.assertTrue(stopped.wait(2))
            self.s.stop_worker()
        self.assertEqual(self.case.web.mode,'OFF')


if __name__ == '__main__':unittest.main()
