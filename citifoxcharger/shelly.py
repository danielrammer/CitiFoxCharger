"""One opt-in Shelly 1 Mini Gen4 output; timers run on the device itself."""
import copy
import json
import math
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import (Request, build_opener, ProxyHandler, HTTPRedirectHandler,
                            HTTPPasswordMgrWithDefaultRealm, HTTPDigestAuthHandler)
from .config import atomic_write
from .network import private_host
from .secrets_store import dpapi
from .skoda import IntegrationError

DEFAULT = dict(enabled=False, host='', name='Shelly output', on_minutes=60)
MODEL = 'S4SW-001X8EU'


def validate(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT):
        raise ValueError('Invalid Shelly configuration.')
    if type(value['enabled']) is not bool:
        raise ValueError('Invalid Shelly switch.')
    if not isinstance(value['host'], str):
        raise ValueError('Shelly requires a private IPv4 address.')
    if value['host'] or value['enabled']:
        private_host(value['host'])
    if (not isinstance(value['name'], str) or not value['name'].strip() or len(value['name']) > 40
            or any(ord(c) < 32 for c in value['name'])):
        raise ValueError('Use a Shelly name of 1 to 40 characters.')
    if type(value['on_minutes']) is not int or value['on_minutes'] not in (0, 30, 60, 90, 120):
        raise ValueError('Choose continuous On or 30, 60, 90 or 120 minutes.')
    return {**value, 'name': value['name'].strip()}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ShellyRPC:
    def __init__(self, host, password=''):
        self.base = 'http://' + private_host(host)
        manager = HTTPPasswordMgrWithDefaultRealm()
        manager.add_password(None, self.base, 'admin', password)
        self.opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPDigestAuthHandler(manager))

    def call(self, method, params=None):
        # Every endpoint is fixed by this module; never accept a URL from a command.
        query = '?id=0' if method == 'Switch.GetStatus' else ''
        body = json.dumps(params).encode() if params is not None else None
        request = Request(self.base + '/rpc/' + method + query, data=body,
                          headers={'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=2) as response:
                raw = response.read(65537)
                if len(raw) > 65536:
                    raise ValueError()
                value = json.loads(raw)
            if not isinstance(value, dict) or 'error' in value or 'code' in value:
                raise ValueError()
            return value
        except HTTPError as error:
            error.close()
            message = ('Shelly access denied. Check its password on the tablet.' if error.code == 401
                       else 'Shelly did not accept the request.')
            raise IntegrationError('shelly_unavailable', message) from None
        except (URLError, OSError, ValueError, UnicodeError):
            raise IntegrationError('shelly_unavailable', 'Shelly is unreachable or returned invalid data.') from None


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


class ShellyOutput:
    def __init__(self, directory, factory=ShellyRPC, defaults=None):
        self.directory, self.factory = Path(directory), factory
        self.path = self.directory / 'shelly.json'
        self.password_path = self.directory / 'shelly-password.dpapi'
        self.commands_path = self.directory / 'shelly-commands.json'
        self.config, self.password, self.client = validate(defaults or dict(DEFAULT)), '', None
        self.next_poll, self.verified, self.commands = 0, False, {}
        self.value = dict(available=False, on=None, timer_ends_at=None, message='')
        try:
            if self.path.exists():
                self.config = validate(json.loads(self.path.read_text(encoding='utf-8')))
            if self.password_path.exists():
                credential = json.loads(dpapi(self.password_path.read_bytes(), decrypt=True).decode('utf-8'))
                if not isinstance(credential, dict) or credential.get('host') != self.config['host'] or not isinstance(credential.get('password'), str):
                    raise ValueError()
                self.password = credential['password']
            if self.commands_path.exists():
                commands = json.loads(self.commands_path.read_text(encoding='utf-8'))
                if not isinstance(commands, dict) or len(commands) > 100:
                    raise ValueError()
                for key, value in commands.items():
                    if (not isinstance(key, str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,80}', key)
                            or not isinstance(value, dict) or set(value) != {'action', 'minutes'}):
                        raise ValueError()
                    self.validate_action(value['action'], value['minutes'])
                self.commands = commands
        except (OSError, ValueError, UnicodeError):
            # Do not start from an unreadable private profile or replay a command.
            self.config['enabled'] = False
            self.value['message'] = 'Check Shelly setup on the tablet.'

    def settings(self):
        return {**self.config, 'password_configured': bool(self.password)}

    def configure(self, data):
        if not isinstance(data, dict) or set(data) != set(DEFAULT) | {'password', 'clear_password'}:
            raise ValueError('Invalid Shelly configuration.')
        value = validate({k: data[k] for k in DEFAULT})
        password = data['password']
        if (not isinstance(password, str) or len(password) > 256 or '\x00' in password
                or type(data['clear_password']) is not bool or password and data['clear_password']):
            raise ValueError('Invalid Shelly password.')
        # Do not send credentials saved for another device to a newly entered IP.
        keep = value['host'] == self.config['host'] and not data['clear_password']
        encrypted = dpapi(json.dumps({'host': value['host'], 'password': password}).encode('utf-8')) if password else None
        if encrypted is not None:
            atomic_write(self.password_path, encrypted)
        elif not keep:
            self.password_path.unlink(missing_ok=True)
        atomic_write(self.path, json.dumps(value).encode('utf-8'))
        self.password = password or (self.password if keep else '')
        self.config, self.client, self.verified, self.next_poll = value, None, False, 0
        self.value = dict(available=False, on=None, timer_ends_at=None, message='')
        return {'message': 'Shelly settings saved. No output was switched.'}

    def rpc(self, method, params=None):
        if not self.config['enabled']:
            raise IntegrationError('blocked', 'Configure the Shelly on the tablet first.')
        if self.client is None:
            self.client = self.factory(self.config['host'], self.password)
        if not self.verified:
            device = self.client.call('Shelly.GetDeviceInfo')
            if device.get('model') != MODEL or device.get('gen') != 4:
                raise IntegrationError('blocked', 'The configured device is not a Shelly 1 Mini Gen4.')
            self.verified = True
        return self.client.call(method, params)

    def read(self):
        result = self.rpc('Switch.GetStatus')
        if result.get('id') != 0 or type(result.get('output')) is not bool:
            raise IntegrationError('shelly_unavailable', 'Shelly did not confirm its output state.')
        if result.get('errors'):
            raise IntegrationError('shelly_unavailable', 'Shelly reports a device fault. Check the device.')
        started, duration = result.get('timer_started_at'), result.get('timer_duration')
        timer = started is not None or duration is not None
        if timer and (not finite(started) or not finite(duration) or duration <= 0):
            raise IntegrationError('shelly_unavailable', 'Shelly did not confirm its timer.')
        self.value = dict(available=True, on=result['output'], timer_ends_at=started+duration if timer else None,
                          timer_duration=duration if timer else None, read_at=time.time(), message='')
        return self.value

    def poll(self):
        if not self.config['enabled'] or time.monotonic() < self.next_poll:
            return
        self.next_poll = time.monotonic()+10
        try:
            self.read()
        except IntegrationError as error:
            self.next_poll = time.monotonic()+60
            self.value = dict(available=False, on=None, timer_ends_at=None, message=str(error))

    def state(self):
        result = copy.deepcopy(self.value)
        if result.get('read_at') and time.time()-result['read_at'] > 30:
            result.update(available=False, on=None, timer_ends_at=None, message='Shelly status is stale.')
        return {**result, 'configured': self.config['enabled'], 'name': self.config['name'],
                'on_minutes': self.config['on_minutes']}

    @staticmethod
    def validate_action(action, minutes):
        if action not in ('on', 'off', 'timer'):
            raise ValueError('Choose On, Off or On with timer.')
        if action == 'timer':
            if type(minutes) is not int or minutes not in (30, 60, 90, 120):
                raise ValueError('Choose continuous On or 30, 60, 90 or 120 minutes.')
        elif minutes is not None:
            raise ValueError('Only timed On accepts a duration.')

    def set(self, action, minutes, request_id):
        self.validate_action(action, minutes)
        if not isinstance(request_id, str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,80}', request_id):
            raise ValueError('Invalid Shelly request ID.')
        intent = dict(action=action, minutes=minutes)
        if request_id in self.commands:
            if self.commands[request_id] != intent:
                raise ValueError('This request ID belongs to a different Shelly command.')
            raise IntegrationError('shelly_duplicate', 'This Shelly command was already submitted. Check its current status.')
        self.verified = False  # Recheck identity after possible DHCP reassignment.
        if action != 'off':
            self.read()  # Validate current status before energizing the output.
        commands = {**self.commands, request_id: intent}
        commands = dict(list(commands.items())[-100:])
        atomic_write(self.commands_path, json.dumps(commands).encode())
        self.commands = commands  # A timeout/restart must never extend a timer by replay.
        params = dict(id=0, on=action != 'off')
        if action == 'timer':
            params['toggle_after'] = minutes*60
        try:
            self.rpc('Switch.Set', params)
            result = self.read()
            if result['on'] != params['on']:
                raise IntegrationError('shelly_unconfirmed', 'Shelly did not confirm the requested output state.')
            if action == 'timer' and (result['timer_duration'] is None
                    or abs(result['timer_duration']-minutes*60) > 1
                    or not time.time()-10 <= result['timer_ends_at']-minutes*60 <= time.time()+10):
                # Never report successful timed On when only On was confirmed.
                self.rpc('Switch.Set', dict(id=0, on=False))
                self.read()
                raise IntegrationError('shelly_unconfirmed', 'Shelly timer was not confirmed. Off was requested as a precaution.')
        except IntegrationError:
            self.value.update(available=False, on=None, timer_ends_at=None,
                              message='Shelly command is unconfirmed. Check the output before retrying.')
            raise
        self.next_poll = time.monotonic()+10
        return {'message': 'Shelly output confirmed.', 'shelly': self.state()}


# Explicit installation destinations authorized by the owner on 2026-11-20.
# Other addresses/credentials can only be configured through the tablet's local UI.
INSTALLATION = {
    'fan': dict(DEFAULT, enabled=True, host='192.168.0.100', name='Fan'),
    'light': dict(DEFAULT, enabled=True, host='192.168.0.85', name='Light'),
}


class ShellyOutputs:
    def __init__(self, directory, factory=ShellyRPC, installation=False):
        self.outputs = {key: ShellyOutput(Path(directory)/'shelly'/key, factory,
                                         defaults if installation else dict(DEFAULT, name=defaults['name']))
                        for key, defaults in INSTALLATION.items()}

    def device(self, key):
        if not isinstance(key, str) or key not in self.outputs:
            raise ValueError('Unknown Shelly output.')
        return self.outputs[key]

    def configure(self, data):
        if not isinstance(data, dict) or 'device' not in data:
            raise ValueError('Choose a Shelly output.')
        return self.device(data['device']).configure({k: v for k, v in data.items() if k != 'device'})

    def preferences(self, data):
        if not isinstance(data, dict) or set(data) != {'device', 'on_minutes'}:
            raise ValueError('Choose a Shelly output and its On behavior.')
        device = self.device(data['device'])
        return device.configure({**device.config, 'on_minutes': data['on_minutes'],
                                 'password': '', 'clear_password': False})

    def settings(self, local_admin):
        return {key: output.settings() if local_admin else {'on_minutes': output.config['on_minutes']}
                for key, output in self.outputs.items()}

    def state(self):
        return {key: output.state() for key, output in self.outputs.items()}

    def poll(self):
        for output in self.outputs.values():
            output.poll()

    def set(self, key, on, request_id):
        if type(on) is not bool:
            raise ValueError('The Shelly output state must be true or false.')
        output = self.device(key)
        minutes = output.config['on_minutes'] if on else 0
        result = output.set('timer' if minutes else 'on' if on else 'off', minutes or None, request_id)
        return {'message': result['message'], 'shelly': self.state()}
