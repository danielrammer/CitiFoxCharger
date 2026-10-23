"""Supervised trials must distinguish external power drops from every app stop."""
import copy
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import test_limit_test
from citifoxcharger.service import Service
from citifoxcharger.skoda import IntegrationError
from citifoxcharger.diagnostics import report


class LimitTrialTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_limit_test.LimitTestTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.s=self.fixture.service;self.case=self.fixture.case;self.power=0
        self.s.cache['skoda']['soc']=35
        self.case.data['vehicle']['charging']['status']['battery']['stateOfChargeInPercent']=35
        original=self.case.web.read
        self.case.web.read=lambda cfg:{**original(cfg),'power_kw':self.power,'offered_current_a':16}

    def start(self,code=202):
        self.fixture.code=code;self.fixture.send(37)
        return self.s.start_limit_trial(37,'supervised-trial-0001',True)

    def ready(self):
        self.s.cache['smartfox']['charger']=self.case.web.read({})
        self.s.limit_trial.tick()
        self.s.limit_trial.save(ready_since=time.time()-4)
        self.s.limit_trial.tick()

    def tick_power(self,power):
        self.power=power;self.s.cache['smartfox']['charger']=self.case.web.read({})
        self.s.limit_trial.tick()

    def prove_power(self):
        self.tick_power(7.3)
        self.s.limit_trial.save(high_since=time.time()-4)
        self.tick_power(7.3)

    def test_explicit_trial_after_500_sends_one_start_without_second_limit_or_readback(self):
        self.start(500);self.ready()
        self.assertEqual(self.case.effects,['OFF','M+','start'])
        self.assertEqual(len(self.fixture.puts),1)
        self.assertEqual(self.s.limit_trial.record['start_outcome'],'accepted')
        self.assertEqual(self.s.limit_test.record['attempts'][-1]['outcome'],'unknown')
        self.s.start_limit_trial(37,'supervised-trial-0001',True)
        self.assertEqual(self.case.effects.count('start'),1)

    def test_trial_requires_explicit_acknowledgement_exact_target_and_small_soc_gap(self):
        self.fixture.send(37)
        with self.assertRaises(ValueError):self.s.start_limit_trial(37,'no-consent-trial-0001',False)
        with self.assertRaises(ValueError):self.s.start_limit_trial(38,'wrong-target-trial-0001',True)
        self.s.cache['skoda']['soc']=55
        with self.assertRaises(IntegrationError):self.s.start_limit_trial(37,'already-above-trial-0001',True)
        self.assertEqual(self.case.effects,['OFF'])

    def test_explicit_rejection_does_not_enable_trial(self):
        self.fixture.code=422;self.fixture.send(37)
        with self.assertRaises(IntegrationError):self.s.start_limit_trial(37,'rejected-limit-trial-0001',True)
        self.assertNotIn('M+',self.case.effects)

    def test_exact_target_does_not_invoke_app_soc_stop(self):
        self.start();self.ready();self.prove_power()
        self.s.cache['skoda'].update(soc=37,captured_at=datetime.now(timezone.utc).isoformat())
        self.s.status()
        self.assertTrue(self.s.limit_trial.active)
        self.assertNotIn('stop',self.case.effects)
        self.assertEqual(self.case.web.mode,'M+')

    def test_new_soc_two_points_above_target_is_labelled_app_fallback(self):
        self.start();self.ready();self.prove_power()
        self.s.cache['skoda'].update(soc=39,captured_at=datetime.now(timezone.utc).isoformat())
        self.tick_power(7.3)
        self.assertEqual(self.s.limit_trial.record['stop_cause'],'overshoot_guard')
        self.assertEqual(self.case.effects[-2:],['OFF','stop'])

    def test_old_high_soc_does_not_trigger_fallback(self):
        self.start();self.ready()
        self.s.cache['skoda'].update(soc=39,captured_at='2000-01-01T00:00:00Z')
        self.tick_power(7.3)
        self.assertNotIn('stop',self.case.effects)

    def test_power_drop_requires_confirmed_charging_and_thirty_seconds_before_app_stop(self):
        self.start();self.ready();self.prove_power()
        self.tick_power(0)
        self.assertNotIn('stop',self.case.effects)
        self.s.limit_trial.save(low_since=time.time()-31)
        self.tick_power(0)
        self.assertEqual(self.s.limit_trial.record['stop_cause'],'external_power_stop')
        self.assertIsNotNone(self.s.limit_trial.record['power_stopped_at'])
        self.assertEqual(self.case.effects[-2:],['OFF','stop'])
        self.assertEqual(self.s.limit_trial.record['phase'],'finished')

    def test_initial_zero_power_is_never_mistaken_for_target_completion(self):
        self.start();self.ready()
        self.s.limit_trial.save(start_at='2000-01-01T00:00:00Z')
        self.tick_power(0)
        self.assertEqual(self.s.limit_trial.record['stop_cause'],'no_charging_power')
        self.assertIsNone(self.s.limit_trial.state()['power_stopped_at'])

    def test_new_trial_uses_exact_sixty_minute_runtime(self):
        from citifoxcharger.skoda import timestamp
        self.start();self.ready()
        r=self.s.limit_trial.record
        self.assertEqual(r['runtime_limit_seconds'],3600)
        self.assertAlmostEqual(r['runtime_deadline']-timestamp(r['start_at']),3600,delta=1)
        self.assertEqual(self.s.limit_trial.state()['runtime_limit_seconds'],3600)

    def test_ten_elapsed_minutes_do_not_stop_a_proven_charging_trial(self):
        self.start();self.ready();self.prove_power()
        start=time.time()-601
        self.s.limit_trial.save(start_at=datetime.fromtimestamp(start,timezone.utc).isoformat(),runtime_deadline=start+3600)
        self.tick_power(4.5)
        self.assertTrue(self.s.limit_trial.active)
        self.assertNotIn('stop',self.case.effects)

    def test_sixty_minute_guard_is_explicit_inconclusive_app_stop(self):
        self.start();self.ready();self.s.limit_trial.save(runtime_deadline=time.time()-1)
        self.tick_power(7.3)
        self.assertEqual(self.s.limit_trial.record['stop_cause'],'time_guard')
        self.assertIsNone(self.s.limit_trial.state()['power_stopped_at'])
        self.assertEqual(self.s.limit_trial.state()['evaluation'],'inconclusive_app_stop')

    def test_historical_ten_minute_report_is_not_relabelled_as_sixty(self):
        from citifoxcharger.skoda import timestamp
        self.start();self.ready()
        record=copy.deepcopy(self.s.limit_test.record)
        record['trial'].pop('runtime_limit_seconds')
        record['trial']['runtime_deadline']=timestamp(record['trial']['start_at'])+600
        self.s.limit_test.persist(record)
        self.assertEqual(self.s.limit_trial.state()['runtime_limit_seconds'],600)

    def test_restore_80_keeps_completed_trial_evidence(self):
        self.start();self.s.stop_charge('preserve-trial-0001')
        self.fixture.send(80,'restore-trial-evidence-0001')
        report_data=report(self.s.diagnostic_snapshot(),True)['limit_trial']
        self.assertEqual(report_data['target_percent'],37)
        self.assertEqual(report_data['phase'],'finished')
        self.assertEqual(report_data['stop_cause'],'manual')
        self.assertEqual(report_data['runtime_limit_seconds'],3600)
        self.assertEqual(report_data['evaluation'],'inconclusive_app_stop')

    def test_phase_switch_waits_then_one_start_only(self):
        self.case.web.phase_switching=True;self.start()
        self.s.cache['smartfox']['charger']=self.case.web.read({});self.s.limit_trial.tick()
        self.assertNotIn('start',self.case.effects)
        self.case.web.phase_switching=False;self.ready();self.ready()
        self.assertEqual(self.case.effects.count('start'),1)

    def test_phase_timeout_never_starts(self):
        self.start();self.s.limit_trial.save(ready_deadline=time.time()-1);self.ready()
        self.assertNotIn('start',self.case.effects)
        self.assertEqual(self.s.limit_trial.record['stop_cause'],'phase_timeout')

    def late_phase(self,prove=True):
        self.start();self.ready()
        if prove:self.prove_power()
        self.case.web.phase_switching=True
        self.tick_power(0)

    def test_late_phase_switch_is_not_mistaken_for_external_power_stop(self):
        self.late_phase()
        self.s.limit_trial.save(low_since=time.time()-31)
        self.tick_power(0)
        self.assertEqual(self.s.limit_trial.record['phase'],'waiting_resume')
        self.assertIsNone(self.s.limit_trial.state()['power_stopped_at'])
        self.assertNotIn('stop',self.case.effects)
        self.assertEqual(self.case.effects.count('start'),1)
        self.assertEqual(self.case.web.mode,'M+')

    def test_late_phase_before_initial_power_does_not_hit_initial_power_timeout(self):
        self.late_phase(prove=False)
        self.s.limit_trial.save(start_at='2000-01-01T00:00:00Z')
        self.tick_power(0)
        self.assertTrue(self.s.limit_trial.active)
        self.assertNotIn('stop',self.case.effects)

    def test_power_resume_after_late_phase_needs_new_proof_without_second_start(self):
        self.late_phase()
        original_deadline=self.s.limit_trial.record['runtime_deadline']
        self.case.web.phase_switching=False;self.tick_power(0)
        self.tick_power(7.3)
        self.s.limit_trial.save(high_since=time.time()-4);self.tick_power(7.3)
        self.assertEqual(self.s.limit_trial.record['phase'],'observing')
        self.assertIsNotNone(self.s.limit_trial.state()['power_resumed_at'])
        self.assertEqual(self.s.limit_trial.record['runtime_deadline'],original_deadline)
        self.assertEqual(self.case.effects.count('start'),1)
        self.assertNotIn('stop',self.case.effects)

    def test_no_resume_is_a_distinct_app_fallback_not_target_completion(self):
        self.late_phase()
        self.case.web.phase_switching=False;self.tick_power(0)
        self.s.limit_trial.save(resume_deadline=time.time()-1);self.tick_power(0)
        self.assertEqual(self.s.limit_trial.record['stop_cause'],'phase_resume_failed')
        self.assertIsNone(self.s.limit_trial.state()['power_stopped_at'])
        self.assertEqual(self.case.effects.count('start'),1)
        self.assertEqual(self.case.effects[-2:],['OFF','stop'])

    def test_late_phase_keeps_original_time_and_overshoot_guards(self):
        self.late_phase()
        self.s.limit_trial.save(runtime_deadline=time.time()-1);self.tick_power(0)
        self.assertEqual(self.s.limit_trial.record['stop_cause'],'time_guard')

    def test_late_phase_still_honours_new_soc_overshoot_guard(self):
        self.late_phase()
        self.s.cache['skoda'].update(soc=39,captured_at=datetime.now(timezone.utc).isoformat())
        self.tick_power(0)
        self.assertEqual(self.s.limit_trial.record['stop_cause'],'overshoot_guard')
        self.assertIsNone(self.s.limit_trial.state()['power_stopped_at'])

    def test_late_phase_has_its_own_bounded_wait(self):
        self.late_phase()
        self.s.limit_trial.save(phase_deadline=time.time()-1);self.tick_power(0)
        self.assertEqual(self.s.limit_trial.record['stop_cause'],'phase_timeout')

    def test_manual_stop_cancels_late_phase_wait(self):
        self.late_phase()
        self.s.stop_charge('stop-late-phase-0001')
        self.assertEqual(self.s.limit_trial.record['stop_cause'],'manual')
        self.assertEqual(self.case.effects.count('start'),1)
        self.assertEqual(self.case.web.mode,'OFF')

    def test_restart_in_resume_wait_never_sends_another_start(self):
        self.late_phase()
        recovered=Service(self.case.config,self.case.secrets,self.s.sf,self.s.car,self.case.web,self.s.power)
        recovered.status()
        self.assertEqual(recovered.limit_trial.record['stop_cause'],'backend_restart')
        self.assertEqual(self.case.effects.count('start'),1)
        self.assertEqual(self.case.web.mode,'OFF')

    def test_missing_telemetry_stops_locally(self):
        self.start();self.ready()
        self.s.cache['smartfox']['charger']['available']=False
        self.s.limit_trial.save(telemetry_missing_since=time.time()-31);self.s.limit_trial.tick()
        self.assertEqual(self.s.limit_trial.record['stop_cause'],'local_data_missing')
        self.assertEqual(self.case.effects[-2:],['OFF','stop'])

    def test_manual_stop_is_available_without_cloud_access(self):
        self.start();self.ready();self.s.car.blocked_until=self.s.car.clock()+60
        self.s.stop_charge('manual-stop-trial-0001')
        self.assertEqual(self.case.web.mode,'OFF')
        self.assertEqual(self.s.limit_trial.record['stop_cause'],'manual')

    def test_vehicle_stop_is_attempted_even_if_local_off_fails(self):
        from citifoxcharger.smartfox_web import SmartfoxControlError
        self.start();self.ready()
        with patch.object(self.case.web,'set_charger_mode',side_effect=SmartfoxControlError('offline')):
            self.s.stop_charge('failed-local-off-trial')
        self.assertEqual(self.case.effects.count('stop'),1)
        self.assertTrue(self.s.limit_trial.active)
        self.s.limit_trial.next_off=0;self.tick_power(0)
        self.assertEqual(self.case.web.mode,'OFF')
        self.assertEqual(self.case.effects.count('stop'),1)

    def test_off_dropdown_cancels_deferred_start_and_never_normalizes_to_auto(self):
        self.start();self.s.set_charger_mode('OFF','cancel-test-mode-0001')
        self.ready();self.s.status()
        self.assertNotIn('start',self.case.effects)
        self.assertEqual(self.case.web.mode,'OFF')

    def test_restart_cancels_trial_and_never_replays_start(self):
        self.start();self.ready()
        recovered=Service(self.case.config,self.case.secrets,self.s.sf,self.s.car,self.case.web,self.s.power)
        recovered.status()
        self.assertEqual(recovered.limit_trial.record['stop_cause'],'backend_restart')
        self.assertEqual(self.case.effects.count('start'),1)
        self.assertEqual(self.case.web.mode,'OFF')

    def test_trial_blocks_end_restore_restart_shutdown_and_regular_start(self):
        self.start()
        for call in (lambda:self.s.end_limit_test(True),lambda:self.fixture.send(80,'trial-restore-blocked'),
                     self.s.prepare_shutdown,self.s.prepare_restart,
                     lambda:self.s.start_manual_charging(37,'regular-start-blocked',True)):
            with self.assertRaises((ValueError,IntegrationError)):call()
        self.assertFalse(self.s.status()['charging']['can_shutdown'])

    def test_no_automatic_limit_restore_or_auto_mode_at_trial_end(self):
        self.start();self.s.stop_charge('end-supervised-trial')
        self.assertEqual(len(self.fixture.puts),1)
        self.assertTrue(self.s.limit_test.active)
        self.assertEqual(self.case.web.mode,'OFF')
        self.assertEqual(self.s.charging.normal_mode,'OFF')

    def test_new_id_cannot_replay_completed_trial_for_same_limit_write(self):
        self.start();self.s.stop_charge('stop-first-trial-0001')
        with self.assertRaises(IntegrationError):self.s.start_limit_trial(37,'try-again-trial-0002',True)
        self.assertNotIn('start',self.case.effects)

    def test_persist_failure_prevents_mplus_and_start(self):
        self.fixture.send(37)
        with patch('citifoxcharger.limit_test.atomic_write',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.s.start_limit_trial(37,'failed-storage-trial',True)
        self.assertEqual(self.case.effects,['OFF'])

    def test_diagnostics_include_trial_evidence_but_no_request_id_or_vin(self):
        self.start();self.ready();self.prove_power()
        diagnostic=report(self.s.diagnostic_snapshot(),True)
        self.assertEqual(diagnostic['limit_trial']['target_percent'],37)
        self.assertIsNotNone(diagnostic['limit_trial']['power_confirmed_at'])
        self.assertNotIn('request_id',diagnostic['limit_trial'])


if __name__=='__main__':unittest.main()
