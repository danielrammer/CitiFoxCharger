"""Persisted exclusive ownership and monitored automatic charging windows.

Only the ordinary manual controller may energize M+. This policy closes access
outside a session; it never implements a separate, unmonitored charging path.
"""
import json
import math
import time
import uuid
from datetime import datetime, timedelta
from .config import atomic_write
from .charging_window import in_window, parse_time, AUTOMATIC_RETRY_SECONDS
from .skoda import IntegrationError
from .smartfox_web import SmartfoxControlError


class ChargingPolicy:
    def __init__(self, service):
        self.service = service
        self.path = service.config.directory / 'charging-policy.json'
        self.value = dict(only=False, automatic=False, start='22:00', end='05:00', target=80, claimed=None,
                          automatic_aborts=[], blocked_window=None)
        self.message = ''
        self.next_attempt = 0
        self.busy = False
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if isinstance(value, dict) and set(value) == {'only','automatic','start','end','target','claimed'}:
                value.update(automatic_aborts=[], blocked_window=None)
            self.validate(value)
            self.value = value
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError):
            # An unreadable ownership record must not silently release A+.
            self.value.update(only=True)
            self.message = 'Charging policy could not be read. Charger remains Off.'

    @staticmethod
    def validate(value):
        if not isinstance(value, dict) or set(value) != {'only','automatic','start','end','target','claimed','automatic_aborts','blocked_window'}:
            raise ValueError('Invalid charging policy.')
        if any(type(value[k]) is not bool for k in ('only','automatic')):
            raise ValueError('Charging switches must be true or false.')
        if value['automatic'] and not value['only']:
            raise ValueError('Automatic charging requires CitiFox only.')
        if parse_time(value['start']) == parse_time(value['end']):
            raise ValueError('Start and end must differ.')
        if type(value['target']) is not int or not 1 <= value['target'] <= 100:
            raise ValueError('Target must be between 1 and 100%.')
        if value['claimed'] is not None and (not isinstance(value['claimed'],str) or len(value['claimed']) > 30):
            raise ValueError('Invalid charging window record.')
        if (not isinstance(value['automatic_aborts'], list) or len(value['automatic_aborts']) > 3
                or any(type(t) not in (int, float) or not math.isfinite(t) or t < 0 for t in value['automatic_aborts'])
                or value['blocked_window'] is not None and (not isinstance(value['blocked_window'], str) or len(value['blocked_window']) > 30)):
            raise ValueError('Invalid automatic-abort record.')

    def save(self, **changes):
        candidate = {**self.value, **changes}
        self.validate(candidate)
        atomic_write(self.path, json.dumps(candidate).encode())
        self.value = candidate

    def window(self, now=None):
        now = now or datetime.now().astimezone()
        if not in_window(now, self.value['start'], self.value['end']):
            return None
        day = now.date()
        if self.value['start'] > self.value['end'] and now.strftime('%H:%M') < self.value['end']:
            day -= timedelta(days=1)
        return f'{day.isoformat()}T{self.value["start"]}'

    def state(self):
        session = self.service.charging.value
        key = self.window()
        message = self.message
        if key and session.get('manual_stop_window') == key:
            message = 'Automatic restart is suspended until the next time window.'
        elif self.is_blocked(key):
            message = 'Three automatic aborts within one hour. Automatic restart is suspended until the next time window.'
        elif self.service.charging.active and session.get('automatic_window') == key and session.get('manual_phase') in ('disconnected', 'automatic_wait'):
            message = session.get('message') or message
        return {k:self.value[k] for k in ('only','automatic','start','end','target')} | {'message':message, 'blocked':self.is_blocked(key)}

    def is_blocked(self, key=None):
        key = self.window() if key is None else key
        return bool(key and self.value['blocked_window'] == key)

    def record_abort(self):
        key = self.window()
        if not key or not self.value['only'] or not self.value['automatic']:
            return False
        if self.is_blocked(key):
            return True
        now = time.time()
        # Future records survive a backward clock correction conservatively.
        recent = [t for t in self.value['automatic_aborts'] if now-t <= 3600]
        recent = (recent+[now])[-3:]
        blocked = len(recent) == 3
        self.save(automatic_aborts=recent, blocked_window=key if blocked else self.value['blocked_window'])
        return blocked

    def configure(self, data):
        if not isinstance(data,dict) or set(data) != {'only','automatic','start','end','target'}:
            raise ValueError('All charging policy fields are required.')
        self.save(**data)
        self.next_attempt = 0
        if not self.value["only"] and not self.service.charging.active:
            self.service.cache.setdefault("smartfox", {})["charger"] = self.service.web.set_mode(self.service.config.value["smartfox"], "A+")
        self.tick()
        return self.state()

    def suppress_window(self):
        key = self.window()
        if key:
            self.save(claimed=key)
            self.service.charging.save(manual_stop_window=key)

    def tick(self):
        s = self.service
        if self.busy or s.stopping or s.app_update_pending or time.monotonic() < self.next_attempt:
            return
        self.next_attempt = time.monotonic()+10
        key = self.window()
        scheduled = s.charging.value.get('automatic_window')
        if s.charging.active:
            if scheduled and (not self.value['automatic'] or scheduled != key or self.is_blocked(key)) and not s.charging.value.get('stop_requested'):
                try:s.stop_charge('window-end-'+uuid.uuid4().hex, manual=False)
                except IntegrationError:self.message = 'Window ended. Local stop requested; vehicle confirmation pending.'
            return
        if not self.value['only']:
            return
        try:
            # Read back every idle enforcement; schedules must not resume power.
            s.cache.setdefault('smartfox', {})['charger'] = s.web.set_charger_mode(s.config.value['smartfox'], 'OFF')
        except (SmartfoxControlError, AttributeError):
            self.message = 'CitiFox only: charger Off is unconfirmed. Check SMARTFOX.'
            return
        if (not self.value['automatic'] or key is None or s.limit_test.active or s.limit_trial.active
                or self.is_blocked(key) or s.charging.value.get('manual_stop_window') == key):
            return
        if key == self.value['claimed']:
            if scheduled == key:
                s.charging.manual.retry_automatic()
                return
            if (s.charging.value.get('automatic_start_retry_window') != key
                    or time.time() < s.charging.value.get('automatic_start_retry_at', float('inf'))):
                return
        if s.charging.manual.reason():
            self.message = "Automatic charging is waiting for vehicle readiness or access."
            return
        # Record each attempt before calling the controller; a failed initial
        # preparation can retry after three minutes, never after manual Stop.
        self.save(claimed=key)
        s.charging.save(automatic_start_retry_window=key, automatic_start_retry_at=time.time()+AUTOMATIC_RETRY_SECONDS)
        self.busy = True
        attempt_id = 'scheduled-'+uuid.uuid4().hex
        try:
            result = s.start_manual_charging(self.value['target'], attempt_id, True)
            s.charging.save(automatic_window=key)
            self.message = 'Automatic charging started; the selected target is monitored.'
        except (IntegrationError, ValueError) as exc:
            if isinstance(exc, IntegrationError) and exc.code == 'target_reached':
                s.charging.save(automatic_start_retry_window=None, automatic_start_retry_at=None)
                self.message = str(exc)
                return
            if s.charging.value.get('request_id') != attempt_id:
                self.record_abort()
            self.message = 'Automatic start could not be completed. Waiting three minutes before the next eligible attempt.'
        finally:
            self.busy = False
