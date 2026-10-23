"""An isolated capability test must never become an implicit charging start."""
import copy
import json
import time
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import test_charging
from citifoxcharger.service import Service
from citifoxcharger.skoda import IntegrationError
from citifoxcharger.limit_test import LimitTest
from citifoxcharger.diagnostics import report


class LimitTestTests(unittest.TestCase):
    def setUp(self):
        self.case=test_charging.ChargingTests();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.service=self.case.service
        self.data=self.case.data['vehicle']['charging']
        self.data['settings']={}  # The real Citigo omits the global limit.
        self.case.data['vehicle']['operations'].append({'name':'setChargingLimit'})
        self.service.next_due['skoda']=0;self.service.update('skoda')
        self.service.set_charger_mode('OFF','test-explicit-off-0001')
        self.effects=list(self.case.effects)
        self.puts=[];self.code=202;self.uncertain=False
        original=self.service.car.request
        def request(method,path,key,payload=None):
            if method=='PUT':
                self.puts.append((path,copy.deepcopy(payload)))
                if self.uncertain:raise IntegrationError('network','PRIVATE BODY',uncertain=True)
                return self.code,{},json.dumps({'detail':'PRIVATE BODY'}).encode()
            return original(method,path,key)
        self.service.car.request=request

    def send(self,target=90,request_id='test-vehicle-limit-0001'):
        return self.service.test_vehicle_limit(target,request_id)

    def test_missing_readback_does_not_block_single_explicit_put(self):
        self.assertTrue(self.service.limit_test.state()['can_set'])
        result=self.send()
        self.assertEqual(self.puts,[(f'/api/v1/vehicles/{self.case.config.value["skoda"]["vin"]}/charging/limit',{'targetStateOfChargeInPercent':90})])
        self.assertEqual(result['attempts'][-1]['outcome'],'accepted')
        self.assertEqual(result['attempts'][-1]['verification'],'not_returned')
        self.assertEqual(result['attempts'][-1]['http_status'],202)
        self.assertEqual(self.case.effects,self.effects)

    def test_repeated_id_never_repeats_a_possibly_uncertain_write(self):
        self.uncertain=True;self.send();self.send()
        self.assertEqual(len(self.puts),1)
        self.assertEqual(self.service.limit_test.state()['attempts'][-1]['outcome'],'unknown')
        with self.assertRaises(ValueError):self.send(80)
        self.assertEqual(len(self.puts),1)

    def test_rejected_request_does_not_round_or_start_or_change_charger_mode(self):
        self.code=422;result=self.send()
        self.assertEqual(result['attempts'][-1]['outcome'],'rejected')
        self.assertEqual(result['attempts'][-1]['http_status'],422)
        self.assertEqual(self.case.effects,self.effects)
        self.assertNotIn('PRIVATE',json.dumps(result))

    def test_unsupported_test_values_are_never_rounded(self):
        for value in (87,True,90.0,'90',None,0,110):
            with self.assertRaises(ValueError):self.send(value)
        self.assertEqual(self.puts,[])
        self.assertFalse(self.service.limit_test.active)

    def test_every_start_and_normal_mode_entry_is_blocked_during_isolation(self):
        self.send()
        for call in (lambda:self.service.start_manual_charging(90,'blocked-manual-0001',True),
                     lambda:self.service.force_charge(90,'blocked-legacy-0001',True),
                     lambda:self.service.force_charge_native(90,'blocked-native-0001',True),
                     lambda:self.service.command('start','blocked-direct-0001',True),
                     lambda:self.service.set_charger_mode('M+','blocked-mplus-0001'),
                     lambda:self.service.set_charger_mode('A+','blocked-auto-0001')):
            with self.assertRaises(IntegrationError):call()
        status=self.service.status()
        self.assertFalse(status['charging']['can_start_manual'])
        self.assertFalse(status['charging']['can_start_manual_with_stale'])
        self.assertEqual(self.case.effects,self.effects)

    def test_normal_80_restore_is_an_explicit_second_put_with_no_other_command(self):
        self.send();result=self.send(80,'restore-test-limit-0001')
        self.assertEqual([x[1]['targetStateOfChargeInPercent'] for x in self.puts],[90,80])
        self.assertEqual(result['attempts'][-1]['outcome'],'accepted')
        self.assertNotEqual(result['attempts'][-1]['verification'],'confirmed')
        self.assertEqual(self.case.effects,self.effects)

    def test_test_end_keeps_off_and_never_claims_restoration(self):
        self.send();self.send(80,'restore-test-limit-0001')
        with self.assertRaises(ValueError):self.service.end_limit_test(False)
        result=self.service.end_limit_test(True)
        self.assertFalse(result['active'])
        self.assertEqual(self.case.effects,self.effects)
        self.assertEqual(self.service.charging.normal_mode,'OFF')
        self.assertEqual(self.case.web.mode,'OFF')
        self.assertNotEqual(result['attempts'][-1]['verification'],'confirmed')
        self.service.set_charger_mode('A+','explicit-release-auto-0001')
        self.assertEqual(self.case.effects,self.effects+['A+'])

    def test_test_does_not_add_cloud_reads_or_repeat_puts_while_waiting(self):
        self.send()
        with patch.object(self.service.car,'read',wraps=self.service.car.read) as read:
            for _ in range(30):self.service.status()
            read.assert_not_called()
        self.assertEqual(len(self.puts),1)
        self.assertEqual(self.case.effects,self.effects)

    def test_readback_requires_exact_value_and_newer_capture(self):
        self.send()
        self.data['settings']['targetStateOfChargeInPercent']=90
        self.service.next_due['skoda']=0;self.service.update('skoda')
        self.assertNotEqual(self.service.limit_test.state()['attempts'][-1]['verification'],'confirmed')
        self.data['carCapturedTimestamp']=datetime.now(timezone.utc).isoformat()
        self.service.next_due['skoda']=0;self.service.update('skoda')
        self.assertEqual(self.service.limit_test.state()['attempts'][-1]['verification'],'confirmed')

    def test_different_readback_is_not_success(self):
        self.send()
        self.data['settings']['targetStateOfChargeInPercent']=80
        self.data['carCapturedTimestamp']=datetime.now(timezone.utc).isoformat()
        self.service.next_due['skoda']=0;self.service.update('skoda')
        result=self.service.limit_test.state()['attempts'][-1]
        self.assertEqual(result['verification'],'different')
        self.assertEqual(result['reported_limit'],80)

    def test_current_actual_off_is_checked_even_if_cache_says_off(self):
        self.case.web.mode='M+'
        with self.assertRaises(IntegrationError):self.send()
        self.assertEqual(self.puts,[])
        self.assertEqual(self.case.effects,self.effects)

    def test_invalid_or_nonzero_power_prevents_write(self):
        original=self.case.web.read
        for change in ({'power_kw':7.3},{'power_kw':float('nan')},{'power_kw':True},
                       {'read_at':'2000-01-01T00:00:00Z'},{'telemetry_available':False}):
            with patch.object(self.case.web,'read',side_effect=lambda c,x=change:{**original(c),**x}):
                with self.assertRaises(IntegrationError):self.send()
        self.assertEqual(self.puts,[])

    def test_active_charging_session_or_old_native_restore_cannot_be_bypassed(self):
        self.service.charging.save(status='monitoring')
        with self.assertRaises(IntegrationError):self.send()
        self.service.charging.save(status='error',vehicle_limit={'phase':'attention'})
        with self.assertRaises(IntegrationError):self.send()
        self.assertEqual(self.puts,[])

    def test_persisted_test_prevents_replay_and_a_plus_normalization_after_restart(self):
        self.uncertain=True;self.send()
        recovered=Service(self.case.config,self.case.secrets,self.service.sf,self.service.car,self.case.web,self.service.power)
        recovered.status()
        recovered.test_vehicle_limit(90,'test-vehicle-limit-0001')
        self.assertEqual(len(self.puts),1)
        self.assertEqual(self.case.effects,self.effects)
        self.assertTrue(recovered.limit_test.active)
        self.assertEqual(recovered.charging.normal_mode,'OFF')

    def test_corrupt_test_needs_explicit_end_and_sends_nothing(self):
        self.service.limit_test.path.write_text('not json')
        recovered=LimitTest(self.service)
        with self.assertRaises(IntegrationError):recovered.send(90,'corrupt-test-id-0001')
        self.assertTrue(recovered.active)
        recovered.end(True)
        self.assertFalse(recovered.active)
        self.assertNotIn('invalid',recovered.record)
        self.assertEqual(self.puts,[])

    def test_persist_failure_prevents_put(self):
        with patch('citifoxcharger.limit_test.atomic_write',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.send()
        self.assertEqual(self.puts,[])

    def test_known_low_budget_blocks_test_but_allows_explicit_restore_from_reserve(self):
        self.service.car.quota_remaining='3';self.service.car.quota_reset_at=self.service.car.clock()+3600
        with self.assertRaises(IntegrationError):self.send()
        self.service.car.quota_remaining='4';self.send()
        self.service.car.quota_remaining='1';self.send(80,'restore-reserve-test-0001')
        self.assertEqual(len(self.puts),2)

    def test_server_cooldown_is_never_bypassed(self):
        self.service.car.blocked_until=self.service.car.clock()+60
        with self.assertRaises(IntegrationError):self.send()
        self.assertEqual(self.puts,[])

    def test_cannot_spam_different_ids_to_bypass_cooldown(self):
        self.send()
        with self.assertRaises(IntegrationError):self.send(100,'another-request-0001')
        self.assertEqual(len(self.puts),1)

    def test_restore_button_cannot_spam_identical_80_requests_with_new_ids(self):
        self.send();self.send(80,'restore-once-0001')
        with self.assertRaises(IntegrationError):self.send(80,'restore-again-0002')
        self.assertEqual(len(self.puts),2)
        self.assertFalse(self.service.limit_test.state()['can_restore'])

    def test_config_change_and_key_removal_require_test_end(self):
        self.send()
        with self.assertRaises(IntegrationError):self.service.save_config(self.case.config.value)
        with self.assertRaises(IntegrationError):self.service.clear_key()

    def test_diagnostics_omit_identity_and_raw_error_messages(self):
        self.uncertain=True;self.send()
        diagnostic=report(self.service.diagnostic_snapshot(),True)
        text=json.dumps(diagnostic)
        self.assertTrue(diagnostic['limit_test']['active'])
        self.assertEqual(diagnostic['limit_test']['attempts'][-1]['outcome'],'unknown')
        self.assertNotIn(self.case.config.value['skoda']['vin'],text)
        self.assertNotIn(self.case.secrets.key,text)
        self.assertNotIn('PRIVATE BODY',text)
        self.assertNotIn(self.service.limit_test.identity(),text)


if __name__=='__main__':unittest.main()
