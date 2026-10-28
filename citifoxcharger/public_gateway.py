"""Explicit, opt-in gateway assertion; never trust forwarded client/role headers."""
import hashlib
import hmac
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from .network import private_host

HEADER = 'X-CitiFox-Gateway'
PUBLIC_READS = frozenset(('/api/config', '/api/status', '/api/diagnostics',
                          '/api/diagnostics/download'))
PUBLIC_ACTIONS = frozenset(('/api/refresh', '/api/charging/start', '/api/charging/stop',
                            '/api/charging/target', '/api/charging/policy', '/api/smartfox/heater',
                            '/api/smartfox/charger-mode'))


class PublicGateway:
    def __init__(self, value):
        if not isinstance(value, dict) or set(value) != {'origin', 'proxy_ip', 'token_sha256'}:
            raise ValueError('Invalid public gateway configuration.')
        origin = value['origin']
        if not isinstance(origin, str):
            raise ValueError('An exact public HTTPS origin is required.')
        parsed = urlsplit(origin)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.netloc != parsed.hostname
                or origin != 'https://' + parsed.hostname
                or not re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)+', parsed.hostname)):
            raise ValueError('An exact public HTTPS origin without a port is required.')
        digest = value['token_sha256']
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ValueError('A gateway token digest is required.')
        self.origin, self.host = origin, parsed.hostname
        self.proxy_ip = private_host(value['proxy_ip'])
        self.digest = digest

    def accepts(self, peer, host, token):
        return (peer == self.proxy_ip and host == self.host
                and isinstance(token, str) and re.fullmatch(r'[0-9a-f]{64}', token) is not None
                and hmac.compare_digest(hashlib.sha256(token.encode('ascii')).hexdigest(), self.digest))


def read_public_gateway(directory):
    path = Path(directory) / 'public-gateway.json'
    return PublicGateway(json.loads(path.read_text(encoding='utf-8'))) if path.exists() else None
