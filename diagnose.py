"""Small, allowlisted support report; no VIN, API key, passwords or raw responses."""
import argparse
import json
import math
import os
import re
from pathlib import Path
from ladezentrale import __version__
from ladezentrale.charging import ACTIVE
from ladezentrale.config import atomic_write
from ladezentrale.lifecycle import LocalControl, ControlError
from ladezentrale.modbus import utcnow
from ladezentrale.skoda import timestamp


def selected(data, numbers=(), flags=(), dates=(), enums=None):
    data = data if isinstance(data, dict) else {}
    result = {}
    for name in numbers:
        value = data.get(name)
        result[name] = value if type(value) in (int, float) and math.isfinite(value) else None
    for name in flags:
        result[name] = data.get(name) if type(data.get(name)) is bool else None
    for name in dates:
        result[name] = data.get(name) if timestamp(data.get(name)) is not None else None
    for name, choices in (enums or {}).items():
        value = data.get(name)
        result[name] = value if isinstance(value, str) and value in choices else None
    return result


def report(status, live=False):
    status = status if isinstance(status, dict) else {}
    car = status.get('skoda', {})
    smartfox = status.get('smartfox', {})
    charger = smartfox.get('charger', {}) if isinstance(smartfox, dict) else {}
    session = status.get('charging', {})
    command = status.get('last_command') or {}
    remaining = car.get('quota_remaining') if isinstance(car, dict) else None
    quota = int(remaining) if isinstance(remaining, str) and remaining.isascii() and remaining.isdecimal() and len(remaining) <= 6 else None
    version = status.get('backend_version')
    return {'report_version': 1, 'program_version': __version__,
            'backend_version': version if isinstance(version, str) and re.fullmatch(r'\d+\.\d+\.\d+', version) else None,
            'generated_at': utcnow(),
            'source': 'running_backend' if live else 'saved_files_only',
            'car': {**selected(car, numbers=('soc',), flags=('available','stale'), dates=('captured_at','read_at'),
                              enums={'code':('ok','partial','network','quota_reserved','rate_limit','auth','forbidden','not_found','unsupported','protocol','upstream','not_configured')}),
                    'quota_remaining': quota},
            'charger': selected(charger, numbers=('power_kw','status_code'), flags=('available','telemetry_available'),
                                dates=('read_at',), enums={'mode': ('OFF','A+','M+','A','M')}),
            'session': selected(session, numbers=('target_percent','last_start_epoch','fresh_soc_deadline',
                        'data_missing_since','stop_intent_epoch','stop_requested_epoch','stop_failures','last_stop_failure_epoch'),
                        flags=('awaiting_fresh_soc','stop_requested','stop_guard','auto_restored','vehicle_stop_confirmed'),
                        dates=('started_at','completed_at','stop_requested_at'),
                        enums={'status': ACTIVE | {'idle','complete','closed','error'},
                               'stop_cause':('target','missing_initial_soc','missing_soc')}),
            'last_command': selected(command, dates=('requested_at',), enums={'action':('start','stop'),
                                    'status':('accepted','unknown','rejected','observed','unconfirmed')}),
            'monitoring': {**selected(status.get('monitoring', {}), flags=('running',)),
                           **selected(status,numbers=('next_skoda_seconds',),dates=('server_at',))}}


def saved_status(directory):
    def read(name):
        try:
            data = json.loads((directory / name).read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}
    return {'charging': read('charging-session.json'), 'skoda': read('vehicle-cache.json').get('status', {})}


def main():
    parser = argparse.ArgumentParser(description='CitiFoxCharger-Diagnose ohne Zugangsdaten erstellen')
    parser.add_argument('--data-dir', type=Path, default=Path(os.environ.get('LOCALAPPDATA', Path(__file__).parent / '.local')) / 'Ladezentrale')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--output', type=Path, default=Path(__file__).parent / 'CitiFoxCharger-Diagnose.json')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Ungültiger Port.')
    status, live = saved_status(args.data_dir), False
    if not args.offline:
        client = LocalControl(args.port)
        try:
            health = client.health()
            if health is not None:
                client.request('/api/session')
                status, live = client.request('/api/status'), True
                status['backend_version'] = health.get('version')
        except ControlError:
            pass
    try:
        atomic_write(args.output, json.dumps(report(status, live), ensure_ascii=False, indent=2).encode('utf-8'))
    except OSError:
        print('Bericht konnte nicht gespeichert werden. Mit --output einen beschreibbaren Pfad wählen.')
        return 1
    print('Diagnose gespeichert:', args.output)
    print('Quelle:', 'laufendes Backend' if live else 'nur gespeicherte Dateien; möglicherweise ältere Daten')
    print('Enthält Ziel, Zeitstempel und Status; keine FIN, Zugangsdaten oder Rohantworten.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
