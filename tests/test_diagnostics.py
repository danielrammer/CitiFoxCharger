import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from diagnostics import main, report, saved_status


class DiagnosticsTests(unittest.TestCase):
    def test_report_omits_secrets_identity_arbitrary_text_and_raw_sections(self):
        data = {'skoda': {'soc':86, 'vin':'PRIVATE-VIN', 'key':'PRIVATE-KEY',
                         'read_at':'PRIVATE-KEY', 'message':'PRIVATE-KEY', 'stale':True},
                'charging': {'target_percent':82, 'status':'paused', 'request_id':'PRIVATE-KEY',
                             'message':'PRIVATE-KEY', 'vin':'PRIVATE-VIN', 'stop_requested':False},
                'smartfox':{'charger':{'power_kw':4.6, 'mode':'M+', 'raw':'PRIVATE-KEY'}},
                'last_command':{'action':'start', 'status':'accepted', 'message':'PRIVATE-KEY'}}
        result = report(data, live=True)
        self.assertEqual(result['car']['soc'],86)
        self.assertEqual(result['session']['target_percent'],82)
        self.assertFalse(result['session']['stop_requested'])
        self.assertEqual(result['charger']['mode'],'M+')
        self.assertNotIn('PRIVATE',json.dumps(result))

    def test_only_finite_numbers_real_bools_and_known_states_are_exported(self):
        result=report({'skoda':{'soc':float('nan'), 'stale':'PRIVATE'},
                       'charging':{'status':'PRIVATE'}, 'smartfox':{'charger':{'mode':'PRIVATE'}}})
        self.assertIsNone(result['car']['soc'])
        self.assertIsNone(result['car']['stale'])
        self.assertIsNone(result['session']['status'])
        self.assertIsNone(result['charger']['mode'])

    def test_phase_report_keeps_verified_fields_and_bounded_wait_state(self):
        result=report({'skoda':{'state':'CHARGING_INTERRUPTED'},'smartfox':{'charger':{'info_code':6,'phase_switching':True,
                         'phase_count':3,'phase_switch_seconds':120,'ready_for_start':False}},
                       'charging':{'status':'starting','phase_start_pending':True,
                         'phase_start_deadline_epoch':1000,'stop_cause':'phase_wait',
                         'stop_trigger_soc':88,'stop_trigger_captured_at':'2026-10-17T11:41:00Z',
                         'stop_trigger_read_at':'2026-10-17T11:41:20Z','stop_trigger_at':'2026-10-17T11:41:20Z',
                         'target_probe_count':2}})
        self.assertEqual(result['charger']['info_code'],6)
        self.assertEqual(result['car']['state'],'CHARGING_INTERRUPTED')
        self.assertTrue(result['charger']['phase_switching'])
        self.assertEqual(result['charger']['phase_switch_seconds'],120)
        self.assertFalse(result['charger']['ready_for_start'])
        self.assertTrue(result['session']['phase_start_pending'])
        self.assertEqual(result['session']['phase_start_deadline_epoch'],1000)
        self.assertEqual(result['session']['stop_cause'],'phase_wait')
        self.assertEqual(result['session']['stop_trigger_soc'],88)
        self.assertEqual(result['session']['stop_trigger_captured_at'],'2026-10-17T11:41:00Z')
        self.assertEqual(result['session']['stop_trigger_read_at'],'2026-10-17T11:41:20Z')
        self.assertEqual(result['session']['target_probe_count'],2)

    def test_offline_report_reads_only_session_and_cached_vehicle(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)
            (path/'charging-session.json').write_text('{"target_percent":82,"status":"paused"}')
            (path/'vehicle-cache.json').write_text('{"vin":"PRIVATE","status":{"soc":86}}')
            (path/'config.json').write_text('Never read config, credentials or private keys')
            result=report(saved_status(path))
            self.assertEqual(result['session']['target_percent'],82)
            self.assertEqual(result['car']['soc'],86)
            self.assertEqual(result['source'],'saved_files_only')
            self.assertNotIn('PRIVATE',json.dumps(result))

    def test_malformed_saved_fields_do_not_crash_or_leak_into_report(self):
        result=report({'skoda': [], 'charging': {'status': ['PRIVATE']},
                       'smartfox': [], 'last_command': [], 'monitoring': []})
        self.assertIsNone(result['session']['status'])
        self.assertNotIn('PRIVATE',json.dumps(result))

    def test_cli_reads_old_backend_with_get_only_and_exports_no_raw_credentials(self):
        paths=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                paths.append(self.path)
                value={'/health':{'app':'CitiFoxCharger','version':'1.6.0'},
                       '/api/session':{'csrf':'PRIVATE-TOKEN'},
                       '/api/status':{'skoda':{'soc':86,'stale':True,'vin':'PRIVATE-VIN','quota_remaining':'2'},
                                      'charging':{'target_percent':82,'status':'paused'},
                                      'monitoring':{'running':True}}}[self.path]
                body=json.dumps(value).encode()
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                output=Path(folder)/'report.json'
                with patch('sys.argv',['diagnostics.py','--port',str(server.server_port),
                                       '--data-dir',folder,'--output',str(output)]):
                    self.assertEqual(main(),0)
                result=json.loads(output.read_text(encoding='utf-8'))
                self.assertEqual(result['backend_version'],'1.6.0')
                self.assertEqual(result['session']['target_percent'],82)
                self.assertEqual(result['car']['soc'],86)
                self.assertEqual(result['car']['quota_remaining'],2)
                self.assertEqual(result['source'],'running_backend')
                self.assertNotIn('PRIVATE',json.dumps(result))
                self.assertEqual(paths,['/health','/api/session','/api/status'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
