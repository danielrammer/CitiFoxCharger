"""Actual Apache TLS/auth/proxy tests on loopback, with an inert upstream only."""
import base64
import http.client
import importlib.util
import os
import json
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MODULES = Path('/usr/lib/httpd/modules')
ROOT = Path(__file__).resolve().parent.parent

if os.name == 'posix':
    spec = importlib.util.spec_from_file_location('isolated_activation', ROOT/'deploy/activate_omdev.py')
    activation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(activation)


@unittest.skipUnless(shutil.which('httpd') and (MODULES/'mod_ssl.so').exists(), 'Apache integration requires Linux httpd modules')
class ApacheGatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='citifox-apache-test-')
        cls.root = Path(cls.directory.name)
        for identity in ('localhost', 'wrong-host.example'):
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                '-keyout', str(cls.root/(identity+'.key')), '-out', str(cls.root/(identity+'.pem')),
                '-days', '1', '-subj', '/CN='+identity, '-addext', 'subjectAltName=IP:127.0.0.1',
                '-addext', 'basicConstraints=critical,CA:TRUE'],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        result = subprocess.run(['htpasswd', '-ni', '-B', '-C', '4', 'dnl'],
                                 input='synthetic-apache-tests-only\n', text=True, capture_output=True, check=True)
        (cls.root/'users.htpasswd').write_text(result.stdout)
        (cls.root/'gateway-header.conf').write_text('RequestHeader set X-CitiFox-Gateway "'+'a'*64+'"\n')
        cls.requests = []
        class Upstream(BaseHTTPRequestHandler):
            def do_GET(self):
                cls.requests.append(dict(self.headers))
                body = json.dumps({'inert': True}).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
            def log_message(self, *args):
                pass
        cls.upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        cls.upstream.handle_error = lambda *args: None
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cls.root/'localhost.pem', cls.root/'localhost.key')
        cls.upstream.socket = tls.wrap_socket(cls.upstream.socket, server_side=True)
        cls.thread = threading.Thread(target=cls.upstream.serve_forever, kwargs={'poll_interval': .02})
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.upstream.shutdown(); cls.upstream.server_close(); cls.thread.join(2)
        cls.directory.cleanup()

    def start_apache(self, trust='localhost', upstream_port=None):
        proxy = (ROOT/'deploy/citifox-proxy.inc.example').read_text()
        proxy = proxy.replace('https://citifoxcharger:8766/', f'https://localhost:{upstream_port or self.upstream.server_port}/')
        proxy = proxy.replace('/etc/httpd/citifox/tablet-ca.pem', str(self.root/(trust+'.pem')))
        proxy = proxy.replace('/etc/httpd/citifox/gateway-header.conf', str(self.root/'gateway-header.conf'))
        vhost = (ROOT/'deploy/citifox-tls-closed.conf').read_text()
        vhost = vhost.replace('/etc/letsencrypt/live/citifox.ancora.cyou/fullchain.pem', str(self.root/'localhost.pem'))
        vhost = vhost.replace('/etc/letsencrypt/live/citifox.ancora.cyou/privkey.pem', str(self.root/'localhost.key'))
        vhost = vhost.replace('/etc/httpd/citifox/users.htpasswd', str(self.root/'users.htpasswd'))
        vhost = vhost.replace('/srv/http/citifox-closed', str(self.root))
        vhost = vhost.replace('/var/log/httpd/citifox_access_log', str(self.root/'access.log'))
        vhost = vhost.replace('/var/log/httpd/citifox_error_log', str(self.root/'error.log'))
        vhost = vhost.replace('</VirtualHost>', proxy+'\n</VirtualHost>')
        tls = ssl.create_default_context(cafile=str(self.root/'localhost.pem'))
        def ready(port):
            code, _, headers = activation.request('127.0.0.1', port, '/', {'Host': 'citifox.ancora.cyou'}, tls=tls)
            return code == 401 and headers.get('X-CitiFox-Gateway-State') == 'holding'
        isolated = activation.isolated_apache(vhost, ready)
        self.port = isolated.__enter__()
        self.addCleanup(isolated.__exit__, None, None, None)

    def call(self, path='/', password='synthetic-apache-tests-only', extra=None):
        connection = http.client.HTTPSConnection('127.0.0.1', self.port,
            context=ssl.create_default_context(cafile=str(self.root/'localhost.pem')), timeout=5)
        headers = {'Host': 'citifox.ancora.cyou', 'Origin': 'https://citifox.ancora.cyou'}
        if password is not None:
            headers['Authorization'] = 'Basic '+base64.b64encode(('dnl:'+password).encode()).decode()
        headers.update(extra or {})
        connection.request('GET', path, headers=headers)
        response = connection.getresponse(); result = response.status; response.read(); connection.close()
        return result

    def test_all_resources_require_login_and_forwarded_identity_is_sanitized(self):
        self.start_apache()
        for path in ('/', '/app.js', '/api/session', '/api/status', '/api/diagnostics'):
            self.assertEqual(self.call(path, password=None), 401)
            self.assertEqual(self.call(path, password='wrong'), 401)
        self.requests.clear()
        self.assertEqual(self.call('/api/status', extra={'X-CitiFox-Gateway': 'forged', 'X-CitiFox-Public-Host': 'evil.example',
            'Forwarded': 'for=127.0.0.1', 'X-Forwarded-For': '127.0.0.1',
            'X-Forwarded-Host': 'localhost', 'X-Forwarded-Proto': 'http',
            'X-CSRF-Token': 'synthetic-csrf', 'Cookie': 'lz_public_session=synthetic'}), 200, (self.root/'error.log').read_text())
        forwarded = {k.lower(): v for k, v in self.requests[-1].items()}
        self.assertEqual(forwarded['host'], f'localhost:{self.upstream.server_port}')
        self.assertEqual(forwarded['x-citifox-public-host'], 'citifox.ancora.cyou')
        self.assertEqual(forwarded['origin'], 'https://citifox.ancora.cyou')
        self.assertEqual(forwarded['x-citifox-gateway'], 'a'*64)
        self.assertEqual(forwarded['x-csrf-token'], 'synthetic-csrf')
        self.assertEqual(forwarded['cookie'], 'lz_public_session=synthetic')
        for header in ('authorization', 'forwarded', 'x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto'):
            self.assertNotIn(header, forwarded)
        self.assertEqual(self.call('/api/app/update'), 403)
        self.assertEqual(self.call('/api/charging/limit-test/set'), 403)
        self.assertEqual(len(self.requests), 1)

    def test_wrong_upstream_ca_fails_without_delivering_application_request(self):
        self.start_apache(trust='wrong-host.example')
        self.requests.clear()
        self.assertEqual(self.call(), 500)
        self.assertEqual(self.requests, [])

    def test_trusted_certificate_with_wrong_hostname_fails(self):
        upstream = ThreadingHTTPServer(('127.0.0.1', 0), self.upstream.RequestHandlerClass)
        upstream.handle_error = lambda *args: None
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(self.root/'wrong-host.example.pem', self.root/'wrong-host.example.key')
        upstream.socket = tls.wrap_socket(upstream.socket, server_side=True)
        thread = threading.Thread(target=upstream.serve_forever, kwargs={'poll_interval': .02})
        thread.start()
        def stop():
            upstream.shutdown(); upstream.server_close(); thread.join(2)
        self.addCleanup(stop)
        self.start_apache(trust='wrong-host.example', upstream_port=upstream.server_port)
        self.requests.clear()
        self.assertEqual(self.call(), 500)
        self.assertEqual(self.requests, [])
