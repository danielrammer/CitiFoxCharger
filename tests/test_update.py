import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from update import update, UpdateError
from citifoxcharger.lifecycle import ControlError


class FakeControl:
    def __init__(self, active=False, persisted=True, blocked=False):
        self.active,self.persisted,self.blocked=active,persisted,blocked
        self.actions=[]
    def health(self):return {'version':'1.6.1'} if self.active else None
    def request(self,path):return {'key':{'configured':True,'persisted':self.persisted}}
    def action(self,action,*args,**kwargs):
        if self.blocked:raise ControlError('A charging session is being monitored.')
        self.actions.append(action)
        self.active=False
        return 'Backend stopped.'


@unittest.skipUnless(shutil.which('git'),'Git required')
class GitUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.remote=self.root/'remote.git'
        self.seed=self.root/'seed'
        self.checkout=self.root/'program with spaces'
        self.git('init','--bare',str(self.remote))
        self.git('init','--initial-branch=main',str(self.seed))
        self.git('-C',str(self.seed),'config','user.name','Update test')
        self.git('-C',str(self.seed),'config','user.email','update@example.invalid')
        self.commit('old')
        self.git('-C',str(self.seed),'remote','add','origin',str(self.remote))
        self.git('-C',str(self.seed),'push','origin','main')
        self.git('clone','--branch','main',str(self.remote),str(self.checkout))
    def git(self,*args):
        return subprocess.run(['git',*args],capture_output=True,text=True,check=True).stdout
    def commit(self,value):
        (self.seed/'version.txt').write_text(value)
        self.git('-C',str(self.seed),'add','version.txt')
        self.git('-C',str(self.seed),'commit','-m',value)
    def newer(self):
        self.commit('new')
        self.git('-C',str(self.seed),'push','origin','main')
    def run_update(self,control=None,path=None):
        return update(path or self.checkout,control=control or FakeControl(),remotes={str(self.remote)})
    def test_fast_forward_updates_and_stops_only_the_existing_idle_backend(self):
        self.newer()
        control=FakeControl(active=True)
        runtime=self.root/'runtime';runtime.mkdir()
        (runtime/'key.dpapi').write_bytes(b'private-test-data')
        self.run_update(control)
        self.assertEqual((self.checkout/'version.txt').read_text(),'new')
        self.assertEqual(control.actions,['stop'])
        self.assertEqual((runtime/'key.dpapi').read_bytes(),b'private-test-data')
    def test_local_changes_are_preserved_without_stopping_backend(self):
        self.newer()
        (self.checkout/'version.txt').write_text('user edit')
        control=FakeControl(active=True)
        with self.assertRaises(UpdateError):self.run_update(control)
        self.assertEqual(control.actions,[])
        self.assertEqual((self.checkout/'version.txt').read_text(),'user edit')
    def test_active_monitor_refuses_update_without_modifying_program(self):
        self.newer()
        with self.assertRaises(ControlError):self.run_update(FakeControl(active=True,blocked=True))
        self.assertEqual((self.checkout/'version.txt').read_text(),'old')
    def test_ram_key_blocks_shutdown_and_update(self):
        self.newer()
        control=FakeControl(active=True,persisted=False)
        with self.assertRaises(UpdateError):self.run_update(control)
        self.assertEqual(control.actions,[])
        self.assertEqual((self.checkout/'version.txt').read_text(),'old')
    def test_unexpected_origin_is_rejected(self):
        with self.assertRaises(UpdateError):update(self.checkout,control=FakeControl())
    def test_zip_install_creates_and_then_reuses_a_separate_git_checkout(self):
        folder=self.root/'extracted zip';folder.mkdir()
        (folder/'user-file.txt').write_text('keep')
        with patch('update.REPOSITORY',str(self.remote)):
            target=self.run_update(path=folder)
        self.assertEqual(target,self.root/'CitiFoxCharger-Git')
        self.newer()
        self.assertEqual(self.run_update(path=folder),target)
        self.assertEqual((target/'version.txt').read_text(),'new')
        self.assertEqual((folder/'user-file.txt').read_text(),'keep')
    def test_existing_non_git_bootstrap_folder_is_not_overwritten(self):
        folder=self.root/'extracted zip';folder.mkdir()
        target=self.root/'CitiFoxCharger-Git';target.mkdir()
        (target/'keep.txt').write_text('keep')
        with self.assertRaises(UpdateError):self.run_update(path=folder)
        self.assertEqual((target/'keep.txt').read_text(),'keep')

    def test_diverged_history_is_not_merged_or_reset(self):
        self.git('-C',str(self.checkout),'config','user.name','Update test')
        self.git('-C',str(self.checkout),'config','user.email','update@example.invalid')
        (self.checkout/'local.txt').write_text('local commit')
        self.git('-C',str(self.checkout),'add','local.txt')
        self.git('-C',str(self.checkout),'commit','-m','local change')
        before=self.git('-C',str(self.checkout),'rev-parse','HEAD')
        self.newer()
        control=FakeControl(active=True)
        with self.assertRaises(UpdateError):self.run_update(control)
        self.assertEqual(self.git('-C',str(self.checkout),'rev-parse','HEAD'),before)
        self.assertEqual(control.actions,[])

    def test_fetch_failure_leaves_backend_running(self):
        missing=str(self.root/'missing-remote.git')
        self.git('-C',str(self.checkout),'remote','set-url','origin',missing)
        control=FakeControl(active=True)
        with self.assertRaises(UpdateError):
            update(self.checkout,control=control,remotes={missing})
        self.assertEqual(control.actions,[])


@unittest.skipUnless(os.name=='nt','Windows batch execution required')
class BatchWindowTests(unittest.TestCase):
    def exercise(self,name,exit_code,self_update=False):
        with tempfile.TemporaryDirectory(prefix='citifox batch ') as folder:
            path=Path(folder)
            shutil.copy2(Path(__file__).resolve().parents[1]/name,path/name)
            stub='import sys\n'
            if self_update:
                stub += "from pathlib import Path\nPath('Update.cmd').write_text('@echo off\\necho REPLACED\\n')\n"
            stub+=f'print("TEST_COMPLETED",flush=True)\nsys.exit({exit_code})\n'
            for script in ('manage.py','setup_network.py','update.py'):(path/script).write_text(stub)
            process=subprocess.Popen([os.environ.get('COMSPEC','cmd.exe'),'/d','/c',name],cwd=path,
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,
                errors='replace',creationflags=subprocess.CREATE_NO_WINDOW)
            ready=threading.Event();lines=[]
            def read():
                for line in process.stdout:
                    lines.append(line)
                    if 'Press any key' in line:ready.set()
            reader=threading.Thread(target=read,daemon=True);reader.start()
            try:
                self.assertTrue(ready.wait(12),''.join(lines))
                with self.assertRaises(subprocess.TimeoutExpired):process.wait(timeout=.1)
                process.stdin.write('\n');process.stdin.flush()
                self.assertEqual(process.wait(timeout=5),exit_code)
                reader.join(timeout=2)
                self.assertIn('TEST_COMPLETED',''.join(lines))
            finally:
                if process.poll() is None:process.kill();process.wait(timeout=3)
                process.stdin.close();process.stdout.close()
    def test_windows_remain_open_after_success_and_failure(self):
        for name in ('Update.cmd','Stop.cmd','Setup-Network.cmd','Setup-Firewall.cmd'):
            for code in (0,7):
                with self.subTest(name=name,code=code):self.exercise(name,code)
    def test_updater_still_pauses_and_preserves_exit_code_after_its_batch_is_replaced(self):
        self.exercise('Update.cmd',7,self_update=True)
