import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from citifoxcharger.power import PowerGuard, SYSTEM_REQUIRED, EXECUTION_REQUIRED
from citifoxcharger.background import spawn_backend, dashboard_running, wait_until_ready


class FakePower:
    def __init__(self, fail=None):
        self.events, self.fail = [], fail
    def create(self): self.events.append('create'); return 42
    def set(self, handle, request):
        self.events.append(('set', request))
        if request == self.fail: raise OSError('test')
    def clear(self, handle, request): self.events.append(('clear', request))
    def close(self, handle): self.events.append('close')
    def on_ac_power(self): return True


class PowerTests(unittest.TestCase):
    def test_active_requests_system_and_execution_but_never_display(self):
        backend=FakePower()
        power=PowerGuard(backend)
        power.update(True)
        self.assertEqual(backend.events,['create',('set',1),('set',3)])
        self.assertTrue(power.state()['requested'])
        power.update(True)
        self.assertEqual(len(backend.events),3)
        power.update(False)
        self.assertEqual(backend.events[-3:],[('clear',3),('clear',1),'close'])
        self.assertFalse(power.state()['requested'])

    def test_partial_failure_releases_every_successful_request(self):
        backend=FakePower(fail=EXECUTION_REQUIRED)
        power=PowerGuard(backend)
        power.update(True)
        self.assertFalse(power.state()['requested'])
        self.assertIsNotNone(power.state()['warning'])
        self.assertEqual(backend.events[-2:],[('clear',1),'close'])

    def test_close_releases_and_cannot_reacquire(self):
        backend=FakePower()
        power=PowerGuard(backend)
        power.update(True)
        power.close()
        saved=list(backend.events)
        power.update(True)
        power.close()
        self.assertEqual(backend.events,saved)

    def test_no_request_without_active_session(self):
        backend=FakePower()
        power=PowerGuard(backend)
        power.update(False)
        self.assertEqual(backend.events,[])


class BackgroundTests(unittest.TestCase):
    @patch('citifoxcharger.background.subprocess.Popen')
    def test_detached_process_uses_files_not_console_and_preserves_paths(self, process):
        with tempfile.TemporaryDirectory(prefix='background test ') as directory:
            child=spawn_backend(['--no-browser','--data-dir',directory],Path(directory))
            self.assertIs(child,process.return_value)
            call=process.call_args
            self.assertEqual(call.args[0][-3:],['--no-browser','--data-dir',directory])
            self.assertTrue(call.kwargs['stdout'].closed)
            self.assertIs(call.kwargs['stderr'],call.kwargs['stdout'])
            self.assertNotIn('shell',call.kwargs)
            self.assertTrue(call.kwargs.get('creationflags') or call.kwargs.get('start_new_session'))
            self.assertTrue((Path(directory)/'background.log').exists())

    @patch('citifoxcharger.background.urllib.request.build_opener')
    def test_health_requires_product_identity(self, opener):
        response=opener.return_value.open.return_value.__enter__.return_value
        response.read.return_value=b'{"status":"ok","app":"AnotherApp"}'
        self.assertFalse(dashboard_running(8765))
        response.read.return_value=b'{"app":"CitiFoxCharger"}'
        self.assertTrue(dashboard_running(8765))

    @patch('citifoxcharger.background.urllib.request.build_opener', side_effect=OSError)
    def test_missing_server_is_not_running(self, opener):
        self.assertFalse(dashboard_running(8765))

    @patch('citifoxcharger.background.dashboard_running')
    def test_exited_child_is_not_reported_as_success(self, healthy):
        child=MagicMock()
        child.poll.return_value=1
        self.assertFalse(wait_until_ready(child,8765))
        healthy.assert_not_called()

    @patch('citifoxcharger.background.dashboard_running',return_value=True)
    def test_live_child_with_product_health_is_ready(self, healthy):
        child=MagicMock()
        child.poll.return_value=None
        self.assertTrue(wait_until_ready(child,8765))
