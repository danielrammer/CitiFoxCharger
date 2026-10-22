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


from citifoxcharger.diagnostics import report, selected


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
