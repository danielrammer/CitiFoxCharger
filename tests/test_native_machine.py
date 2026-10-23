"""Reported incidents as deterministic state-machine and adapter scenarios."""
import json
import time
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch

import test_charging
from citifoxcharger.native_charging.machine import Session, State, Step, Facts, begin, transition
from citifoxcharger.native_charging.controller import NativeCharging
from citifoxcharger.skoda import IntegrationError
from citifoxcharger.service import Service
from citifoxcharger.diagnostics import report


class MachineTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.s = begin(87, 80, "test-request-0001", "test-vin", self.now)
        self.f = Facts(local_fresh=True, mode="OFF", power=0, local_capture=1000,
                       vehicle_available=True, vehicle_capture=990, soc=85, limit=80, start_allowed=True)

    def event(self, event="tick", seconds=0, **facts):
        self.now += seconds
        self.f = replace(self.f, **facts)
        d = transition(self.s, event, self.f, self.now)
        self.s = d.session
        return d.effect

    def verified(self):
        self.assertEqual(self.event(), "off")
        self.event("confirmed")
        self.assertEqual(self.event(), "set_limit")
        self.event("accepted")
        self.event(seconds=1, limit=87, vehicle_capture=1001)
        self.assertEqual(self.s.step, Step.MANUAL)

    def started(self):
        self.verified()
        self.assertEqual(self.event(), "manual")
        self.event("confirmed")
        self.event(mode="M+", ready=True, local_capture=self.now)
        self.event(seconds=3, local_capture=self.now+3)
        self.assertEqual(self.s.state, State.STARTING)
        self.assertEqual(self.event(), "start")
        self.event("accepted")

    def charging(self):
        self.started()
        self.event(power=7.3, local_capture=self.now)
        self.event(seconds=3, local_capture=self.now+3)
        self.assertEqual(self.s.state, State.CHARGING)

    def test_accepted_write_and_same_capture_do_not_confirm_limit(self):
        self.event();self.event("confirmed");self.event();self.event("accepted")
        self.event(limit=87)
        self.assertEqual(self.s.step,Step.WAIT_LIMIT)
        self.assertEqual(self.s.limit_outcome,"accepted")

    def test_87_is_exact_and_never_rounded_to_90(self):
        self.event();self.event("confirmed");self.event();self.event("accepted")
        self.event(limit=90,vehicle_capture=1001)
        self.assertEqual(self.s.step,Step.WAIT_LIMIT)
        self.event(limit=87,vehicle_capture=1002)
        self.assertEqual(self.s.step,Step.MANUAL)

    def test_phase_reports_never_extend_deadline_or_send_start(self):
        self.verified();self.event();self.event("confirmed")
        deadline=self.s.deadline
        for _ in range(5):
            self.assertIsNone(self.event(seconds=30,switching=True))
            self.assertEqual(self.s.deadline,deadline)
        self.event(seconds=220)
        self.assertEqual(self.s.state,State.STOPPING)
        self.assertEqual(self.s.result_reason,"charger_ready_timeout")

    def test_readiness_needs_distinct_reports(self):
        self.verified();self.event();self.event("confirmed")
        self.event(mode="M+",ready=True,local_capture=self.now)
        self.event(seconds=5)
        self.assertEqual(self.s.step,Step.WAIT_READY)
        self.event(local_capture=self.now)
        self.assertEqual(self.s.step,Step.START)

    def test_accepted_start_waits_for_power_and_never_repeats(self):
        self.started()
        for _ in range(4):self.assertIsNone(self.event(seconds=30))
        self.assertEqual(self.s.state,State.STARTING)
        self.event(seconds=61)
        self.assertEqual(self.s.state,State.STOPPING)
        self.assertEqual(self.s.result_reason,"start_power_timeout")

    def test_power_needs_two_distinct_samples_after_start(self):
        self.started();self.event(power=7.3,local_capture=self.now)
        self.event(seconds=10)
        self.assertEqual(self.s.state,State.STARTING)
        self.event(local_capture=self.now)
        self.assertEqual(self.s.state,State.CHARGING)

    def test_stale_soc_does_not_stop_a_native_protected_charge(self):
        self.charging()
        self.event(seconds=3600,vehicle_available=False,vehicle_capture=990,soc=85,local_capture=self.now+3600)
        self.assertEqual(self.s.state,State.CHARGING)
        self.assertIsNone(self.event())

    def test_fresh_target_requests_stop_not_immediate_completion(self):
        self.charging();self.event(soc=87,vehicle_capture=self.now)
        self.assertEqual(self.s.state,State.STOPPING)
        self.assertEqual(self.s.result,"target_reached")
        self.assertEqual(self.event(),"off")

    def test_native_limit_drift_stops_before_new_start(self):
        self.charging();self.event(limit=100,vehicle_capture=self.now)
        self.assertEqual(self.s.state,State.STOPPING)
        self.assertEqual(self.s.result_reason,"vehicle_limit_changed")

    def test_restart_never_replays_a_pending_start(self):
        self.started();self.event("restart")
        self.assertEqual(self.s.state,State.RECOVERING)
        self.event()
        self.assertEqual(self.s.state,State.STOPPING)
        self.assertEqual(self.event(),"off")

    def test_restart_can_reconfirm_actual_power_without_sending_start(self):
        self.charging();self.event("restart")
        self.assertIsNone(self.event())
        self.assertEqual(self.s.step,Step.WAIT_POWER)
        self.event(local_capture=self.now)
        self.event(seconds=3,local_capture=self.now+3)
        self.assertEqual(self.s.state,State.CHARGING)

    def test_stop_interrupts_phase_wait(self):
        self.verified();self.event();self.event("confirmed");self.event(switching=True)
        self.event("stop")
        self.assertEqual(self.event(),"off")
        self.event("confirmed")
        self.assertEqual(self.event(),"restore")

    def test_unknown_stop_requires_new_vehicle_and_local_off_evidence(self):
        self.charging();self.event("stop");self.event();self.event("confirmed")
        self.assertEqual(self.event(),"stop")
        self.event("unknown")
        self.event(mode="OFF",power=0,vehicle_state="CHARGING_INTERRUPTED",vehicle_capture=990)
        self.assertEqual(self.s.step,Step.WAIT_STOP)
        self.event(vehicle_capture=self.now)
        self.assertEqual(self.s.step,Step.RESTORE)

    def test_failed_restore_stays_attention_without_background_writes(self):
        self.s=replace(self.s,state=State.STOPPING,step=Step.RESTORE)
        self.assertEqual(self.event(),"restore")
        self.event("accepted")
        self.event(seconds=181)
        self.assertEqual(self.s.state,State.ATTENTION)
        for _ in range(5):self.assertIsNone(self.event(seconds=30))

    def test_power_dropout_has_grace_and_late_target_can_finish(self):
        self.charging();self.event(power=0)
        self.event(seconds=20)
        self.assertEqual(self.s.state,State.CHARGING)
        self.event(seconds=26)
        self.assertEqual(self.s.state,State.ATTENTION)
        self.event(soc=87,vehicle_capture=self.now)
        self.assertEqual(self.s.state,State.STOPPING)

    def test_local_off_failure_does_not_suppress_vehicle_stop(self):
        self.charging();self.event('stop');self.event();self.event('unknown')
        self.assertEqual(self.s.step,Step.STOP)
        self.assertEqual(self.event(),'stop')

    def test_restart_observes_unknown_stop_without_replaying_it(self):
        self.charging();self.event('stop');self.event();self.event('confirmed');self.event();self.event('unknown')
        deadline=self.s.deadline
        self.event('restart');self.event(mode='OFF',power=0)
        self.assertEqual(self.s.step,Step.WAIT_STOP)
        self.assertEqual(self.s.deadline,deadline)
        self.assertNotEqual(self.event(),'stop')

    def test_restart_observes_unknown_restore_without_repeating_put(self):
        self.s=replace(self.s,state=State.STOPPING,step=Step.RESTORE,stop_outcome='confirmed')
        self.event();self.event('unknown');deadline=self.s.deadline
        self.event('restart');self.event()
        self.assertEqual(self.s.step,Step.WAIT_RESTORE)
        self.assertEqual(self.s.deadline,deadline)
        self.assertNotEqual(self.event(),'restore')

    def test_restart_does_not_retry_attention_automatically(self):
        self.s=replace(self.s,state=State.ATTENTION,step=Step.WAIT_RESTORE,reason='restore_unconfirmed')
        self.assertIsNone(self.event('restart'))
        self.assertEqual(self.s.state,State.ATTENTION)
        self.assertIsNone(self.event())

    def test_stop_cancels_an_unsent_start(self):
        self.started();self.s=replace(self.s,step=Step.START,start_epoch=0,start_outcome='not_sent')
        self.event('stop')
        self.assertEqual(self.event(),'off')
        self.event('confirmed',mode='OFF',power=0)
        self.assertEqual(self.event(),'restore')

    def test_restore_and_normal_release_require_local_stopped_evidence(self):
        for step in (Step.RESTORE,Step.AUTO):
            self.s=replace(self.s,state=State.STOPPING,step=step)
            self.assertIsNone(self.event(power=7.3))
            self.assertEqual(self.s.state,State.ATTENTION)

    def test_retarget_preserves_original_restore_and_does_not_claim_new_limit(self):
        self.charging()
        self.s=transition(self.s,'retarget',self.f,self.now,target=90).session
        self.assertEqual(self.s.state,State.PREPARING)
        self.assertEqual(self.s.restore,80)
        self.assertEqual(self.s.verified,0)
        self.assertEqual(self.s.target,90)

    def test_limit_write_waits_for_off_power_to_settle(self):
        self.event();self.event('confirmed')
        self.assertIsNone(self.event(power=7.3))
        self.assertEqual(self.s.step,Step.SET_LIMIT)
        self.assertEqual(self.event(seconds=2,power=0),'set_limit')

    def test_persistent_power_after_off_blocks_limit_write(self):
        self.event();self.event('confirmed')
        self.assertIsNone(self.event(seconds=31,power=7.3))
        self.assertEqual(self.s.state,State.ATTENTION)
        self.assertEqual(self.s.limit_epoch,0)


class NativeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.case=test_charging.ChargingTests();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.service=self.case.service
        self.now=time.time()
        self.addCleanup(patch.stopall)
        patch("time.time",side_effect=lambda:self.now).start()
        self.charging=self.case.data['vehicle']['charging']
        self.charging['settings']={'targetStateOfChargeInPercent':80}
        self.charging['status']['battery']['stateOfChargeInPercent']=85
        self.charging['status']['state']='CHARGING_INTERRUPTED'
        self.case.data['vehicle']['operations'].append({'name':'setChargingLimit'})
        self.writes=[];self.code=202;self.uncertain=False;self.power=0
        original=self.service.car.request
        def request(method,path,key,payload=None):
            if method=='PUT':
                self.writes.append(payload['targetStateOfChargeInPercent'])
                if self.uncertain:raise IntegrationError('network','Unknown',uncertain=True)
                return self.code,{},b''
            return original(method,path,key)
        self.service.car.request=request
        read=self.case.web.read
        self.case.web.read=lambda config:{**read(config),'read_at':self.date(self.now),'power_kw':self.power if self.case.web.mode != 'OFF' else 0}
        self.service.next_due.clear();self.service.status()

    def date(self,value):return datetime.fromtimestamp(value,timezone.utc).isoformat()

    def tick(self,seconds=2):
        self.now+=seconds
        self.service.cache['smartfox']['charger']=self.case.web.read(self.case.config.value['smartfox'])
        self.service.charging.tick()

    def capture(self,target,soc=85,state='CHARGING_INTERRUPTED'):
        self.charging['settings']['targetStateOfChargeInPercent']=target
        self.charging['carCapturedTimestamp']=self.date(self.now)
        self.charging['status']['state']=state
        self.charging['status']['battery']['stateOfChargeInPercent']=soc
        self.service.next_due['skoda']=0;self.service.update('skoda',verify_restoration=True)

    def start(self):return self.service.start_manual_charging(87,'native-fsm-start-0001',True)

    def running(self):
        self.start();self.tick()  # Off then PUT.
        self.now+=1;self.capture(87)
        self.tick();self.tick()  # Confirm limit then M+.
        self.tick();self.tick(3);self.tick()  # Stable ready, then Start.
        self.power=7.3;self.tick();self.tick(3)
        self.assertEqual(self.service.charging.session.state,State.CHARGING)

    def test_gui_start_uses_fsm_exact_put_then_single_post(self):
        self.running()
        self.assertEqual(self.writes,[87])
        self.assertEqual(self.case.effects,['OFF','M+','start'])
        self.assertEqual(self.service.charging.state()['machine_state'],'charging')
        self.assertEqual(self.service.charging.state()['target_control'],'vehicle')

    def test_missing_readback_blocks_before_any_device_change(self):
        self.charging['settings']={};self.capture(None)
        effects=list(self.case.effects)
        with self.assertRaises(IntegrationError):self.start()
        self.assertEqual(self.case.effects,effects)
        self.assertEqual(self.writes,[])
        self.assertIn('does not return',self.service.charging.state()['manual_start_reason'])

    def test_preflight_needs_budget_for_cleanup_and_does_not_change_devices(self):
        self.service.car.quota_remaining='9';self.service.car.quota_reset_at=self.service.car.clock()+3600
        with self.assertRaises(IntegrationError):self.start()
        self.assertEqual(self.writes,[]);self.assertEqual(self.case.effects,[])

    def test_duplicate_start_and_stop_ids_do_not_repeat_effects(self):
        self.running();self.start();self.assertEqual(self.writes,[87])
        self.service.stop_charge('native-fsm-stop-0001')
        self.service.stop_charge('native-fsm-stop-0001')
        self.tick();self.tick()
        self.service.stop_charge('native-fsm-stop-0001')
        self.assertEqual(self.case.effects.count('stop'),1)

    def test_vehicle_target_stop_restores_80_and_releases_a_plus_once(self):
        self.running();self.capture(87,87)
        self.tick();self.tick();self.tick()  # Request Stop, Off, cloud Stop.
        self.now+=1;self.capture(87,87)
        self.tick();self.tick()  # Stop proof then restore PUT.
        self.now+=1;self.capture(80,87)
        self.tick();self.tick()  # Restore proof then A+.
        self.assertEqual(self.writes,[87,80])
        self.assertEqual(self.service.charging.session.state,State.IDLE)
        self.assertEqual(self.case.web.mode,'A+')
        effects=list(self.case.effects)
        for _ in range(5):self.tick()
        self.assertEqual(self.case.effects,effects)

    def test_restart_reads_versioned_session_without_start_replay(self):
        self.running()
        saved=json.loads(self.service.charging.path.read_text())
        self.assertEqual(saved['schema_version'],2)
        self.assertNotIn('stop_requested',saved['session'])
        recovered=NativeCharging(self.service,saved,clock=lambda:self.now)
        self.service.charging=self.service.vehicle_limit=recovered
        self.tick();self.tick();self.tick(3)
        self.assertEqual(recovered.session.state,State.CHARGING)
        self.assertEqual(self.case.effects.count('start'),1)

    def test_schema_two_loads_through_service_factory(self):
        self.running()
        service=Service(self.case.config,self.case.secrets,self.service.sf,self.service.car,self.case.web,self.service.power)
        self.assertIsInstance(service.charging,NativeCharging)
        self.assertEqual(service.charging.session.state,State.RECOVERING)
        self.assertIs(service.vehicle_limit,service.charging)

    def test_quota_and_old_soc_do_not_trigger_legacy_protective_stop(self):
        self.running()
        self.service.car.quota_remaining='2';self.service.car.quota_reset_at=self.service.car.clock()+3600
        self.now+=3600;self.tick()
        self.assertEqual(self.case.web.mode,'M+')
        self.assertEqual(self.service.charging.session.state,State.CHARGING)

    def test_rejected_87_restores_normal_mode_without_rounding(self):
        self.code=422;self.start();self.tick();self.tick()
        self.assertEqual(self.writes,[87])
        self.assertNotIn('start',self.case.effects)
        self.assertEqual(self.case.web.mode,'A+')
        self.assertEqual(self.service.charging.session.result,'failed')

    def test_unknown_put_does_not_repeat_on_tick_or_restart(self):
        self.uncertain=True;self.start();self.tick()
        for _ in range(3):self.tick()
        self.assertEqual(self.writes,[87])
        self.assertEqual(self.service.charging.session.limit_outcome,'unknown')
        self.assertNotIn('start',self.case.effects)

    def test_diagnostics_explain_transitions_without_vin_or_key(self):
        self.running()
        data=report(self.service.diagnostic_snapshot(),True)
        self.assertEqual(data['charging_machine']['machine_state'],'charging')
        self.assertTrue(data['charging_machine']['transitions'])
        text=json.dumps(data)
        self.assertNotIn(self.case.config.value['skoda']['vin'],text)
        self.assertNotIn(self.case.secrets.key,text)

    def test_invalid_saved_proof_never_loads_as_a_running_session(self):
        self.running();saved=json.loads(self.service.charging.path.read_text())
        saved['session']['verified']=0
        self.service.charging.path.write_text(json.dumps(saved))
        service=Service(self.case.config,self.case.secrets,self.service.sf,self.service.car,self.case.web,self.service.power)
        self.assertEqual(service.charging.session.state,State.ATTENTION)
        self.assertFalse(service.charging.state()['can_start_manual'])

    def test_unknown_or_malformed_session_never_normalizes_wallbox_mode(self):
        for content in ('{"schema_version":999,"controller":"native"}','not-json'):
            self.service.charging.path.write_text(content)
            self.case.web.mode='M+'
            effects=list(self.case.effects)
            service=Service(self.case.config,self.case.secrets,self.service.sf,self.service.car,self.case.web,self.service.power)
            service.status()
            self.assertEqual(service.charging.session.state,State.ATTENTION)
            self.assertEqual(self.case.effects,effects)
            self.assertEqual(self.writes,[])

    def test_a_second_native_session_can_start_after_cleanup(self):
        self.test_vehicle_target_stop_restores_80_and_releases_a_plus_once()
        self.service.command_after=0
        result=self.service.start_manual_charging(90,'native-fsm-second-0001',True)
        self.assertEqual(result['machine_state'],'preparing')
        self.tick()
        self.assertEqual(self.writes,[87,80,90])
        self.assertEqual(self.service.charging.session.restore,80)

    def test_persistence_failure_prevents_device_effect(self):
        self.start()
        with patch('citifoxcharger.native_charging.controller.atomic_write',side_effect=OSError('storage full')):
            with self.assertRaises(OSError):self.tick()
        self.assertEqual(self.writes,[])
        self.assertNotIn('start',self.case.effects)

    def test_shutdown_waits_for_native_restoration(self):
        self.running();self.service.stop_charge('native-fsm-exit-0001')
        with self.assertRaises(ValueError):self.service.prepare_shutdown()
        self.assertFalse(self.service.stopping)

    def test_battery_care_cap_is_not_silently_disabled(self):
        self.service.cache['skoda']['charging_settings'].update(battery_care_mode='ACTIVATED',battery_care_target_percent=80)
        with self.assertRaises(IntegrationError):self.start()
        self.assertEqual(self.writes,[])
        self.assertEqual(self.case.effects,[])

    def test_history_survives_restart_without_importing_untrusted_fields(self):
        self.running();saved=json.loads(self.service.charging.path.read_text())
        saved['transitions'].append({'at':self.date(self.now),'from':'PRIVATE VIN','event':'tick'})
        native=NativeCharging(self.service,saved,clock=lambda:self.now)
        self.assertGreater(len(native.history),1)
        self.assertNotIn('PRIVATE VIN',json.dumps(native.history))

    def test_explicit_retry_reuses_verified_limit_without_automatic_repeated_start(self):
        self.running();self.power=0;self.tick();self.tick(46)
        self.assertEqual(self.service.charging.session.state,State.ATTENTION)
        self.capture(87,state='CHARGING_INTERRUPTED')
        self.service.command_after=0
        self.assertTrue(self.service.charging.can_retry_start())
        self.assertEqual(self.case.effects.count('start'),1)
        self.service.start_manual_charging(87,'native-fsm-retry-0001',True)
        self.tick();self.tick(3);self.tick()
        self.assertEqual(self.case.effects.count('start'),2)
        self.assertEqual(self.writes,[87])

    def test_non_integer_targets_never_change_devices(self):
        for target in (True,87.0,'87',None,0,101):
            with self.assertRaises(ValueError):
                self.service.start_manual_charging(target,'invalid-fsm-target-0001',True)
        self.assertEqual(self.case.effects,[])
        self.assertEqual(self.writes,[])


if __name__ == '__main__':unittest.main()
