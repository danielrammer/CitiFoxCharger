"""Detach the backend with redirected handles; no task installation or power-plan changes."""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def dashboard_running(port):
    try:
        client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with client.open(f"http://127.0.0.1:{port}/health", timeout=.5) as response:
            return json.loads(response.read(2048)).get("app") == "CitiFoxCharger"
    except (OSError, ValueError):
        return False


def spawn_backend(arguments, data_dir):
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    options = {"stdin": subprocess.DEVNULL, "cwd": str(Path(__file__).resolve().parent.parent)}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        options["start_new_session"] = True
    with (data_dir / "background.log").open("ab", buffering=0) as log:
        # The child inherits redirected files, not the closing launcher console.
        return subprocess.Popen([sys.executable, "-u", str(Path(__file__).resolve().parent.parent / "run.py"),
                                 *arguments], stdout=log, stderr=log, **options)


def wait_until_ready(process, port, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if dashboard_running(port):
            return True
        time.sleep(.15)
    return False
