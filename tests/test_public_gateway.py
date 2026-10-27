"""Real TLS requests, simulated device methods only; no workers or device networking."""
import hashlib
import http.client
import json
import os
import ssl
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from citifoxcharger.network import NetworkAuth
from citifoxcharger.public_gateway import PublicGateway, PUBLIC_ACTIONS, read_public_gateway
from citifoxcharger.server import LocalServer, FILES

# Deliberately synthetic test assertion, never a deployed credential.
TOKEN = 'a' * 64
ORIGIN = 'https://citifox.example.test'
VALUE = {'origin': ORIGIN, 'proxy_ip': '192.168.0.98',
         'token_sha256': hashlib.sha256(TOKEN.encode()).hexdigest()}


class GatewayPolicyTests(unittest.TestCase):
    def test_exact_peer_host_and_assertion_required(self):
        gateway = PublicGateway(VALUE)
        self.assertTrue(gateway.accepts('192.168.0.98', 'citifox.example.test', TOKEN))
        for peer, host, token in [('192.168.0.99', gateway.host, TOKEN),
                                  ('192.168.0.98', '192.168.0.95:8766', TOKEN),
                                  ('192.168.0.98', gateway.host, ''),
                                  ('192.168.0.98', gateway.host, 'b' * 64)]:
            self.assertFalse(gateway.accepts(peer, host, token))

    def test_configuration_is_opt_in_and_invalid_values_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(read_public_gateway(directory))
            for change in ({'origin': 'http://public.example'}, {'origin': ORIGIN + '/'},
                           {'origin': ORIGIN + ':443'}, {'origin': 'https://user@public.example'},
                           {'origin': None}, {'proxy_ip': '0.0.0.0'}, {'proxy_ip': '127.0.0.1'},
                           {'token_sha256': ''}, {'trusted_forwarded_headers': True}):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    PublicGateway({**VALUE, **change})
            Path(directory, 'public-gateway.json').write_text(json.dumps(VALUE))
            self.assertEqual(read_public_gateway(directory).origin, ORIGIN)
        with self.assertRaises(ValueError):
            LocalServer(0, None, public_gateway=PublicGateway(VALUE))


class GatewayHttpsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.material = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.material.cleanup)
        root = Path(cls.material.name)
        if os.name == 'nt':
            # Use the same native, disposable certificate helper as the LAN tests.
            # This never changes the Windows trust store and needs no OpenSSL CLI.
            from setup_network import create_certificate
            from citifoxcharger.network import tls_context, PUBLIC_CERTIFICATE
            create_certificate(root, '127.0.0.1')
            cls.tls = tls_context(root)
            cls.client_tls = ssl.create_default_context(cadata=ssl.DER_cert_to_PEM_cert(
                (root/'network'/PUBLIC_CERTIFICATE).read_bytes()))
            return
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(root/'key.pem'), '-out', str(root/'cert.pem'),
                        '-days', '1', '-subj', '/CN=127.0.0.1',
                        '-addext', 'subjectAltName=IP:127.0.0.1'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.tls.load_cert_chain(root/'cert.pem', root/'key.pem')
        cls.client_tls = ssl.create_default_context(cafile=str(root/'cert.pem'))

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.service = SimpleNamespace(lock=threading.RLock(), app_update_pending=False,
            config=SimpleNamespace(directory=Path(self.directory.name), value={}), network={},
            secrets=SimpleNamespace(status=lambda: {}),
            status=Mock(return_value={'cached': True}), diagnostic_snapshot=Mock(return_value={}),
            refresh_now=Mock(return_value={'message': 'simulated refresh'}),
            start_manual_charging=Mock(return_value={'message': 'simulated start'}),
            stop_charge=Mock(return_value={'message': 'simulated stop'}),
            change_target=Mock(return_value={'message': 'simulated target'}),
            set_charger_mode=Mock(return_value={'status': 'confirmed'}),
            set_heater_mode=Mock(return_value={'status': 'confirmed'}))
        self.updater = SimpleNamespace(state=lambda: {'phase': 'idle'}, launch=Mock())
        gateway = PublicGateway(VALUE)
        # TLS integration fixture only: the production validator rejects loopback proxy IPs.
        gateway.proxy_ip = '127.0.0.1'
        self.server = LocalServer(0, self.service, network_auth=NetworkAuth({'authentication': 'none'}),
                                  tls=self.tls, public_gateway=gateway, updater=self.updater)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01})
        self.thread.start()
        self.lan_host = f'127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(2)
        self.directory.cleanup()

    def call(self, path, data=None, lan=False, extra=None):
        host = self.lan_host if lan else 'citifox.example.test'
        headers = {'Host': host, 'Origin': ('https://' + host), 'Content-Type': 'application/json',
                   'Cookie': ('lz_session=' + self.server.session if lan else
                              'lz_public_session=' + self.server.public_session),
                   'X-CSRF-Token': self.server.csrf if lan else self.server.public_csrf}
        if not lan:
            headers['X-CitiFox-Gateway'] = TOKEN
        headers.update(extra or {})
        connection = http.client.HTTPSConnection('127.0.0.1', self.server.server_port,
                                                 context=self.client_tls, timeout=3)
        connection.request('GET' if data is None else 'POST', path,
                           json.dumps(data) if data is not None else None, headers)
        response = connection.getresponse()
        result = response.status, response.read(), dict(response.headers)
        connection.close()
        return result

    def test_public_assertion_required_on_every_static_and_api_request(self):
        for path in (*FILES, '/health', '/api/session', '/api/status', '/api/diagnostics'):
            for assertion in ('', 'b' * 64):
                with self.subTest(path=path, assertion=assertion[:1]):
                    self.assertEqual(self.call(path, extra={'X-CitiFox-Gateway': assertion})[0], 403)
            self.assertEqual(self.call(path)[0], 200)
        self.assertEqual(self.call('/api/status', extra={'X-Forwarded-For': '127.0.0.1',
                         'X-Forwarded-Host': self.lan_host, 'X-Forwarded-Proto': 'http'})[0], 200)
        access = json.loads(self.call('/api/config')[1])['access']
        self.assertEqual(access, {'local_admin': False, 'public_access': True,
                                 'can_update': False, 'can_limit_test': False})
        self.assertEqual(self.call('/', lan=True, extra={'X-CitiFox-Gateway': TOKEN})[0], 403)

    def test_separate_public_session_and_csrf_no_lan_token_reuse(self):
        code, body, headers = self.call('/api/session', extra={'Cookie': ''})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)['csrf'], self.server.public_csrf)
        self.assertIn('lz_public_session=', headers['Set-Cookie'])
        for flag in ('Secure', 'HttpOnly', 'SameSite=Strict'):
            self.assertIn(flag, headers['Set-Cookie'])
        self.assertEqual(self.call('/api/status', extra={'Cookie': ''})[0], 401)
        self.assertEqual(self.call('/api/status', extra={'Cookie': 'lz_session=' + self.server.session})[0], 401)
        for token in ('', 'wrong', self.server.csrf):
            self.assertEqual(self.call('/api/refresh', {}, extra={'X-CSRF-Token': token})[0], 403)
        self.service.refresh_now.assert_not_called()
        self.assertEqual(self.call('/api/refresh', {})[0], 200)
        self.service.refresh_now.assert_called_once()

    def test_apache_transport_authority_needs_assertion_peer_and_exact_public_host(self):
        translated = {'Host': f'citifoxcharger:{self.server.server_port}',
                      'X-CitiFox-Public-Host': 'citifox.example.test'}
        self.assertEqual(self.call('/api/status', extra=translated)[0], 200)
        self.assertEqual(self.call('/api/refresh', {}, extra=translated)[0], 200)
        for change in ({'X-CitiFox-Gateway': ''}, {'X-CitiFox-Public-Host': 'evil.example'},
                       {'Host': self.lan_host}, {'Host': 'citifoxcharger:1'},
                       {'Origin': 'https://citifoxcharger'}, {'X-CSRF-Token': ''}):
            with self.subTest(change=change):
                self.assertEqual(self.call('/api/refresh', {}, extra={**translated, **change})[0], 403)
        self.server.public_gateway.proxy_ip = '192.168.0.98'
        self.assertEqual(self.call('/api/status', extra=translated)[0], 403)

    def test_origin_host_and_fetch_metadata_checks_survive_proxy(self):
        for origin in ('https://evil.example', 'https://' + self.lan_host, 'null', ''):
            self.assertEqual(self.call('/api/refresh', {}, extra={'Origin': origin})[0], 403)
        self.assertEqual(self.call('/api/status', extra={'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.call('/api/status', extra={'Host': 'evil.example'})[0], 403)
        navigation = {'Origin': '', 'Sec-Fetch-Site': 'cross-site',
                      'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document'}
        self.assertEqual(self.call('/', extra=navigation)[0], 200)
        self.assertEqual(self.call('/api/session', extra=navigation)[0], 403)
        self.assertEqual(self.call('/api/refresh', {}, extra=navigation)[0], 403)
        self.service.refresh_now.assert_not_called()

    def test_public_dangerous_routes_denied_before_any_side_effect(self):
        for path in ('/api/app/update', '/api/app/restart', '/api/app/quit', '/api/config',
                     '/api/key', '/api/key/clear', '/api/skoda/command', '/api/smartfox/command',
                     '/api/charging/limit-test/set', '/api/charging/limit-test/end',
                     '/api/charging/limit-test/start-trial', '/api/charging/release-limit-guard',
                     '/api/charging/confirm-restored-limit', '/api/charging/restore-limit', '/api/solar/location', '/api/future'):
            with self.subTest(path=path):
                self.assertEqual(self.call(path, {})[0], 403)
        self.assertEqual(self.call('/api/app/update/status')[0], 403)
        self.assertEqual(self.call('/api/network/certificate')[0], 403)
        self.assertFalse(json.loads(self.call('/api/config')[1])['network']['certificate_available'])
        self.updater.launch.assert_not_called()

    def test_public_operations_use_existing_tablet_service_simulated_only(self):
        request_id = 'simulated-request-0001'
        cases = [('/api/refresh', {}, 'refresh_now'),
                 ('/api/charging/start', {'request_id': request_id, 'target_percent': 80}, 'start_manual_charging'),
                 ('/api/charging/stop', {'request_id': request_id}, 'stop_charge'),
                 ('/api/charging/target', {'target_percent': 75}, 'change_target'),
                 ('/api/smartfox/heater', {'request_id': request_id, 'device': 'heater', 'mode': 'OFF'}, 'set_heater_mode'),
                 ('/api/smartfox/charger-mode', {'request_id': request_id, 'mode': 'OFF'}, 'set_charger_mode')]
        self.assertEqual({case[0] for case in cases}, PUBLIC_ACTIONS)
        for path, data, method in cases:
            self.assertEqual(self.call(path, data)[0], 200)
            getattr(self.service, method).assert_called_once()

    def test_direct_lan_stays_passwordless_and_retains_permissions(self):
        for path in (*FILES, '/api/session', '/health', '/api/app/update/status'):
            self.assertEqual(self.call(path, lan=True)[0], 200)
        access = json.loads(self.call('/api/config', lan=True)[1])['access']
        self.assertFalse(access['local_admin'])
        self.assertTrue(access['can_update'])
        self.assertTrue(access['can_limit_test'])
        self.assertFalse(access['public_access'])
        self.assertEqual(self.call('/api/status', lan=True, extra={'Cookie': ''})[0], 401)
        self.assertEqual(self.call('/api/key', {}, lan=True)[0], 403)
