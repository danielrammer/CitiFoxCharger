"""Start the loopback-only charging dashboard, Python 3.11+."""
import argparse
import os
import sys
import threading
from pathlib import Path
from ladezentrale.server import make_server
from ladezentrale.browser import open_dashboard


def main():
    parser = argparse.ArgumentParser(description="CitiFoxCharger für Windows")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("LOCALAPPDATA", Path(__file__).parent / ".local")) / "Ladezentrale")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--browser", choices=("brave", "default"), default="brave")
    parser.add_argument("--fullscreen", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port muss zwischen 1 und 65535 liegen.")
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
    return 0


if __name__ == "__main__":
    if sys.version_info < (3, 11):
        raise SystemExit("Python 3.11 oder neuer erforderlich.")
    raise SystemExit(main())
