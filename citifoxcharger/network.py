"""Opt-in HTTPS on one private interface; independent from loopback administration."""
import base64
import hashlib
import hmac
import ipaddress
import json
import secrets
import ssl
import tempfile
import threading
import time
from pathlib import Path
from .secrets_store import dpapi

ITERATIONS = 600000
PRIVATE = tuple(ipaddress.ip_network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16'))


def private_host(value):
    try:
        address = ipaddress.IPv4Address(value)
        if any(address in network for network in PRIVATE):
            return str(address)
    except (ValueError,TypeError):
        pass
    raise ValueError('A private IPv4 address of this tablet is required.')


def password_record(password):
    if not isinstance(password,str) or not 12 <= len(password) <= 128 or any(ord(c)<32 for c in password):
        raise ValueError('Choose a separate network password of 12 to 128 characters.')
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),bytes.fromhex(salt),ITERATIONS).hex()
    return {'salt':salt,'password_hash':digest,'iterations':ITERATIONS}


def validate_network(value):
    required = {'enabled','host','port','salt','password_hash','iterations','certificate_expires'}
    if not isinstance(value,dict) or set(value) not in (required,required|{'authentication'}) or type(value['enabled']) is not bool:
        raise ValueError('Invalid network configuration.')
    value = dict(value)
    value['host'] = private_host(value['host'])
    if type(value['port']) is not int or not 1024 <= value['port'] <= 65535:
        raise ValueError('Invalid network port.')
    mode = value.get('authentication','password')  # Existing configurations remain password-protected.
    if mode == 'password':
        for key,length in (('salt',32),('password_hash',64)):
            data = value[key]
            if not isinstance(data,str) or len(data)!=length or any(c not in '0123456789abcdef' for c in data):
                raise ValueError('Invalid network authentication.')
        if value['iterations'] != ITERATIONS:
            raise ValueError('Invalid network authentication.')
    elif mode != 'none' or value['salt'] is not None or value['password_hash'] is not None or type(value['iterations']) is not int or value['iterations'] != 0:
        raise ValueError('Invalid network authentication.')
    if type(value['certificate_expires']) is not str:
        raise ValueError('Invalid network authentication or certificate time.')
    try:
        from datetime import datetime
        expiry = datetime.fromisoformat(value['certificate_expires'])
        if expiry.tzinfo is None or expiry.timestamp() <= time.time():
            raise ValueError()
    except ValueError:
        raise ValueError('The network certificate expired. Run Setup-Network.cmd again.') from None
    return value


def read_network(directory):
    path = Path(directory)/'network.json'
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding='utf-8'))
    if isinstance(value,dict) and value.get('enabled') is False:return None
    value = validate_network(value)
    return value if value['enabled'] else None


def tls_context(directory):
    directory = Path(directory)/'network'
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    # The persistent private key is user-bound DPAPI. SSL retains the loaded key in memory.
    decrypted = dpapi((directory/'server-key.dpapi').read_bytes(),decrypt=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory,suffix='.key.tmp',delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(decrypted)
        context.load_cert_chain(directory/'server.pem',temporary)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return context


class NetworkAuth:
    def __init__(self,config,clock=None):
        self.config,self.clock = config,clock or time.monotonic
        self.failures,self.accepted,self.lock = {},{},threading.Lock()

    def check(self,header,client):
        """Return 200, 401 or 429. No credentials are logged or returned."""
        if self.config.get('authentication','password') == 'none':
            return 200
        if not header:
            return 401
        now = self.clock()
        with self.lock:
            self.failures = {k:v for k,v in self.failures.items() if now-v[0]<60}
            self.accepted = {k:v for k,v in self.accepted.items() if now-v<900}
            if self.failures.get(client,(0,0))[1]>=5:
                return 429
            fingerprint = hashlib.sha256(header.encode('utf-8')).digest()
            cache_key = (client,fingerprint)
            if cache_key in self.accepted:
                return 200
            valid = False
            if len(header)<=2048 and header.startswith('Basic '):
                try:
                    username,password = base64.b64decode(header[6:],validate=True).decode('utf-8').split(':',1)
                    if username=='citifox' and 12<=len(password)<=128:
                        actual = hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),bytes.fromhex(self.config['salt']),ITERATIONS).hex()
                        valid = hmac.compare_digest(actual,self.config['password_hash'])
                except (ValueError,UnicodeError):
                    pass
            if valid:
                if len(self.accepted)>=128:self.accepted.pop(next(iter(self.accepted)))
                self.accepted[cache_key] = now
                self.failures.pop(client,None)
                return 200
            if client not in self.failures and len(self.failures)>=256:
                self.failures.pop(next(iter(self.failures)))
            began,count = self.failures.get(client,(now,0))
            self.failures[client] = (began,count+1)
            return 401
