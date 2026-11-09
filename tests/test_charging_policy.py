import unittest
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from datetime import datetime, timezone
from citifoxcharger.charging_policy import ChargingPolicy
from citifoxcharger.skoda import IntegrationError

class PolicyTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.s=SimpleNamespace(config=SimpleNamespace(directory=Path(self.temp.name),value={'smartfox':{}}),stopping=False,app_update_pending=False,cache={},web=Mock(),charging=SimpleNamespace(active=False,value={},save=Mock(),manual=SimpleNamespace(reason=lambda:None)),limit_test=SimpleNamespace(active=False),limit_trial=SimpleNamespace(active=False),start_manual_charging=Mock(return_value={}),stop_charge=Mock())
  self.p=ChargingPolicy(self.s)
 def enable(self):self.p.save(only=True,automatic=True)
 def test_ownership_idle_always_off(self):
  self.p.save(only=True);self.p.tick();self.s.web.set_charger_mode.assert_called_once_with({},'OFF');self.s.start_manual_charging.assert_not_called()
 def test_one_start_per_window_including_restart(self):
  self.enable()
  with patch.object(ChargingPolicy,'window',return_value='2026-10-28T22:00'):
   self.p.tick();self.p.next_attempt=0;self.p.tick();ChargingPolicy(self.s).tick()
  self.assertEqual(self.s.start_manual_charging.call_count,1)
 def test_stop_suppresses_current_window(self):
  self.enable()
  with patch.object(self.p,'window',return_value='2026-10-28T22:00'):
   self.p.suppress_window();self.p.tick()
  self.s.start_manual_charging.assert_not_called()
 def test_window_end_stops_automatic_but_not_manual(self):
  self.enable();self.s.charging.active=True;self.s.charging.value={'automatic_window':'old'}
  with patch.object(self.p,'window',return_value=None):self.p.tick()
  self.s.stop_charge.assert_called_once()
  self.s.stop_charge.reset_mock();self.s.charging.value={};self.p.next_attempt=0;self.p.tick();self.s.stop_charge.assert_not_called()
 def test_midnight_has_same_window_identity(self):
  self.assertEqual(self.p.window(datetime(2026,10,29,2,tzinfo=timezone.utc)),'2026-10-28T22:00')
 def test_invalid_settings_never_replace_saved_policy(self):
  self.enable()
  for changes in ({'target':0},{'only':False},{'end':'22:00'}):
   with self.assertRaises(ValueError):self.p.save(**changes)
  self.assertTrue(self.p.value['only'])
 def test_corrupt_saved_policy_fails_closed(self):
  self.p.path.write_text('broken');self.assertTrue(ChargingPolicy(self.s).value['only'])

 def test_initial_failure_retries_every_three_minutes_unless_manually_stopped(self):
  self.enable()
  self.s.charging.save.side_effect=lambda **changes:self.s.charging.value.update(changes)
  self.s.start_manual_charging.side_effect=ValueError('simulated initial failure')
  with patch.object(self.p,'window',return_value='2026-11-09T22:00'),patch('time.time',return_value=1000) as clock:
   self.p.tick()
   self.assertEqual(self.s.start_manual_charging.call_count,1)
   clock.return_value=1179;self.p.next_attempt=0;self.p.tick()
   self.assertEqual(self.s.start_manual_charging.call_count,1)
   clock.return_value=1180;self.p.next_attempt=0;self.p.tick()
   self.assertEqual(self.s.start_manual_charging.call_count,2)
   self.p.suppress_window()
   clock.return_value=2000;self.p.next_attempt=0;self.p.tick()
   self.assertEqual(self.s.start_manual_charging.call_count,2)

 def test_three_aborts_in_rolling_hour_persist_a_window_block(self):
  self.enable()
  with patch.object(ChargingPolicy,'window',return_value='2026-11-09T22:00') as window,patch('time.time',return_value=1000) as clock:
   self.assertFalse(self.p.record_abort())
   clock.return_value=1100;self.assertFalse(self.p.record_abort())
   clock.return_value=4599;self.assertTrue(self.p.record_abort())
   self.assertTrue(ChargingPolicy(self.s).is_blocked())
   self.p.save(automatic=False);self.p.save(automatic=True)
   clock.return_value=10000;self.p.next_attempt=0;self.p.tick()
   self.s.start_manual_charging.assert_not_called()
   self.assertTrue(self.p.state()['blocked'])
   window.return_value='2026-11-10T22:00';clock.return_value=90000
   self.p.next_attempt=0;self.p.tick()
   self.s.start_manual_charging.assert_called_once()
   self.assertFalse(self.p.is_blocked())

 def test_aborts_older_than_one_hour_do_not_count(self):
  self.enable()
  with patch.object(self.p,'window',return_value='2026-11-09T22:00'),patch('time.time',return_value=1000) as clock:
   self.p.record_abort()
   clock.return_value=1100;self.p.record_abort()
   clock.return_value=4601;self.assertFalse(self.p.record_abort())
   self.assertEqual(self.p.value['automatic_aborts'],[1100,4601])

 def test_failed_initial_attempts_stop_after_third_abort(self):
  self.enable()
  self.s.charging.save.side_effect=lambda **changes:self.s.charging.value.update(changes)
  self.s.start_manual_charging.side_effect=ValueError('simulated initial failure')
  with patch.object(self.p,'window',return_value='2026-11-09T22:00'),patch('time.time',return_value=1000) as clock:
   for now in (1000,1180,1360,1540,1720):
    clock.return_value=now;self.p.next_attempt=0;self.p.tick()
   self.assertEqual(self.s.start_manual_charging.call_count,3)
   self.assertTrue(self.p.is_blocked())

 def test_initial_target_already_reached_is_not_an_abort_or_retry(self):
  self.enable()
  self.s.charging.save.side_effect=lambda **changes:self.s.charging.value.update(changes)
  self.s.start_manual_charging.side_effect=IntegrationError('target_reached','The selected target is already reached.')
  with patch.object(self.p,'window',return_value='2026-11-09T22:00'),patch('time.time',return_value=1000) as clock:
   self.p.tick()
   clock.return_value=2000;self.p.next_attempt=0;self.p.tick()
   self.s.start_manual_charging.assert_called_once()
   self.assertEqual(self.p.value['automatic_aborts'],[])
   self.assertFalse(self.p.is_blocked())

 def test_legacy_policy_migrates_without_erasing_ownership_or_claim(self):
  self.enable();self.p.save(claimed='2026-11-09T22:00')
  legacy={k:v for k,v in self.p.value.items() if k not in ('automatic_aborts','blocked_window')}
  self.p.path.write_text(json.dumps(legacy))
  recovered=ChargingPolicy(self.s)
  self.assertTrue(recovered.value['only']);self.assertTrue(recovered.value['automatic'])
  self.assertEqual(recovered.value['claimed'],'2026-11-09T22:00')
  self.assertEqual(recovered.value['automatic_aborts'],[])
  self.assertIsNone(recovered.value['blocked_window'])

 def test_bad_abort_history_is_rejected(self):
  for changes in ({'automatic_aborts':[float('nan')]},{'automatic_aborts':[True]},
                  {'automatic_aborts':[1,2,3,4]},{'blocked_window':[]}):
   with self.subTest(changes=changes),self.assertRaises(ValueError):self.p.save(**changes)
