"""Activate only after read-only/negative security checks; never switch real devices."""
import argparse
import base64
import fcntl
import getpass
import hashlib
import hmac
import http.client
import json
import os
import pwd
import re
import shlex
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from citifoxcharger.public_gateway import PublicGateway

DOMAIN = 'citifox.ancora.cyou'
TABLET = '192.168.0.95'
TRANSFER = ROOT.parent / 'gateway-transfer'
BASE = Path('/etc/httpd/citifox')
MAIN = Path('/etc/httpd/conf/httpd.conf')
VHOST = Path('/etc/httpd/conf/extra/citifox-tls-closed.conf')
VALIDATION = Path('/etc/httpd/conf/extra/citifox-validation.conf')
HOSTS = Path('/etc/hosts')
CA = BASE / 'tablet-ca.pem'
ORIGIN = 'https://' + DOMAIN


def ensure(condition, message):
    if not condition:
        raise ValueError(message)


def proxy_is_active(configuration):
    return re.search(rb'^\s*ProxyPass(?:Match)?\s+', configuration, re.MULTILINE | re.IGNORECASE) is not None


def command(*args):
    subprocess.run(args, check=True)


def write(path, data, mode=0o644):
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    path.chmod(mode)


def reload_apache():
    command('/usr/bin/httpd', '-t')
    command('/usr/bin/systemctl', 'reload', 'httpd')


def request(address, port, path, headers=None, data=None, *, tls=None, server_name=None):
    # Avoid proxy environment variables and verify every TLS connection.
    with socket.create_connection((address, port), timeout=8) as raw:
        with (tls or ssl.create_default_context()).wrap_socket(raw, server_hostname=server_name or address) as sock:
            connection = http.client.HTTPConnection(address, port, timeout=8)
            connection.sock = sock
            connection.request('GET' if data is None else 'POST', path,
                               body=json.dumps(data) if data is not None else None, headers=headers or {})
            response = connection.getresponse()
            body = response.read(2 * 1024 * 1024)
            result = response.status, body, dict(response.headers)
            connection.close()
            return result


def session(call, headers):
    code, body, returned = call('/api/session', headers)
    ensure(code == 200, 'Session bootstrap failed. Tablet gateway profile may be missing or not loaded; keep forwarding disabled.')
    cookie = next((value for name, value in returned.items() if name.lower() == 'set-cookie'), '')
    return {**headers, 'Cookie': cookie.split(';', 1)[0], 'X-CSRF-Token': json.loads(body)['csrf']}


def validate_public(call, base):
    for path in ('/', '/app.js', '/i18n.js', '/style.css', '/health'):
        ensure(call(path, base)[0] == 200, 'Public resource check failed: ' + path)
    authenticated = session(call, base)
    for path in ('/api/config', '/api/status', '/api/diagnostics', '/api/diagnostics/download'):
        ensure(call(path, authenticated)[0] == 200, 'Public read check failed: ' + path)
    access = json.loads(call('/api/config', authenticated)[1])['access']
    ensure(access.get('public_access') is True and access.get('local_admin') is False
           and access.get('can_update') is False and access.get('can_limit_test') is False,
           'Public permission boundaries do not match the required policy.')
    for path in ('/api/app/update/status', '/api/network/certificate', '/api/future'):
        ensure(call(path, authenticated)[0] == 403, 'Public read restriction failed: ' + path)
    ensure(call('/api/status', {**base, 'Cookie': ''})[0] == 401, 'A browser session must be mandatory.')
    ensure(call('/api/status', {**authenticated, 'Origin': 'https://invalid.example'})[0] == 403,
           'Foreign Origin was not rejected.')
    ensure(call('/api/session', {**base, 'Sec-Fetch-Site': 'cross-site'})[0] == 403,
           'Cross-site session bootstrap was not rejected.')
    # Empty config cannot alter device settings even if a regression were to bypass a gate.
    for csrf in ('', 'invalid-csrf'):
        ensure(call('/api/config', {**authenticated, 'X-CSRF-Token': csrf}, {})[0] == 403,
               'Missing/invalid CSRF was not rejected.')
    ensure(call('/api/config', authenticated, {})[0] == 403, 'Public configuration mutation was not denied.')
    # Nonempty data cannot restart/quit/update even if a route restriction regressed.
    for path in ('/api/app/update', '/api/app/restart', '/api/app/quit'):
        ensure(call(path, authenticated, {'invalid_test_payload': True})[0] == 403,
               'Public process/update mutation was not denied: ' + path)
    return authenticated


def snapshot(paths):
    return {path: (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None for path in paths}


def restore(saved):
    for path, original in saved.items():
        if original is None:
            path.unlink(missing_ok=True)
        else:
            write(path, *original)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--disable', action='store_true', help='Restore holding site only; leave tablet and Maya running')
    args = parser.parse_args()
    ensure(os.geteuid() == 0, 'Run locally with sudo. No passwords or assertions belong in command arguments.')
    ensure(args.disable or os.isatty(0), 'Activation requires a local interactive terminal for the public password.')
    with Path('/run/lock/citifox-activation.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ensure(VHOST.exists(), 'Expected prepared CitiFox vhost is missing.')
        before = snapshot((MAIN, HOSTS, VHOST, CA, VALIDATION))
        backup = Path('/var/backups') / ('citifox-activation-' + time.strftime('%Y%m%d-%H%M%S'))
        backup.mkdir(mode=0o700)
        for path, original in before.items():
            if original:
                write(backup / path.name, original[0], 0o600)
        if args.disable:
            write(VHOST, (ROOT/'deploy/citifox-tls-closed.conf').read_bytes())
            try:
                reload_apache()
            except Exception:
                restore(before); reload_apache(); raise
            (TRANSFER/'ACTIVATED').unlink(missing_ok=True)
            print('Public application access DISABLED. Tablet monitoring, LAN and Maya were not restarted.')
            return
        ensure(not VALIDATION.exists(), 'A validation vhost already exists; inspect before proceeding.')
        ensure(not proxy_is_active(before[VHOST][0]), 'Public proxy is already active; inspect instead of overwriting it.')
        configuration = json.loads((TRANSFER/'public-gateway.json').read_text())
        gateway = PublicGateway(configuration)
        ensure(gateway.origin == ORIGIN and gateway.proxy_ip == '192.168.0.98', 'Unexpected gateway origin or proxy IP.')
        tokens = []
        for line in (BASE/'gateway-header.conf').read_text().splitlines():
            words = shlex.split(line, comments=True)
            if words:
                ensure(len(words) == 4 and words[:3] == ['RequestHeader', 'set', 'X-CitiFox-Gateway'],
                       'Unexpected secret include format.')
                tokens.append(words[3])
        ensure(len(tokens) == 1 and gateway.accepts('192.168.0.98', DOMAIN, tokens[0]),
               'Gateway assertion and transfer configuration do not match.')
        token = tokens[0]
        tls = ssl.create_default_context(cafile=str(TRANSFER/'tablet-current-ca.pem'))
        def upstream(path, headers=None, data=None):
            return request(TABLET, 8766, path, headers, data, tls=tls)
        lan = {'Host': TABLET + ':8766', 'Origin': 'https://' + TABLET + ':8766'}
        health = json.loads(upstream('/health', lan)[1])
        ensure(health.get('app') == 'CitiFoxCharger', 'Unexpected upstream application.')
        lan_session = session(upstream, lan)
        lan_config = json.loads(upstream('/api/config', lan_session)[1])
        ensure(lan_config.get('network', {}).get('authentication') == 'none', 'LAN access must remain passwordless.')
        assertion = {'Host': 'citifoxcharger:8766', 'Origin': ORIGIN,
                     'X-CitiFox-Public-Host': DOMAIN, 'X-CitiFox-Gateway': token}
        validate_public(upstream, assertion)
        ensure(upstream('/api/session', {**assertion, 'X-CitiFox-Gateway': ''})[0] == 403,
               'A missing gateway assertion was accepted.')
        ensure(upstream('/api/session', {**assertion, 'X-CitiFox-Public-Host': 'invalid.example'})[0] == 403,
               'An incorrect public authority was accepted.')
        ensure(upstream('/api/session', {**assertion, 'Host': 'invalid.example'})[0] == 403,
               'An incorrect transport Host was accepted.')
        print('PASS: tablet TLS, passwordless LAN and public permissions/origin/session/CSRF. No device was switched.')
        password = getpass.getpass('Existing PUBLIC CitiFox password for dnl (not the sudo password): ')
        verified = subprocess.run(['/usr/bin/htpasswd', '-vi', str(BASE/'users.htpasswd'), 'dnl'],
                                  input=password+'\n', text=True, capture_output=True)
        ensure(verified.returncode == 0, 'Public password did not match; no forwarding configuration was changed.')
        hosts = before[HOSTS][0].decode()
        for line in hosts.splitlines():
            words = line.split('#', 1)[0].split()
            if any(name.lower() == 'citifoxcharger' for name in words[1:]):
                ensure(words[0] == TABLET, 'Conflicting tablet alias in /etc/hosts.')
        alias = '\n192.168.0.95 citifoxcharger\n' if not any('citifoxcharger' in line.split('#', 1)[0].lower().split()[1:] for line in hosts.splitlines()) else ''
        candidate = (ROOT/'deploy/citifox-tls-closed.conf').read_text()
        candidate = candidate.replace('</VirtualHost>', (ROOT/'deploy/citifox-proxy.inc.example').read_text()+'\n</VirtualHost>')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
        test_config = f'Listen 127.0.0.1:{port}\n' + candidate.replace('*:443', f'127.0.0.1:{port}')
        try:
            write(CA, (TRANSFER/'tablet-current-ca.pem').read_bytes())
            write(HOSTS, hosts + alias, before[HOSTS][1])
            addresses = {item[4][0] for item in socket.getaddrinfo('citifoxcharger', 8766)}
            ensure(addresses == {TABLET}, 'Tablet alias did not resolve exclusively to the configured tablet IP.')
            write(VALIDATION, test_config)
            write(MAIN, before[MAIN][0] + b'\nInclude conf/extra/citifox-validation.conf\n', before[MAIN][1])
            reload_apache()
            basic = 'Basic ' + base64.b64encode(('dnl:'+password).encode()).decode()
            def local_call(test_port):
                return lambda path, headers=None, data=None: request('127.0.0.1', test_port, path, headers, data, server_name=DOMAIN)
            public = {'Host': DOMAIN, 'Origin': ORIGIN, 'Content-Type': 'application/json', 'Authorization': basic}
            test = local_call(port)
            for path in ('/', '/app.js', '/api/session', '/api/status', '/api/diagnostics'):
                ensure(test(path, {**public, 'Authorization': ''})[0] == 401, 'Missing public login was accepted.')
                wrong = 'Basic ' + base64.b64encode(b'dnl:invalid-public-test-password').decode()
                ensure(test(path, {**public, 'Authorization': wrong})[0] == 401, 'Wrong public login was accepted.')
            validate_public(test, public)
            print('PASS: loopback Apache password protection and strict upstream TLS. Enabling public vhost.')
            write(VHOST, candidate, before[VHOST][1])
            write(MAIN, before[MAIN][0], before[MAIN][1])
            VALIDATION.unlink()
            reload_apache()
            validate_public(local_call(443), public)
            ensure(local_call(443)('/', {'Host': DOMAIN})[0] == 401, 'Live public login guard failed.')
            ensure(upstream('/health', lan)[0] == 200, 'LAN endpoint stopped responding.')
            maya = request('127.0.0.1', 443, '/', {'Host': 'maya.ancora.cyou'}, server_name='maya.ancora.cyou')
            ensure(maya[0] == 401 and 'Maya Player' in str(maya[2]), 'Existing Maya website changed unexpectedly.')
            account = pwd.getpwnam('dnl')
            marker = TRANSFER/'ACTIVATED'
            write(marker, 'Public proxy activated after local security checks. External mobile-data test remains required.\n')
            os.chown(marker, account.pw_uid, account.pw_gid)
            print('ACTIVATED: https://' + DOMAIN + '/. Maya and tablet LAN preserved. External HTTPS still requires a mobile-data test.')
            print('Rollback: sudo python ' + str(Path(__file__).resolve()) + ' --disable')
        except Exception:
            restore(before)
            reload_apache()
            print('Activation failed. Original configuration restored; public forwarding remains closed.')
            raise


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError, KeyError, TypeError) as error:
        # Do not print captured subprocess output, request headers, bodies or tokens.
        print('Setup stopped: ' + (str(error) if isinstance(error, ValueError) else type(error).__name__))
        sys.exit(1)
