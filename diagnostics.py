"""Small, allowlisted support report; no VIN, API key, passwords or raw responses."""
import argparse
import json
import math
import os
import re
from pathlib import Path
from citifoxcharger.profile import default_data_dir
from citifoxcharger import __version__
from citifoxcharger.charging import ACTIVE
from citifoxcharger.config import atomic_write
from citifoxcharger.lifecycle import LocalControl, ControlError
from citifoxcharger.modbus import utcnow
from citifoxcharger.skoda import timestamp


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
                              enums={'code':('ok','partial','network','quota_reserved','rate_limit','auth','forbidden','not_found','unsupported','protocol','upstream','not_configured'),
                                     'state':('CHARGING','CONNECT_CABLE','CONSERVING','READY_FOR_CHARGING','DISCHARGING','CHARGING_INTERRUPTED')}),
                    'quota_remaining': quota},
            'charger': selected(charger, numbers=('power_kw','status_code','info_code','offered_current_a','phase_count','phase_switch_seconds'), flags=('available','telemetry_available','ready_for_start','phase_switching'),
                                dates=('read_at',), enums={'mode': ('OFF','A+','M+','A','M')}),
            'session': selected(session, numbers=('target_percent','last_start_epoch','fresh_soc_deadline',
                        'data_missing_since','stop_intent_epoch','stop_requested_epoch','stop_failures','last_stop_failure_epoch','phase_start_deadline_epoch','phase_ready_since_epoch','stop_trigger_soc','target_probe_count'),
                        flags=('awaiting_fresh_soc','stop_requested','stop_guard','auto_restored','vehicle_stop_confirmed','phase_start_pending'),
                        dates=('started_at','completed_at','stop_requested_at','stop_trigger_at','stop_trigger_captured_at','stop_trigger_read_at','target_probe_capture','target_probe_scheduled_at'),
                        enums={'status': ACTIVE | {'idle','complete','closed','error'},
                               'stop_cause':('target','missing_initial_soc','missing_soc','phase_wait')}),
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
    parser = argparse.ArgumentParser(description='Create CitiFoxCharger diagnostics without credentials')
    parser.add_argument('--data-dir', type=Path, default=default_data_dir())
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--output', type=Path, default=Path(__file__).parent / 'CitiFoxCharger-Diagnostics.json')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Invalid port.')
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
        print('The report could not be saved. Choose a writable path with --output.')
        return 1
    print('Diagnostics saved:', args.output)
    print('Source:', 'running backend' if live else 'saved files only; data may be older')
    print('Includes target, timestamps and status; no VIN, credentials or raw responses.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
