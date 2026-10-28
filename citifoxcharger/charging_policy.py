"""Persisted exclusive ownership and one automatic attempt per local window.

Only the ordinary manual controller may energize M+. This policy closes access
outside a session; it never implements a separate, unmonitored charging path.
"""
import json
import time
import uuid
from datetime import datetime, timedelta
from .config import atomic_write
from .charging_window import in_window, parse_time
from .skoda import IntegrationError
from .smartfox_web import SmartfoxControlError


class ChargingPolicy:
    def __init__(self, service):
        self.service = service
        self.path = service.config.directory / 'charging-policy.json'
        self.value = dict(only=False, automatic=False, start='22:00', end='05:00', target=80, claimed=None)
        self.message = ''
        self.next_attempt = 0
        self.busy = False
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
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
        if not isinstance(value, dict) or set(value) != {'only','automatic','start','end','target','claimed'}:
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
        return {k:self.value[k] for k in ('only','automatic','start','end','target')} | {'message':self.message}

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

    def tick(self):
        s = self.service
        if self.busy or s.stopping or s.app_update_pending or time.monotonic() < self.next_attempt:
            return
        self.next_attempt = time.monotonic()+10
        key = self.window()
        scheduled = s.charging.value.get('automatic_window')
        if s.charging.active:
            if scheduled and (not self.value['automatic'] or scheduled != key) and not s.charging.value.get('stop_requested'):
                try:s.stop_charge('window-end-'+uuid.uuid4().hex)
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
        if not self.value['automatic'] or key is None or key == self.value['claimed'] or s.limit_test.active or s.limit_trial.active:
            return
        if s.charging.manual.reason():
            self.message = "Automatic charging is waiting for vehicle readiness or access."
            return
        # Commit before the first attempt. Restart, Stop and target completion
        # cannot create another automatic charge in the same window.
        self.save(claimed=key)
        self.busy = True
        try:
            result = s.start_manual_charging(self.value['target'], 'scheduled-'+uuid.uuid4().hex, True)
            s.charging.save(automatic_window=key)
            self.message = 'Automatic charging started; the selected target is monitored.'
        except (IntegrationError, ValueError):
            self.message = 'Automatic start could not be completed. Check charging diagnostics; no automatic repeat in this window.'
        finally:
            self.busy = False
