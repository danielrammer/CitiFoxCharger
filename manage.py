"""Double-click entry points for stopping/restarting the local backend."""
import argparse
import os
from pathlib import Path
from ladezentrale.lifecycle import LocalControl, ControlError


def main():
    parser=argparse.ArgumentParser(description="CitiFoxCharger beenden oder neu starten")
    parser.add_argument("action",choices=("stop","restart"))
    parser.add_argument("--port",type=int,default=8765)
    parser.add_argument("--no-browser",action="store_true")
    parser.add_argument("--data-dir",type=Path,default=Path(os.environ.get("LOCALAPPDATA",Path(__file__).parent/".local"))/"Ladezentrale")
    args=parser.parse_args()
    if not 1<=args.port<=65535:
        parser.error("Port muss zwischen 1 und 65535 liegen.")
    try:
        print(LocalControl(args.port).action(args.action,args.data_dir,args.port,args.no_browser))
    except (ControlError,OSError) as error:
        print(str(error))
        return 1
    return 0


if __name__=="__main__":
    raise SystemExit(main())
