"""Local sunrise/sunset policy; no forecast requests and no vehicle commands.

Location and restoration intent live only in the user's data profile. A night
policy selects three phases; it never claims the idle charger physically switched.
"""
import calendar
import json
import math
import time
from datetime import datetime, timedelta, timezone

from .config import atomic_write
from .skoda import timestamp
from .smartfox_web import SmartfoxControlError


def sun_times(day, latitude, longitude):
    """NOAA fractional-year equations, 90.833 degree sunrise/sunset zenith.

    Approximate astronomical horizon times, not a prediction of usable PV.
    https://gml.noaa.gov/grad/solcalc/solareqns.PDF
    """
    angle = 2*math.pi/(366 if calendar.isleap(day.year) else 365)*(day.timetuple().tm_yday-1)
    eq = 229.18*(.000075+.001868*math.cos(angle)-.032077*math.sin(angle)
                 -.014615*math.cos(2*angle)-.040849*math.sin(2*angle))
    decl = (.006918-.399912*math.cos(angle)+.070257*math.sin(angle)
            -.006758*math.cos(2*angle)+.000907*math.sin(2*angle)
            -.002697*math.cos(3*angle)+.00148*math.sin(3*angle))
    lat = math.radians(latitude)
    cosine = (math.cos(math.radians(90.833))/(math.cos(lat)*math.cos(decl))
              -math.tan(lat)*math.tan(decl))
    if not -1 <= cosine <= 1:
        return None, None  # Polar day/night: leave the installed policy alone.
    hour = math.degrees(math.acos(cosine))
    midnight = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return (midnight+timedelta(minutes=720-4*(longitude+hour)-eq),
            midnight+timedelta(minutes=720-4*(longitude-hour)-eq))


class SolarPreparation:
    def __init__(self, service):
        self.service = service
        self.path = service.config.directory/'solar-preparation.json'
        self.record, self.next_check, self.view = {}, 0, {'configured': False}
        try:
            record = json.loads(self.path.read_text(encoding='utf-8'))
            self.validate_location(record)
            if record.get('override') and type(record.get('original_auto')) is not int:
                raise ValueError()
            if record.get('original_auto') not in (None, 0, 1, 2):
                raise ValueError()
            self.record = record
        except (OSError, ValueError, TypeError):
            pass

    @staticmethod
    def validate_location(value):
        if not isinstance(value, dict):
            raise ValueError('A valid local solar location is required.')
        for key, bound in (('latitude', 90), ('longitude', 180)):
            number = value.get(key)
            if type(number) not in (int, float) or not math.isfinite(number) or not -bound <= number <= bound:
                raise ValueError('A valid local solar location is required.')

    def save(self, **changes):
        record = {**self.record, **changes}
        atomic_write(self.path, json.dumps(record).encode())
        self.record = record

    def configure(self, value):
        if set(value) != {'latitude', 'longitude'}:
            raise ValueError('Only latitude and longitude are required.')
        self.validate_location(value)
        self.save(**value)
        self.next_check = 0

    def state(self):
        if not self.record:
            return {'configured': False, 'sunrise_at': None, 'sunset_at': None}
        now = datetime.now(timezone.utc)
        rise, setting = sun_times(now.astimezone().date(), self.record['latitude'], self.record['longitude'])
        # Only calculated times/status leave the data profile; never coordinates.
        return {'configured': True, 'sunrise_at': rise.isoformat() if rise else None,
                'sunset_at': setting.isoformat() if setting else None,
                'night': bool(rise and (now < rise or now >= setting)),
                'three_phase_selected': bool(self.record.get('override') or self.view.get('night_selected')),
                'error': self.view.get('error', False)}

    def restore(self):
        if not self.record.get('override'):
            return
        cfg = self.service.config.value['smartfox']
        current = self.service.web.read_phase_settings(cfg)
        if current['auto_mode'] == 2:
            self.service.web.set_phase_settings(cfg, auto_mode=self.record['original_auto'])
        self.save(override=False, original_auto=None)

    def tick(self):
        if not self.record or time.monotonic() < self.next_check:
            return
        self.next_check = time.monotonic()+60
        s = self.service
        if (s.stopping or s.app_update_pending or s.charging.active or s.limit_trial.active
                or s.vehicle_limit.busy or s.charging.manual.phases.pending_restore
                or not callable(getattr(s.web, 'read_phase_settings', None))):
            return
        solar = self.state()
        if solar['sunrise_at'] is None:
            return
        c = s.cache.get('smartfox', {}).get('charger', {})
        captured = timestamp(c.get('read_at'))
        if not (c.get('available') and c.get('control_available') and c.get('telemetry_available')
                and c.get('mode') == 'A+' and c.get('power_kw') == 0 and not c.get('phase_switching')
                and captured is not None and -5 <= time.time()-captured <= 30):
            return
        try:
            self.view['night_selected'] = False
            if not solar['night']:
                self.restore()
                if self.record.get('suppressed'):
                    self.save(suppressed=False)
            elif not self.record.get('suppressed'):
                settings = s.web.read_phase_settings(s.config.value['smartfox'])
                self.view['night_selected'] = settings['auto_mode'] == 2
                if self.record.get('override'):
                    if settings['auto_mode'] != 2:
                        # Respect an external phase choice for the rest of this night.
                        self.save(override=False, original_auto=None, suppressed=True)
                elif settings['auto_mode'] != 2:
                    self.save(original_auto=settings['auto_mode'], override=True)
                    s.web.set_phase_settings(s.config.value['smartfox'], auto_mode=2)
                    self.view['night_selected'] = True
            self.view['error'] = False
        except (SmartfoxControlError, OSError):
            self.view['error'] = True
