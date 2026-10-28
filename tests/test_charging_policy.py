import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from datetime import datetime, timezone
from citifoxcharger.charging_policy import ChargingPolicy

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
