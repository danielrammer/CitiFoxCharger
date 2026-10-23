import copy
import json
import time
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import test_charging
from citifoxcharger.service import Service
from citifoxcharger.skoda import IntegrationError, SkodaAdapter, https_request
from citifoxcharger.vehicle_limit import VehicleLimit
from citifoxcharger.diagnostics import report


class VehicleLimitTests(unittest.TestCase):
    def setUp(self):
        self.case=test_charging.ChargingTests();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.service=self.case.service
        self.writes=[];self.put_status=202;self.uncertain=False
        self.charging=self.case.data['vehicle']['charging']
        self.charging['settings']={'targetStateOfChargeInPercent':80}
        self.case.data['vehicle']['operations'].append({'name':'setChargingLimit'})
        self.original=self.service.car.request
        def request(method,path,key,payload=None):
            if method=='PUT':
                self.assertTrue(path.endswith('/charging/limit'))
                self.assertEqual(set(payload),{'targetStateOfChargeInPercent'})
                self.writes.append(payload['targetStateOfChargeInPercent'])
                if self.uncertain:raise IntegrationError('network','Unknown outcome',uncertain=True)
                return self.put_status,{},b''
            return self.original(method,path,key)
        self.service.car.request=request
        self.service.next_due['skoda']=0;self.service.update('skoda')

    def begin(self,target=90):
        return self.service.force_charge_native(target,'native-limit-test-0001')

    def capture(self,target=90,soc=40,state='CHARGING'):
        self.charging['settings']['targetStateOfChargeInPercent']=target
        self.charging['carCapturedTimestamp']=datetime.now(timezone.utc).isoformat()
        self.charging['status']['battery']['stateOfChargeInPercent']=soc
        self.charging['status']['state']=state
        self.service.next_due['skoda']=0;self.service.update('skoda')

    def verified(self):
        self.begin();self.capture();self.service.charging.tick()
        self.assertTrue(self.service.vehicle_limit.protected)
        self.service.cache["smartfox"]["charger"].update(power_kw=7.3,mode="M+")

    def test_accepted_limit_never_starts_before_a_new_matching_vehicle_report(self):
        result=self.begin()
        self.assertEqual(result['vehicle_limit']['phase'],'setting')
        self.assertEqual(self.case.effects,['OFF'])
        self.assertEqual(self.writes,[90])
        self.service.charging.tick()
        self.assertNotIn('start',self.case.effects)
        self.charging['settings']['targetStateOfChargeInPercent']=90
        self.service.next_due['skoda']=0;self.service.update('skoda')
        self.service.charging.tick()  # Same vehicle capture must not confirm the write.
        self.assertNotIn('start',self.case.effects)
        self.capture();self.service.charging.tick()
        self.assertEqual(self.case.effects,['OFF','M+','start'])
        self.service.charging.tick()
        self.assertEqual(self.case.effects.count('start'),1)

    def test_rejected_or_missing_capability_never_sends_start(self):
        self.service.cache['skoda']['charging_settings']['advertises_set_charging_limit']=False
        with self.assertRaises(IntegrationError):self.begin()
        self.assertEqual(self.writes,[]);self.assertEqual(self.case.effects,[])
        self.service.cache['skoda']['charging_settings']['advertises_set_charging_limit']=True
        self.put_status=422
        with self.assertRaises(IntegrationError):self.begin()
        self.assertNotIn('start',self.case.effects)
        self.assertEqual(self.case.web.mode,'A+')
        self.assertEqual(self.service.vehicle_limit.record['phase'],'rejected')

    def test_missing_readback_blocks_before_wallbox_or_vehicle_writes(self):
        self.service.cache['skoda']['charging_settings']['target_percent']=None
        state=self.service.charging.state()
        self.assertFalse(state['can_force_native'])
        self.assertIn('does not return',state['native_limit_reason'])
        with self.assertRaises(IntegrationError) as error:self.begin()
        self.assertEqual(error.exception.code,'limit_unreadable')
        self.assertEqual(self.writes,[])
        self.assertEqual(self.case.effects,[])
        self.assertEqual(self.case.web.mode,'A+')

    def test_normal_manual_start_without_limit_readback_uses_mplus_and_start_without_put(self):
        self.charging['settings'].pop('targetStateOfChargeInPercent')
        self.charging['status']['battery']['stateOfChargeInPercent']=85
        self.service.next_due['skoda']=0;self.service.update('skoda')
        state=self.service.charging.state()
        self.assertTrue(state['can_start_manual'])
        self.assertEqual(state['target_control'],'app')
        result=self.service.start_manual_charging(87,'manual-app-target-0001',True)
        self.assertEqual(self.writes,[])
        self.assertEqual(self.case.effects,['M+','start'])
        self.assertEqual(result['status'],'monitoring')
        self.assertNotIn('vehicle_limit',result)
        self.service.start_manual_charging(87,'manual-app-target-0001',True)
        self.assertEqual(self.case.effects,['M+','start'])
        self.capture(80,87)
        self.service.charging.tick()
        self.assertEqual(self.case.effects[-2:],['OFF','stop'])
        self.assertEqual(self.service.charging.value['stop_cause'],'target')

    def test_manual_dispatch_keeps_native_verification_when_the_limit_is_readable(self):
        result=self.service.start_manual_charging(87,'manual-native-target-0001',True)
        self.assertEqual(self.writes,[87])
        self.assertEqual(self.case.effects,['OFF'])
        self.assertEqual(result['vehicle_limit']['phase'],'setting')
        with self.assertRaises(IntegrationError):self.service.start_manual_charging(87,'manual-pending-target-001',True)
        self.assertNotIn('start',self.case.effects)

    def test_older_soc_automatic_allowance_refreshes_old_api_access_once_without_limit_put(self):
        self.charging['settings'].pop('targetStateOfChargeInPercent')
        self.service.next_due['skoda']=0;self.service.update('skoda')
        self.case.stale_soc(minutes=70)
        self.service.last_success['skoda']-=600
        self.service.next_due['skoda']=time.monotonic()+600
        captured=self.service.cache['skoda']['captured_at']
        self.assertFalse(self.service.can_command('start',True))
        self.assertTrue(self.service.charging.state()['can_start_manual_with_stale'])
        with patch.object(self.service.car,'read',wraps=self.service.car.read) as read:
            result=self.service.start_manual_charging(87,'manual-aged-access-0001',True)
        self.assertEqual(read.call_count,1)
        self.assertEqual(self.writes,[])
        self.assertEqual(self.case.effects,['M+','start'])
        self.assertTrue(result['awaiting_fresh_soc'])
        self.assertEqual(self.service.cache['skoda']['captured_at'],captured)

    def test_known_original_limit_is_preserved_and_not_replaced_with_default(self):
        self.service.cache['skoda']['charging_settings']['target_percent']=100
        self.assertEqual(self.begin()['vehicle_limit']['restore_percent'],100)

    def test_unknown_write_is_observed_without_replay_or_early_start(self):
        self.uncertain=True;self.begin()
        self.service.charging.tick();self.service.charging.tick()
        self.assertEqual(self.writes,[90]);self.assertNotIn('start',self.case.effects)
        self.capture();self.service.charging.tick()
        self.assertEqual(self.case.effects.count('start'),1)
        self.assertEqual(self.writes,[90])

    def test_idempotency_and_invalid_vehicle_targets(self):
        for value in (0,101,110,87.5,True,'90'):
            with self.subTest(value=value),self.assertRaises(ValueError):self.begin(value)
        self.begin();self.begin()
        self.assertEqual(self.writes,[90])
        with self.assertRaises(ValueError):self.begin(100)

    def test_quota_gate_precedes_all_wallbox_and_vehicle_effects(self):
        self.service.car.quota_remaining='7';self.service.car.quota_reset_at=self.service.car.clock()+900
        with self.assertRaises(IntegrationError):self.begin()
        self.assertEqual(self.writes,[]);self.assertEqual(self.case.effects,[])

    def test_verified_limit_survives_cloud_loss_and_quota_exhaustion_without_early_stop(self):
        self.verified()
        self.service.cache['skoda'].update(available=False,stale=True,code='rate_limit',soc=40,
            captured_at=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat())
        self.service.car.quota_remaining='0';self.service.car.blocked_until=self.service.car.clock()+300
        self.service.charging.next_attempt=0;self.service.charging.tick()
        self.assertEqual(self.case.web.mode,'M+')
        self.assertFalse(self.service.charging.value.get('stop_requested'))
        self.assertNotIn('stop',self.case.effects)

    def test_manual_stop_waits_for_stopped_vehicle_then_restores_and_verifies_before_a_plus(self):
        self.verified()
        self.service.stop_charge('native-manual-stop-0001')
        self.assertEqual(self.case.web.mode,'OFF')
        self.assertEqual(self.writes,[90])
        self.capture(90,75,'CHARGING_INTERRUPTED')
        self.service.charging.next_attempt=0;self.service.charging.tick()
        self.service.charging.tick()
        self.assertEqual(self.writes,[90,80])
        self.assertEqual(self.case.web.mode,'OFF')
        self.capture(80,75,'CHARGING_INTERRUPTED');self.service.charging.tick()
        self.assertEqual(self.case.web.mode,'A+')
        self.assertEqual(self.service.charging.value['status'],'complete')

    def test_target_completion_uses_same_restoration_and_off_guard(self):
        self.verified();self.capture(90,90,'CHARGING_INTERRUPTED')
        self.service.charging.tick()
        self.assertEqual(self.service.charging.value['stop_cause'],'target')
        self.assertEqual(self.case.web.mode,'OFF')

    def test_stopping_preparation_cancels_the_start_even_when_limit_later_matches(self):
        self.begin();self.service.stop_charge('native-pending-stop-0001')
        self.capture(90,40,'CHARGING_INTERRUPTED')
        self.service.charging.next_attempt=0;self.service.charging.tick()
        self.assertNotIn('start',self.case.effects)

    def test_restart_during_preflight_restores_without_start_replay(self):
        self.begin()
        self.service.vehicle_limit=VehicleLimit(self.service)
        self.assertEqual(self.service.vehicle_limit.record['phase'],'restore_needed')
        self.service.charging.tick()
        self.assertEqual(self.writes,[90,80])
        self.capture(80);self.service.charging.tick()
        self.assertEqual(self.case.web.mode,'A+')
        self.assertNotIn('start',self.case.effects)
        self.assertEqual(self.service.charging.value['status'],'error')

    def test_no_confirmation_times_out_and_restores_without_start(self):
        self.begin();self.service.vehicle_limit.save(deadline_epoch=time.time()-1)
        self.service.charging.tick();self.service.charging.tick()
        self.assertEqual(self.writes,[90,80])
        self.assertNotIn('start',self.case.effects)

    def aborted(self):
        self.begin(87)
        self.service.vehicle_limit.save(deadline_epoch=time.time()-1)
        self.service.charging.tick()

    def test_aborted_unstarted_preparation_can_explicitly_release_aplus_without_api_writes(self):
        self.aborted()
        self.assertTrue(self.service.charging.state()['can_release_native_guard'])
        writes=list(self.writes)
        with patch.object(self.service.car,'request',side_effect=AssertionError('No vehicle request allowed')):
            state=self.service.vehicle_limit.release_guard('release-aborted-test-001')
            self.service.charging.tick()
            self.service.vehicle_limit.release_guard('release-aborted-test-001')
        self.assertEqual(self.writes,writes)
        self.assertEqual(state['vehicle_limit']['phase'],'released')
        self.assertEqual(state['status'],'closed')
        self.assertFalse(state['stop_guard'])
        self.assertIsNone(state['vehicle_limit'].get('restoration_confirmation'))
        self.assertEqual(self.case.web.mode,'A+')
        self.assertNotIn('start',self.case.effects)
        self.assertFalse(self.service.vehicle_limit.busy)
        from citifoxcharger.charging import ChargingSession
        self.service.charging=ChargingSession(self.service,self.case.web)
        self.service.vehicle_limit=VehicleLimit(self.service)
        self.service.charging.tick()
        self.assertEqual(self.case.web.mode,'A+')
        self.assertIn('unconfirmed',self.service.charging.value['message'])

    def test_release_is_not_available_during_verification_or_after_any_vehicle_start(self):
        self.begin()
        self.assertFalse(self.service.vehicle_limit.can_release_guard())
        with self.assertRaises(IntegrationError):self.service.vehicle_limit.release_guard('release-too-soon-0001')
        self.service.vehicle_limit.save(phase='attention',cancelled=True)
        for changes in ({'last_start_epoch':time.time()},{'started_at':datetime.now(timezone.utc).isoformat()}):
            self.service.charging.save(**{'last_start_epoch':None,'started_at':None,**changes})
            self.assertFalse(self.service.vehicle_limit.can_release_guard())
            with self.assertRaises(IntegrationError):self.service.vehicle_limit.release_guard('release-after-start-001')
        self.service.charging.save(last_start_epoch=None,started_at=None)
        self.service.vehicle_limit.save(retarget=True)
        self.assertFalse(self.service.vehicle_limit.can_release_guard())

    def test_release_requires_fresh_stopped_local_proof_and_verified_aplus(self):
        self.aborted()
        original=self.case.web.read
        for change in ({'power_kw':7.3},{'power_kw':float('nan')},{'power_kw':True},
                       {'read_at':'2000-01-01T00:00:00Z'},{'telemetry_available':False},
                       {'status_code':3},{'mode':'M+'}):
            with patch.object(self.case.web,'read',side_effect=lambda config,c=change:{**original(config),**c}):
                with self.assertRaises(IntegrationError):self.service.vehicle_limit.release_guard('release-bad-proof-001')
            self.assertTrue(self.service.charging.value['stop_guard'])
            self.assertEqual(self.case.web.mode,'OFF')
        self.case.web.allow_control=False
        with self.assertRaises(IntegrationError):self.service.vehicle_limit.release_guard('release-aplus-fail-001')
        self.assertTrue(self.service.charging.value['stop_guard'])
        self.case.web.allow_control=True
        self.service.vehicle_limit.release_guard('release-aplus-fail-001')
        self.assertEqual(self.case.web.mode,'A+')

    def test_target_and_restore_write_evidence_are_separate_and_survive_timeout(self):
        self.begin(87)
        record=self.service.vehicle_limit.record
        self.assertEqual(record['target_write_outcome'],'accepted')
        self.assertEqual(record['target_write_http_status'],202)
        self.service.vehicle_limit.save(deadline_epoch=time.time()-1)
        self.service.charging.tick()
        self.uncertain=True
        self.service.charging.tick()
        record=self.service.vehicle_limit.record
        self.assertEqual(record['target_write_outcome'],'accepted')
        self.assertEqual(record['target_write_http_status'],202)
        self.assertEqual(record['restore_write_outcome'],'unknown')
        self.assertEqual(record['restore_write_code'],'network')
        self.assertIsNone(record['restore_write_http_status'])
        self.assertEqual(record['target_verification'],'unconfirmed')
        diagnostic=report(self.service.diagnostic_snapshot(),True)['vehicle_limit']
        self.assertEqual(diagnostic['target_write_http_status'],202)
        self.assertEqual(diagnostic['restore_write_code'],'network')

    def test_restore_failure_keeps_off_and_requires_explicit_retry(self):
        self.verified();self.service.vehicle_limit.start_restore();self.service.charging.tick()
        self.service.vehicle_limit.save(deadline_epoch=time.time()-1)
        self.service.charging.tick();self.service.charging.tick()
        self.assertEqual(self.writes,[90,80]);self.assertEqual(self.case.web.mode,'OFF')
        self.assertEqual(self.service.vehicle_limit.record['phase'],'attention')
        self.service.vehicle_limit.retry_restore('native-restore-retry-0001')
        self.assertEqual(self.writes,[90,80,80])
        self.service.vehicle_limit.retry_restore('native-restore-retry-0001')
        self.assertEqual(self.writes,[90,80,80])

    def test_restored_vehicle_limit_retries_failed_wallbox_return_without_another_put(self):
        self.verified();self.service.stop_charge('native-return-mode-stop-0001')
        self.capture(90,75,'CHARGING_INTERRUPTED')
        self.service.charging.next_attempt=0;self.service.charging.tick();self.service.charging.tick()
        self.capture(80,75,'CHARGING_INTERRUPTED');self.case.web.allow_control=False
        self.service.charging.tick()
        self.assertEqual(self.service.vehicle_limit.record['phase'],'restored')
        self.assertEqual(self.service.charging.value['status'],'restore_pending')
        self.assertEqual(self.case.web.mode,'OFF')
        self.case.web.allow_control=True;self.service.charging.tick()
        self.assertEqual(self.case.web.mode,'A+')
        self.assertEqual(self.service.charging.value['status'],'complete')
        self.assertEqual(self.writes,[90,80])

    def test_verification_probes_are_spaced_bounded_and_persisted(self):
        self.begin();self.service.vehicle_limit.save(next_probe_epoch=0)
        with patch.object(self.service.car,'read',wraps=self.service.car.read) as read:
            self.service.charging.tick();self.service.charging.tick()
            self.assertEqual(read.call_count,1)
            self.assertGreater(self.service.vehicle_limit.record['next_probe_epoch'],time.time()+59)
            for _ in range(4):
                self.service.vehicle_limit.save(next_probe_epoch=0);self.service.charging.tick()
            self.assertEqual(read.call_count,3)
        self.assertEqual(self.service.vehicle_limit.record['probe_count'],3)

    def test_limit_drift_causes_local_stop_even_when_quota_blocks_cloud(self):
        self.verified();self.capture(100,45)
        self.service.car.blocked_until=self.service.car.clock()+300
        self.service.charging.next_attempt=0;self.service.charging.tick()
        self.assertEqual(self.case.web.mode,'OFF')
        self.assertEqual(self.service.charging.value['stop_cause'],'vehicle_limit_lost')

    def test_retarget_preserves_original_limit_and_verifies_before_resuming(self):
        self.verified();self.service.command_after=0
        result=self.service.change_target(100)
        self.assertEqual(result['vehicle_limit']['restore_percent'],80)
        self.assertEqual(self.writes,[90,100]);self.assertEqual(self.case.web.mode,'OFF')
        self.capture(100);self.service.charging.tick()
        self.assertEqual(self.case.effects.count('start'),2)
        self.assertEqual(self.service.charging.value['target_percent'],100)

    def test_lower_target_already_reached_secures_off_even_without_quota(self):
        self.verified();self.capture(90,85)
        self.service.car.blocked_until=self.service.car.clock()+300
        self.service.change_target(80)
        self.assertEqual(self.case.web.mode,'OFF')
        self.assertEqual(self.service.charging.value['target_percent'],80)

    def test_verified_limit_still_waits_for_phase_switching_before_start(self):
        self.case.web.phase_switching=True
        self.begin();self.capture();self.service.charging.tick()
        self.assertNotIn('start',self.case.effects)
        self.assertTrue(self.service.charging.value['phase_start_pending'])
        self.assertTrue(self.service.vehicle_limit.protected)

    def test_explicit_wallbox_off_cancels_pending_native_start(self):
        self.begin()
        self.service.set_charger_mode('OFF','native-explicit-off-0001')
        self.capture();self.service.charging.tick()
        self.assertNotIn('start',self.case.effects)
        self.assertEqual(self.case.web.mode,'OFF')

    def test_changing_target_while_charger_is_off_does_not_start_the_car(self):
        self.verified();self.service.command_after=0
        self.service.set_charger_mode('OFF','native-explicit-off-0002')
        self.service.change_target(100);self.capture(100);self.service.charging.tick()
        self.assertEqual(self.case.effects.count('start'),1)
        self.assertEqual(self.case.web.mode,'OFF')
        self.assertTrue(self.service.vehicle_limit.protected)

    def test_explicit_retry_cannot_bypass_a_changed_limit(self):
        self.verified();self.capture(100,40,'CHARGING_INTERRUPTED')
        with self.assertRaises(IntegrationError):
            self.service.force_charge_native(90,'native-changed-retry-0001')
        self.assertEqual(self.case.effects.count('start'),1)
        self.assertEqual(self.case.web.mode,'OFF')

    def test_routine_native_reads_reserve_three_calls_and_do_not_stop_charging(self):
        self.verified()
        self.service.car.quota_remaining='6';self.service.car.quota_reset_at=self.service.car.clock()+1200
        self.assertGreaterEqual(self.service.poll_interval('skoda'),399)
        self.service.car.quota_remaining='3';self.service.car.quota_reset_at=self.service.car.clock()+300
        self.service.next_due['skoda']=0;self.service.status()
        self.assertEqual(self.service.cache['skoda']['code'],'quota_reserved')
        self.assertFalse(self.service.charging.value.get('stop_requested'))

    def test_guarded_restore_read_can_use_the_command_reserve(self):
        self.verified();self.service.stop_charge('native-reserve-stop-0001')
        self.capture(90,75,'CHARGING_INTERRUPTED')
        self.service.charging.next_attempt=0;self.service.charging.tick();self.service.charging.tick()
        self.service.car.quota_remaining='2';self.service.car.quota_reset_at=self.service.car.clock()+300
        self.charging['settings']['targetStateOfChargeInPercent']=80
        self.charging['carCapturedTimestamp']=datetime.now(timezone.utc).isoformat()
        self.service.vehicle_limit.save(next_probe_epoch=0)
        self.service.charging.tick();self.service.charging.tick()
        self.assertEqual(self.service.vehicle_limit.record['phase'],'restored')
        self.assertEqual(self.case.web.mode,'A+')

    def test_sanitized_diagnostics_include_limit_proof_and_restore_status(self):
        self.verified()
        data=report(self.service.diagnostic_snapshot(),True)
        self.assertEqual(data['vehicle_limit']['phase'],'verified')
        self.assertEqual(data['vehicle_limit']['restore_percent'],80)
        self.assertNotIn(self.case.config.value['skoda']['vin'],json.dumps(data))

    def test_changed_vin_never_receives_limit_or_stop_commands(self):
        self.verified();self.capture(100,45)
        effects=list(self.case.effects);writes=list(self.writes)
        self.service.config.value['skoda']['vin']='TMBJB9NY5RF888888'
        self.service.charging.next_attempt=0;self.service.charging.tick()
        self.assertEqual(self.case.effects,effects);self.assertEqual(self.writes,writes)
        self.assertEqual(self.service.charging.value['status'],'error')

    def test_missing_persisted_proof_never_disables_cloud_safety_guards(self):
        self.verified();self.service.vehicle_limit.save(verified_at=None)
        self.service.vehicle_limit=VehicleLimit(self.service)
        self.assertFalse(self.service.vehicle_limit.protected)
        self.service.charging.tick()
        self.assertEqual(self.case.web.mode,'OFF')
        self.assertEqual(self.service.vehicle_limit.record['phase'],'attention')

    def test_exact_87_percent_is_sent_without_rounding_and_must_read_back_exactly(self):
        self.case.data['vehicle']['charging']['status']['battery']['stateOfChargeInPercent']=85
        self.service.next_due['skoda']=0;self.service.update('skoda')
        self.begin(87)
        self.assertEqual(self.writes,[87])
        self.capture(90,85);self.service.charging.tick()
        self.assertNotIn('start',self.case.effects)
        self.capture(87,85);self.service.charging.tick()
        self.assertTrue(self.service.vehicle_limit.protected)
        self.assertEqual(self.case.effects,['OFF','M+','start'])
        self.assertEqual(self.service.charging.value['target_percent'],87)

    def test_rejected_87_never_retries_a_rounded_target_or_starts_the_car(self):
        self.put_status=422
        with self.assertRaises(IntegrationError):self.begin(87)
        self.assertEqual(self.writes,[87])
        self.assertNotIn('start',self.case.effects)
        self.assertEqual(self.case.web.mode,'A+')

    def test_manual_restore_confirmation_is_distinct_idempotent_and_never_starts(self):
        self.begin(87);self.service.vehicle_limit.attention()
        result=self.service.vehicle_limit.confirm_restore_manually(80,'user-restored-80-test-0001')
        self.assertEqual(result['vehicle_limit']['phase'],'restored')
        self.assertEqual(result['vehicle_limit']['restoration_confirmation'],'user')
        self.assertEqual(self.case.web.mode,'A+')
        self.assertNotIn('start',self.case.effects)
        self.assertEqual(self.writes,[87])
        self.service.vehicle_limit.confirm_restore_manually(80,'user-restored-80-test-0001')
        self.assertEqual(self.writes,[87])

    def test_manual_restore_requires_the_exact_pending_value_and_zero_local_power(self):
        self.begin(87)
        with self.assertRaises(IntegrationError):
            self.service.vehicle_limit.confirm_restore_manually(80,'manual-restore-blocked-01')
        self.service.vehicle_limit.attention()
        with self.assertRaises(IntegrationError):
            self.service.vehicle_limit.confirm_restore_manually(90,'manual-restore-blocked-02')
        value=self.case.web.read(self.case.config.value['smartfox'])
        with patch.object(self.case.web,'read',return_value={**value,'power_kw':7.3}):
            with self.assertRaises(IntegrationError):
                self.service.vehicle_limit.confirm_restore_manually(80,'manual-restore-blocked-03')
        self.assertEqual(self.service.vehicle_limit.record['phase'],'attention')
        self.assertNotIn('start',self.case.effects)

    def test_an_existing_legacy_session_cannot_offer_an_unverified_native_retry(self):
        self.case.force(90);self.service.command_after=0
        self.service.charging.save(last_start_epoch=time.time()-60)
        self.service.cache['skoda'].update(state='READY_FOR_CHARGING',captured_at=datetime.now(timezone.utc).isoformat())
        state=self.service.charging.state()
        self.assertTrue(state['can_force'])
        self.assertFalse(state['can_force_native'])
        self.assertIn('existing cloud-monitored session',state['native_limit_reason'])

    def test_closed_tablet_session_can_prepare_87_after_the_last_api_read_aged(self):
        self.case.data['vehicle']['charging']['status']['battery']['stateOfChargeInPercent']=85
        self.case.web.mode='OFF'
        self.service.cache['smartfox']['charger']=self.case.web.read(self.case.config.value['smartfox'])
        self.service.charging.save(status='closed',target_percent=90,stop_requested=True,stop_guard=True,
                                  vin=self.case.config.value['skoda']['vin'])
        self.case.stale_soc(minutes=70)
        self.service.last_success['skoda']-=600
        self.service.next_due['skoda']=time.monotonic()+600
        before=self.service.cache['skoda']['read_at']
        self.assertFalse(self.service.can_command('start',allow_stale=True))
        state=self.service.charging.state()
        self.assertFalse(state['can_force_with_stale'])
        self.assertTrue(state['can_force_native_with_stale'])
        self.service.force_charge_native(87,'native-aged-read-test-0001',allow_stale=True)
        self.assertEqual(self.writes,[87])
        self.assertNotIn('start',self.case.effects)
        self.assertEqual(self.service.cache['skoda']['read_at'],before)
        self.capture(87,85);self.service.charging.tick()
        self.assertEqual(self.case.effects,['M+','start'])

    def test_limit_preparation_does_not_relax_actual_start_or_old_capture_guards(self):
        self.case.stale_soc(minutes=70)
        self.service.last_success['skoda']-=600
        self.assertTrue(self.service.can_command('start',allow_stale=True,prepare_limit=True))
        with self.assertRaises(IntegrationError):
            self.service.command('start','direct-old-api-start-0001',allow_stale=True)
        self.assertNotIn('start',self.case.effects)
        self.service.cache['skoda']['captured_at']='2000-01-01T00:00:00Z'
        self.assertFalse(self.service.can_command('start',allow_stale=True,prepare_limit=True))


class NativeTransportTests(unittest.TestCase):
    def test_put_has_json_body_and_exact_allowlisted_path(self):
        class Response:
            code=202;headers={}
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,size):return b''
        with patch('citifoxcharger.skoda.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value=Response()
            https_request('PUT','/api/v1/vehicles/TEST/charging/limit','test-key',{'targetStateOfChargeInPercent':90})
        request=opener.return_value.open.call_args.args[0]
        self.assertEqual(request.method,'PUT')
        self.assertEqual(json.loads(request.data),{'targetStateOfChargeInPercent':90})
        self.assertEqual(request.get_header('Content-type'),'application/json')
