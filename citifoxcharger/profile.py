"""Choose a default profile without moving existing credentials or session state."""
import os
from pathlib import Path


def default_data_dir():
    base = Path(os.environ.get('LOCALAPPDATA', Path(__file__).resolve().parents[1] / '.local'))
    # Existing installations retain the historical directory and per-profile lock.
    legacy = base / 'Ladezentrale'
    return legacy if legacy.exists() else base / 'CitiFoxCharger'
