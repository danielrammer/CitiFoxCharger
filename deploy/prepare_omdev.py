"""Privileged, interactive preparation only. Never enables the tablet proxy."""
import base64
import getpass
import hashlib
import hmac
import http.client
import json
import os
import pwd
import secrets
import shutil
import ssl
import subprocess
import time
from pathlib import Path

DOMAIN = 'citifox.ancora.cyou'
BASE = Path('/etc/httpd/citifox')
MAIN = Path('/etc/httpd/conf/httpd.conf')
HTTP = Path('/etc/httpd/conf/extra/citifox-closed.conf')
HTTPS = Path('/etc/httpd/conf/extra/citifox-tls-closed.conf')
TEMPLATES = Path(__file__).resolve().parent
TRANSFER = TEMPLATES.parent.parent / 'gateway-transfer'


def run(*args):
    subprocess.run(args, check=True)


def write(path, data, mode=0o644):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(data)
    os.chmod(path, mode)


def reload_apache():
    run('/usr/bin/httpd', '-t')
    run('/usr/bin/systemctl', 'reload', 'httpd')


def check(password):
    # Local TLS endpoint; still verify the real public certificate and hostname.
    tls = ssl.create_default_context()
    import socket
    def request(path, secret):
        with socket.create_connection(('127.0.0.1', 443), timeout=5) as raw:
            with tls.wrap_socket(raw, server_hostname=DOMAIN) as sock:
                authorization = '' if secret is None else 'Authorization: Basic ' + base64.b64encode(('citifox:' + secret).encode()).decode() + '\r\n'
                sock.sendall(('GET ' + path + ' HTTP/1.1\r\nHost: ' + DOMAIN + '\r\n' + authorization + 'Connection: close\r\n\r\n').encode())
                response = http.client.HTTPResponse(sock)
                response.begin()
                code = response.status
                response.read()
                return code
    for path in ('/', '/app.js', '/api/session', '/api/status', '/api/diagnostics'):
        assert request(path, None) == 401, 'Missing-login check failed.'
        assert request(path, secrets.token_hex(24)) == 401, 'Wrong-login check failed.'
    assert request('/', password) == 200, 'Correct-login check failed.'
    print('PASS: trusted public TLS; missing/wrong login denied on pages, assets and APIs; correct login accepted.')


def main():
    if os.geteuid() != 0 or not os.isatty(0):
        raise SystemExit('Run locally in a visible terminal using sudo; never enter passwords in chat.')
    if BASE.exists() or HTTP.exists() or HTTPS.exists():
        raise SystemExit('CitiFox preparation already exists. Inspect it before making further changes.')
    original = MAIN.read_text()
    if 'Include conf/extra/maya.conf' not in original:
        raise SystemExit('Expected Maya include absent; inspect existing vhosts first.')
    run('/usr/bin/httpd', '-t')
    backup = Path('/var/backups') / ('citifox-' + time.strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True, mode=0o700)
    os.chmod(backup, 0o700)
    shutil.copy2(MAIN, backup/'httpd.conf')
    shutil.copy2('/etc/httpd/conf/extra/maya.conf', backup/'maya.conf')
    BASE.mkdir(mode=0o750)
    http_gid = __import__('grp').getgrnam('http').gr_gid
    os.chown(BASE, 0, http_gid)
    print('Choose the PUBLIC CitiFox password (12 to 72 UTF-8 bytes). It stays on this PC.')
    while True:
        password = getpass.getpass('Public password: ')
        repeated = getpass.getpass('Repeat public password: ')
        if 12 <= len(password) and len(password.encode()) <= 72 and all(ord(c) >= 32 for c in password) and hmac.compare_digest(password.encode(), repeated.encode()):
            break
        print('Passwords must match and contain 12 or more printable characters (at most 72 UTF-8 bytes).')
    del repeated
    result = subprocess.run(['/usr/bin/htpasswd', '-i', '-B', '-C', '12', '-c', str(BASE/'users.htpasswd'), 'citifox'],
                            input=password + '\n', text=True, capture_output=True)
    if result.returncode:
        raise SystemExit('Password hashing failed; no website has been changed.')
    os.chmod(BASE/'users.htpasswd', 0o640)
    os.chown(BASE/'users.htpasswd', 0, http_gid)
    token = secrets.token_hex(32)
    write(BASE/'gateway-header.conf', 'RequestHeader set X-CitiFox-Gateway "' + token + '"\n', 0o640)
    os.chown(BASE/'gateway-header.conf', 0, http_gid)
    account = pwd.getpwnam('dnl')
    TRANSFER.mkdir(mode=0o700)
    os.chown(TRANSFER, account.pw_uid, account.pw_gid)
    write(TRANSFER/'public-gateway.json', json.dumps({'origin': 'https://' + DOMAIN,
          'proxy_ip': '192.168.0.98', 'token_sha256': hashlib.sha256(token.encode()).hexdigest()}, indent=2) + '\n', 0o600)
    os.chown(TRANSFER/'public-gateway.json', account.pw_uid, account.pw_gid)
    del token
    challenge = Path('/srv/http/citifox-acme/.well-known/acme-challenge')
    challenge.mkdir(parents=True, mode=0o755)
    closed = Path('/srv/http/citifox-closed')
    closed.mkdir(mode=0o755)
    write(closed/'index.html', '<!doctype html><title>CitiFoxCharger</title><p>Public access is awaiting tablet validation.</p>\n')
    write(HTTP, (TEMPLATES/'citifox-closed.conf').read_text())
    write(MAIN, original + '\n# CitiFox is intentionally after Maya; preserve the existing default host.\nInclude conf/extra/citifox-closed.conf\n')
    try:
        reload_apache()
    except subprocess.CalledProcessError:
        shutil.copy2(backup/'httpd.conf', MAIN)
        raise
    print('Obtaining CitiFox certificate using existing Certbot account; Maya remains active.')
    run('/usr/bin/certbot', 'certonly', '--webroot', '-w', '/srv/http/citifox-acme',
        '-d', DOMAIN, '--cert-name', DOMAIN, '--non-interactive')
    write(HTTPS, (TEMPLATES/'citifox-tls-closed.conf').read_text())
    write(MAIN, MAIN.read_text() + 'Include conf/extra/citifox-tls-closed.conf\n')
    try:
        reload_apache()
    except subprocess.CalledProcessError:
        write(MAIN, MAIN.read_text().replace('Include conf/extra/citifox-tls-closed.conf\n', ''))
        raise
    check(password)
    del password
    run('/usr/bin/systemctl', 'is-enabled', 'httpd', 'maya-cert-renew.timer')
    run('/usr/bin/certbot', 'renew', '--cert-name', DOMAIN, '--dry-run')
    write(TRANSFER/'PREPARED', 'Holding site authenticated and verified; tablet proxy remains DISABLED.\n')
    os.chown(TRANSFER/'PREPARED', account.pw_uid, account.pw_gid)
    print('Prepared. Tablet proxy is DISABLED. Config backups: ' + str(backup))
    print('Transfer only gateway-transfer/public-gateway.json to the active tablet profile.')


if __name__ == '__main__':
    main()
