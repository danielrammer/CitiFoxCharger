"""Local charging dashboard with optional authenticated LAN HTTPS, Python 3.11+."""
import argparse
import os
import sys
import threading
from pathlib import Path
from ladezentrale.server import make_server
from ladezentrale.browser import open_dashboard
from ladezentrale.background import dashboard_running, spawn_backend, wait_until_ready


def main():
    parser = argparse.ArgumentParser(description="CitiFoxCharger für Windows")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("LOCALAPPDATA", Path(__file__).parent / ".local")) / "Ladezentrale")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--browser", choices=("brave", "default"), default="brave")
    parser.add_argument("--fullscreen", action="store_true")
    parser.add_argument("--background", action="store_true", help="Backend ohne offenes Konsolenfenster starten")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port muss zwischen 1 und 65535 liegen.")
    if args.background:
        if dashboard_running(args.port):
            if not args.no_browser:
                open_dashboard(f"http://127.0.0.1:{args.port}", args.browser, args.fullscreen)
            print("CitiFoxCharger läuft bereits. Vorhandene Instanz wird verwendet.")
            return 0
        try:
            child = spawn_backend([value for value in sys.argv[1:] if value != "--background"], args.data_dir)
            if wait_until_ready(child, args.port):
                print("CitiFoxCharger läuft im Hintergrund. Beenden unter Einstellungen.")
                return 0
        except OSError:
            pass
        print("Hintergrundstart nicht bestätigt. Bitte Datenordner/background.log oder Start.cmd prüfen.")
        return 1
    try:
        server = make_server(args.port, args.data_dir)
    except ValueError as error:
        print(str(error))
        return 1
    except OSError:
        print("Start fehlgeschlagen. Port belegt oder Datenordner nicht lesbar.")
        return 1
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"CitiFoxCharger: {url}\nBeenden: Strg+C. Modbus nur lesend; lokale M+/A+-Websteuerung freigegeben.")
    if not args.no_browser:
        opener = threading.Timer(0.6, lambda: open_dashboard(url, args.browser, args.fullscreen))
        opener.daemon = True
        opener.start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    if server.restart_requested:
        # Closing the server releases the instance lock and energy handles first.
        # Start the same data profile/port; browser control stays with the launcher.
        try:
            spawn_backend([*[arg for arg in sys.argv[1:] if arg not in ("--background", "--no-browser")], "--no-browser"], args.data_dir)
        except OSError:
            print("Neustart fehlgeschlagen. Bitte Start.cmd aufrufen.")
            return 1
    return 0


if __name__ == "__main__":
    if sys.version_info < (3, 11):
        raise SystemExit("Python 3.11 oder neuer erforderlich.")
    raise SystemExit(main())
