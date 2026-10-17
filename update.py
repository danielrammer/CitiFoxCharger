"""Safe Git update; runtime data stay outside the checkout and no charging is started."""
import argparse
import os
import shutil
import subprocess
from pathlib import Path
from ladezentrale.lifecycle import LocalControl, ControlError

REPOSITORY = 'https://github.com/danielrammer/CitiFoxCharger.git'
REMOTES = {REPOSITORY, REPOSITORY.removesuffix('.git'), 'git@github.com:danielrammer/CitiFoxCharger.git'}


class UpdateError(Exception):
    pass


def update(directory, port=8765, data_dir=None, *, control=None, git_binary=None, remotes=REMOTES):
    binary = git_binary or shutil.which('git')
    if not binary:
        raise UpdateError('Git wurde nicht gefunden. Zuerst Git fuer Windows installieren und dieses Fenster danach neu oeffnen.')
    directory = Path(directory).resolve()
    target = directory
    if not (target / '.git').exists():
        name = 'CitiFoxCharger-Git' if directory.name != 'CitiFoxCharger-Git' else 'CitiFoxCharger-Git-Checkout'
        target = directory.parent / name
        if not target.exists():
            print('ZIP-Installation erkannt. Erstelle einen eigenen Git-Programmordner:', target, flush=True)
            print('GitHub kann eine Anmeldung fuer das private Repository verlangen.', flush=True)
            result = subprocess.run([binary, 'clone', '--branch', 'main', '--single-branch', '--', REPOSITORY, str(target)])
            if result.returncode:
                raise UpdateError('Klonen fehlgeschlagen. GitHub-Anmeldung und Internetverbindung pruefen. Der bisherige Programmordner bleibt erhalten.')
        elif not (target / '.git').exists():
            raise UpdateError('Der vorgesehene Git-Ordner existiert bereits ohne Git-Historie. Es wird nichts ueberschrieben: ' + str(target))

    def git(*args, capture=False):
        result = subprocess.run([binary, '-C', str(target), *args], capture_output=capture,
                                text=True, encoding='utf-8', errors='replace')
        if result.returncode:
            raise UpdateError('Git-Schritt fehlgeschlagen: ' + args[0] + '. Es wird nichts zurueckgesetzt.')
        return result.stdout.strip() if capture else None

    if Path(git('rev-parse', '--show-toplevel', capture=True)).resolve() != target:
        raise UpdateError('Der Programmordner ist nicht die Wurzel des Repositorys.')
    if git('remote', 'get-url', 'origin', capture=True) not in remotes:
        raise UpdateError('origin ist nicht das erwartete private CitiFoxCharger-Repository. Update abgebrochen.')
    if git('symbolic-ref', '--short', 'HEAD', capture=True) != 'main':
        raise UpdateError('Das Update ist fuer main vorgesehen. Der aktuelle Branch bleibt unveraendert.')

    def require_clean():
        if git('status', '--porcelain', '--untracked-files=normal', capture=True):
            raise UpdateError('Eigene oder ungespeicherte Programmdateien vorhanden. Bitte zuerst pruefen. Kein Reset, Stash oder Ueberschreiben wurde ausgefuehrt.')

    require_clean()
    print('Pruefe neue Version auf GitHub ...', flush=True)
    git('fetch', 'origin', 'main')
    ancestor = subprocess.run([binary, '-C', str(target), 'merge-base', '--is-ancestor', 'HEAD', 'FETCH_HEAD'], capture_output=True)
    if ancestor.returncode:
        raise UpdateError('Lokaler Stand und GitHub lassen sich nicht ohne Zusammenfuehren aktualisieren. Bitte Git-Verlauf pruefen. Kein Reset wurde ausgefuehrt.')

    client = control or LocalControl(port)
    if client.health() is not None:
        client.request('/api/session')
        key = client.request('/api/config')['key']
        if key.get('configured') and not key.get('persisted'):
            raise UpdateError('Den API-Schluessel zuerst in Einstellungen mit Windows verschluesselt speichern. Das Backend bleibt aktiv.')
        print('Beende das Backend geordnet. Eine laufende Ladung wird dadurch nicht gestoppt.', flush=True)
        print(client.action('stop', data_dir, port, no_browser=True), flush=True)

    require_clean()
    git('pull', '--ff-only', '--no-rebase', 'origin', 'main')
    print('\nUpdate abgeschlossen. Programmordner:', target, flush=True)
    print('Zum Starten Start.cmd in DIESEM Ordner ausfuehren. Es wurde kein Ladestart gesendet.', flush=True)
    return target


def main():
    parser = argparse.ArgumentParser(description='CitiFoxCharger sicher von GitHub aktualisieren')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data-dir', type=Path, default=Path(os.environ.get('LOCALAPPDATA', Path(__file__).parent / '.local')) / 'Ladezentrale')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Ungueltiger Backend-Port.')
    try:
        update(Path(__file__).parent, args.port, args.data_dir)
    except (UpdateError, ControlError, OSError) as error:
        print('\nUpdate nicht abgeschlossen:', error, flush=True)
        print('Falls das Backend bereits beendet wurde: nach der Pruefung Start.cmd aufrufen.', flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
