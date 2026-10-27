import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from citifoxcharger.app_update import AppUpdater
from update import app_job,UpdateError


JOB='a'*32


class AppUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='app update ')
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)
        self.spawn=Mock(return_value=Mock(poll=Mock(return_value=None)))
        self.updater=AppUpdater(self.path,self.path,8765,self.spawn)
        self.service=SimpleNamespace(lock=threading.RLock(),stopping=False,app_update_pending=False,
            secrets=SimpleNamespace(key='',persisted=False),
            charging=SimpleNamespace(active=False,state=lambda:{'can_shutdown':not self.service.charging.active}))

    def test_detached_job_is_single_and_uses_argument_list_with_same_profile(self):
        first=self.updater.launch(self.service)
        self.assertEqual(first['phase'],'queued')
        self.assertEqual(self.updater.launch(self.service)['job_id'],first['job_id'])
        self.spawn.assert_called_once()
        args=self.spawn.call_args.args[0]
        self.assertEqual(args[-4:],['--port','8765','--data-dir',str(self.path)])
        self.assertNotIn('shell',self.spawn.call_args.kwargs)
        self.assertEqual(self.spawn.call_args.kwargs['env']['GIT_TERMINAL_PROMPT'],'0')
        self.assertTrue(self.service.app_update_pending)

    def test_active_monitoring_ram_key_and_shutdown_block_the_helper(self):
        for field in ('charging','key','stopping'):
            with self.subTest(field=field):
                self.service.charging.active=field=='charging'
                self.service.secrets.key='fake-memory-key' if field=='key' else ''
                self.service.stopping=field=='stopping'
                with self.assertRaises(ValueError):self.updater.launch(self.service)
        self.spawn.assert_not_called()
        self.assertFalse(self.updater.path.exists())

    def test_failed_spawn_or_dead_helper_has_a_visible_failed_state(self):
        self.spawn.side_effect=OSError('test')
        with self.assertRaises(ValueError):self.updater.launch(self.service)
        self.assertEqual(self.updater.state()['phase'],'failed')
        self.spawn.side_effect=None
        self.spawn.return_value.poll.return_value=1
        self.assertEqual(self.updater.launch(self.service)['phase'],'failed')

    def test_launch_allows_a_confirmed_local_stop_with_a_persisted_key(self):
        self.service.charging.active=True
        self.service.charging.state=lambda:{'can_shutdown':True}
        self.service.secrets.key='fake-persisted-key'
        self.service.secrets.persisted=True
        self.assertEqual(self.updater.launch(self.service)['phase'],'queued')
        self.spawn.assert_called_once()

    def test_public_state_omits_arbitrary_saved_fields_and_bad_job_ids(self):
        self.updater.path.write_text(json.dumps({'job_id':JOB,'phase':'checking','key':'SECRET','message':'SECRET'}))
        self.assertNotIn('SECRET',json.dumps(self.updater.state()))
        self.updater.path.write_text(json.dumps({'job_id':'../invalid','phase':'checking'}))
        self.assertEqual(self.updater.state()['phase'],'idle')

    def test_inactive_session_with_pending_phase_restoration_blocks_update(self):
        self.service.charging.state=lambda:{'can_shutdown':False}
        with self.assertRaises(ValueError):self.updater.launch(self.service)
        self.spawn.assert_not_called()

    def job_fixture(self):
        self.updater.write(JOB,'queued')
        self.health={'app':'CitiFoxCharger','instance_id':'old'}
        self.client=Mock()
        self.client.health.side_effect=lambda:self.health
        def run(args,**kwargs):
            self.health={'app':'CitiFoxCharger','instance_id':'new'}
            return SimpleNamespace(returncode=0)
        self.runner=Mock(side_effect=run)

    @patch('update.time.sleep')
    def test_worker_updates_then_starts_without_browser_and_confirms_new_instance(self,sleep):
        self.job_fixture()
        def update(directory,port,data_dir,**kwargs):
            self.assertEqual(kwargs['timeout'],120)
            for phase in ('checking','stopping','applying'):kwargs['progress'](phase)
            self.health=None
            return directory
        self.assertEqual(app_job(JOB,self.path,8765,self.path,update_function=update,
                                client=self.client,runner=self.runner),0)
        self.assertEqual(self.updater.state()['phase'],'complete')
        self.assertIn('--no-browser',self.runner.call_args.args[0])
        self.assertEqual(self.runner.call_args.args[0][-2:],['--data-dir',str(self.path)])

    @patch('update.time.sleep')
    def test_failed_preflight_leaves_running_backend_untouched(self,sleep):
        self.job_fixture()
        failed=Mock(side_effect=UpdateError('tracked changes'))
        self.assertEqual(app_job(JOB,self.path,8765,self.path,update_function=failed,
                                client=self.client,runner=self.runner),1)
        self.runner.assert_not_called()
        self.assertEqual(self.updater.state()['phase'],'failed')

    @patch('update.time.sleep')
    def test_git_timeout_does_not_stop_or_restart_the_original_backend(self,sleep):
        self.job_fixture()
        failed=Mock(side_effect=subprocess.TimeoutExpired('git',120))
        self.assertEqual(app_job(JOB,self.path,8765,self.path,update_function=failed,
                                client=self.client,runner=self.runner),1)
        self.runner.assert_not_called()
        self.assertEqual(self.updater.state()['phase'],'failed')

    @patch('update.time.sleep')
    def test_failure_after_shutdown_recovers_backend_but_does_not_claim_update_success(self,sleep):
        self.job_fixture()
        def failed(*args,**kwargs):
            self.health=None
            raise UpdateError('merge failed')
        self.assertEqual(app_job(JOB,self.path,8765,self.path,update_function=failed,
                                client=self.client,runner=self.runner),1)
        self.runner.assert_called_once()
        self.assertEqual(self.health['instance_id'],'new')
        self.assertEqual(self.updater.state()['phase'],'failed')

    @patch('update.time.sleep')
    def test_unchanged_backend_instance_does_not_complete_the_job(self,sleep):
        self.job_fixture()
        self.runner.side_effect=lambda *a,**k:SimpleNamespace(returncode=0)
        self.assertEqual(app_job(JOB,self.path,8765,self.path,update_function=lambda *a,**k:self.path,
                                client=self.client,runner=self.runner),1)
        self.assertEqual(self.updater.state()['phase'],'failed')

    def test_unknown_job_never_runs_git_or_starts_a_backend(self):
        update,runner=Mock(),Mock()
        self.assertEqual(app_job(JOB,self.path,8765,self.path,update_function=update,runner=runner),1)
        update.assert_not_called();runner.assert_not_called()
