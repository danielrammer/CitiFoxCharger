"""Open the dashboard in Brave when installed, with a normal-browser fallback."""
import os
import shutil
import subprocess
import webbrowser
from pathlib import Path


def find_brave():
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        root = os.environ.get(variable)
        if root:
            candidate = Path(root) / "BraveSoftware/Brave-Browser/Application/brave.exe"
            if candidate.is_file():
                return str(candidate)
    return shutil.which("brave") or shutil.which("brave-browser")


def open_dashboard(url, browser="brave", fullscreen=False):
    executable = find_brave() if browser == "brave" else None
    if executable:
        arguments = [executable, "--new-window"]
        if fullscreen:
            arguments.append("--start-fullscreen")
        arguments.append(url)
        try:
            subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return "brave"
        except OSError:
            pass
    if browser == "brave":
        print("Brave nicht verfügbar. Öffne den Standardbrowser; Vollbild gegebenenfalls mit F11.")
    webbrowser.open(url, new=1)
    return "default"
