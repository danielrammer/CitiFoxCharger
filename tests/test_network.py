import base64
import copy
import http.client
import io
import json
import os
import ssl
import tempfile
import threading
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stdout
from ladezentrale.config import ConfigStore
from ladezentrale.network import NetworkAuth,ITERATIONS,password_record,private_host,read_network,tls_context,validate_network
from ladezentrale.secrets_store import SecretStore
from ladezentrale.server import LocalServer,make_server
from ladezentrale.service import Service
from test_service_server import FakeSmartfox

PASSWORD='only-for-fake-network-tests'


def credentials(password=PASSWORD):
    return 'Basic '+base64.b64encode(('citifox:'+password).encode('utf-8')).decode('ascii')


def configuration():
    return {'enabled':True,'host':'192.168.0.123','port':8766,**password_record(PASSWORD),
            'certificate_expires':(datetime.now(timezone.utc)+timedelta(days=730)).isoformat()}


class NetworkValidationTests(unittest.TestCase):
    def test_only_concrete_private_ipv4_and_hashed_long_password(self):
        self.assertEqual(private_host('192.168.0.123'),'192.168.0.123')
        for host in ('0.0.0.0','127.0.0.1','8.8.8.8','::','tablet.local'):
            with self.subTest(host=host),self.assertRaises(ValueError):private_host(host)
        for password in ('short','a'*129,'bad\npassword123'):
            with self.assertRaises(ValueError):password_record(password)
        config=configuration()
        self.assertNotIn(PASSWORD,json.dumps(config))
        self.assertEqual(validate_network(config),config)

    def test_bad_hash_port_and_expiry_fail_closed(self):
        config=configuration()
        for change in ({'password_hash':'x'*64},{'salt':'00'},{'iterations':1},{'port':80},{'port':True},
                       {'certificate_expires':'2000-01-01T00:00:00+00:00'},{'certificate_expires':'unknown'}):
            with self.subTest(change=change),self.assertRaises(ValueError):validate_network({**config,**change})

    def test_off_by_default_and_disabled_config_does_not_load_expired_certificate(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(read_network(directory))
            Path(directory,'network.json').write_text(json.dumps({'enabled':False}),encoding='utf-8')
            self.assertIsNone(read_network(directory))

    def test_password_required_and_repeated_failures_throttled_without_logging(self):
        config=configuration();clock=[100.0]
        auth=NetworkAuth(config,clock=lambda:clock[0])
        self.assertEqual(auth.check(None,'client'),401)
        for _ in range(5):self.assertEqual(auth.check(credentials('wrong-password'),'client'),401)
        self.assertEqual(auth.check(credentials(),'client'),429)
        clock[0]+=61
        self.assertEqual(auth.check(credentials(),'client'),200)
        self.assertEqual(auth.check(credentials(),'client'),200)
        self.assertNotIn(PASSWORD,str(auth.accepted))

    def test_unicode_password_and_malformed_basic_auth(self):
        auth=NetworkAuth(password_record('fuer-tests-äöü-123'))
        self.assertEqual(auth.check(credentials('fuer-tests-äöü-123'),'client'),200)
        for header in ('Bearer token','Basic ###','Basic '+base64.b64encode(b'no-colon').decode()):
            self.assertEqual(auth.check(header,'other'),401)

    def test_unprotected_network_bind_is_rejected_before_opening_a_socket(self):
        with self.assertRaises(ValueError):LocalServer(0,None,address='192.168.0.123')
        with self.assertRaises(ValueError):LocalServer(0,None,network_auth=NetworkAuth(configuration()))


@unittest.skipUnless(os.name=='nt','Native certificate setup is Windows-only')
class NetworkHttpsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from setup_network import create_certificate
        cls.material=tempfile.TemporaryDirectory()
        create_certificate(cls.material.name,'127.0.0.1')
        cls.tls=tls_context(cls.material.name)
        cls.client_tls=ssl.create_default_context()
        root=Path(cls.material.name,'network/CitiFoxCharger-Heimnetz.cer').read_bytes()
        cls.client_tls.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(root))

    @classmethod
    def tearDownClass(cls):cls.material.cleanup()

    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        config=ConfigStore(self.directory.name)
        self.service=Service(config,SecretStore(config.directory),FakeSmartfox())
        self.server=LocalServer(0,self.service,network_auth=NetworkAuth(configuration()),tls=self.tls)
        self.host=f'127.0.0.1:{self.server.server_port}'
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.02},daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2)
        self.service.stop_worker();self.directory.cleanup()

    def call(self,path,data=None,**override):
        connection=http.client.HTTPSConnection('127.0.0.1',self.server.server_port,context=self.client_tls,timeout=5)
        headers={'Host':self.host,'Authorization':credentials(),'Origin':'https://'+self.host,
                 'Cookie':'lz_session='+self.server.session,'X-CSRF-Token':self.server.csrf,'Content-Type':'application/json'}
        headers.update(override)
        connection.request('GET' if data is None else 'POST',path,json.dumps(data) if data is not None else None,headers)
        response=connection.getresponse();result=response.status,response.read(),dict(response.headers)
        connection.close();return result

    def test_real_tls_certificate_and_dpapi_key_are_valid_without_changing_windows_trust(self):
        self.assertEqual(self.call('/health')[0],200)
        self.assertTrue(Path(self.material.name,'network/server-key.dpapi').exists())
        self.assertFalse(list(Path(self.material.name).rglob('*.key.tmp')))
        self.assertNotIn(b'PRIVATE KEY',Path(self.material.name,'network/server-key.dpapi').read_bytes())

    def test_entire_site_requires_auth_even_static_session_and_health(self):
        for path in ('/','/app.js','/api/session','/health','/api/status'):
            status,body,headers=self.call(path,Authorization='')
            self.assertEqual(status,401)
            self.assertIn('WWW-Authenticate',headers)
            self.assertNotIn('csrf',body.decode())

    def test_https_cookie_origin_and_csrf_are_required(self):
        status,body,headers=self.call('/api/session')
        self.assertEqual(status,200)
        self.assertIn('Secure',headers['Set-Cookie'])
        for change in ({'Origin':'http://'+self.host},{'X-CSRF-Token':'wrong'},{'Host':'other.example'}):
            self.assertEqual(self.call('/api/refresh',{},**change)[0],403)

    def test_remote_vehicle_and_heat_controls_use_the_existing_single_service(self):
        with patch.object(self.service,'change_target',return_value={'target_percent':82}) as target:
            self.assertEqual(self.call('/api/charging/target',{'target_percent':82})[0],200)
            target.assert_called_once_with(82)
        with patch.object(self.service,'set_heater_mode',return_value={'status':'confirmed'}) as heater:
            self.assertEqual(self.call('/api/smartfox/heater',{'device':'heater2','mode':'auto','request_id':'remote-heater-test-001'})[0],200)
            heater.assert_called_once_with('heater2','auto','remote-heater-test-001')

    def test_remote_login_never_grants_key_configuration_or_backend_administration(self):
        for path in ('/api/key','/api/key/clear','/api/config','/api/app/quit','/api/app/restart','/api/skoda/command'):
            self.assertEqual(self.call(path,{})[0],403)
        config=json.loads(self.call('/api/config')[1])
        self.assertFalse(config['access']['local_admin'])
        self.assertNotIn('password_hash',json.dumps(config))

    def test_closing_remote_listener_does_not_stop_shared_monitor(self):
        with patch.object(self.service,'stop_worker') as stop:
            self.server.shutdown();self.server.server_close()
            stop.assert_not_called()

    def test_parent_closes_both_listeners_and_stops_shared_monitor_exactly_once(self):
        parent=LocalServer(0,self.service)
        parent.remote_server,parent.remote_thread=self.server,self.thread
        with patch.object(self.service,'stop_worker') as stop:
            parent.server_close()
            stop.assert_called_once_with()
        self.assertFalse(self.thread.is_alive())


class NetworkStartupTests(unittest.TestCase):
    def test_local_setup_stores_only_a_hash_and_requires_positive_listener_confirmation(self):
        import setup_network
        with tempfile.TemporaryDirectory() as directory:
            argv=['setup_network.py','--data-dir',directory]
            with patch('sys.argv',argv),patch.object(setup_network,'LocalControl') as control,patch('builtins.input',return_value='192.168.0.123'),patch.object(setup_network.getpass,'getpass',side_effect=[PASSWORD,PASSWORD]),patch.object(setup_network,'create_certificate',return_value=((datetime.now(timezone.utc)+timedelta(days=730)).isoformat(),'public-test-fingerprint')):
                control.return_value.health.return_value={'app':'CitiFoxCharger'}
                control.return_value.request.side_effect=[{}, {'key':{'configured':True,'persisted':True}}, {}, {'network':{'enabled':False,'message':'Test listener did not start'}}]
                control.return_value.action.return_value='restart confirmed'
                output=io.StringIO()
                with redirect_stdout(output):result=setup_network.main()
                self.assertEqual(result,1)
                self.assertIn('Test listener did not start',output.getvalue())
                self.assertNotIn(PASSWORD,output.getvalue())
                config=read_network(directory)
                self.assertTrue(config['enabled'])
                self.assertNotIn(PASSWORD,Path(directory,'network.json').read_text())

    def test_local_disable_updates_only_own_config_and_restarts_without_a_start_command(self):
        import setup_network
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory,'network.json');path.write_text(json.dumps(configuration()))
            with patch('sys.argv',['setup_network.py','--data-dir',directory,'--disable']),patch.object(setup_network,'LocalControl') as control:
                control.return_value.health.return_value={'app':'CitiFoxCharger'}
                control.return_value.request.side_effect=[{}, {'key':{'configured':True,'persisted':True}}, {}, {'network':{'enabled':False}}]
                control.return_value.action.return_value='restart confirmed'
                with redirect_stdout(io.StringIO()):self.assertEqual(setup_network.main(),0)
                self.assertIsNone(read_network(directory))
                control.return_value.action.assert_called_once_with('restart',Path(directory),8765,no_browser=True)

    def test_invalid_network_config_preserves_the_loopback_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory,'network.json').write_text('{}',encoding='utf-8')
            with patch.object(Service,'start_worker'),patch.object(Service,'stop_worker'):
                server=make_server(0,directory)
                try:
                    self.assertEqual(server.server_address[0],'127.0.0.1')
                    self.assertFalse(server.service.network['enabled'])
                    self.assertIn('Lokale Überwachung läuft weiter',server.service.network['message'])
                finally:server.server_close()


if __name__=='__main__':unittest.main()
