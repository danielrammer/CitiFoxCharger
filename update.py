"""Safe Git update; runtime data stay outside the checkout and no charging is started."""
import argparse
import os
import shutil
import subprocess
from pathlib import Path
from citifoxcharger.profile import default_data_dir
from citifoxcharger.lifecycle import LocalControl, ControlError

REPOSITORY = 'https://github.com/danielrammer/CitiFoxCharger.git'
REMOTES = {REPOSITORY, REPOSITORY.removesuffix('.git'), 'git@github.com:danielrammer/CitiFoxCharger.git'}


class UpdateError(Exception):
    pass


def update(directory, port=8765, data_dir=None, *, control=None, git_binary=None, remotes=REMOTES):
    binary = git_binary or shutil.which('git')
    if not binary:
        raise UpdateError('Git was not found. Install Git for Windows, then open this command window again.')
    directory = Path(directory).resolve()
    target = directory
    if not (target / '.git').exists():
        name = 'CitiFoxCharger-Git' if directory.name != 'CitiFoxCharger-Git' else 'CitiFoxCharger-Git-Checkout'
        target = directory.parent / name
        if not target.exists():
            print('ZIP installation detected. Creating a separate Git program folder:', target, flush=True)
            print('GitHub may ask you to sign in to access the private repository.', flush=True)
            result = subprocess.run([binary, 'clone', '--branch', 'main', '--single-branch', '--', REPOSITORY, str(target)])
            if result.returncode:
                raise UpdateError('Clone failed. Check GitHub sign-in and the internet connection. The existing program folder is unchanged.')
        elif not (target / '.git').exists():
            raise UpdateError('The Git folder already exists without Git history. Nothing will be overwritten: ' + str(target))

    def git(*args, capture=False):
        result = subprocess.run([binary, '-C', str(target), *args], capture_output=capture,
                                text=True, encoding='utf-8', errors='replace')
        if result.returncode:
            raise UpdateError('Git step failed: ' + args[0] + '. Nothing will be reset.')
        return result.stdout.strip() if capture else None

    if Path(git('rev-parse', '--show-toplevel', capture=True)).resolve() != target:
        raise UpdateError('The program folder is not the repository root.')
    if git('remote', 'get-url', 'origin', capture=True) not in remotes:
        raise UpdateError('origin is not the expected private CitiFoxCharger repository. Update cancelled.')
    if git('symbolic-ref', '--short', 'HEAD', capture=True) != 'main':
        raise UpdateError('This updater requires main. The current branch is unchanged.')

    def require_clean():
        if git('status', '--porcelain', '--untracked-files=normal', capture=True):
            raise UpdateError('Local or untracked program changes exist. Check them first. No reset, stash or overwrite was performed.')

    require_clean()
    print('Checking GitHub for updates ...', flush=True)
    git('fetch', 'origin', 'main')
    ancestor = subprocess.run([binary, '-C', str(target), 'merge-base', '--is-ancestor', 'HEAD', 'FETCH_HEAD'], capture_output=True)
    if ancestor.returncode:
        raise UpdateError('Local and GitHub history cannot be updated by fast-forward. Check Git history. No reset was performed.')

    client = control or LocalControl(port)
    if client.health() is not None:
        client.request('/api/session')
        key = client.request('/api/config')['key']
        if key.get('configured') and not key.get('persisted'):
            raise UpdateError('First save the API key with Windows encryption in Settings. The backend stays active.')
        print('Stopping the backend cleanly. This does not stop an active charge.', flush=True)
        print(client.action('stop', data_dir, port, no_browser=True), flush=True)

    require_clean()
    git('pull', '--ff-only', '--no-rebase', 'origin', 'main')
    print('\nUpdate complete. Program folder:', target, flush=True)
    print('Run Start.cmd in THIS folder to start the app. No charging start was sent.', flush=True)
    return target


def main():
    parser = argparse.ArgumentParser(description='Safely update CitiFoxCharger from GitHub')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data-dir', type=Path, default=default_data_dir())
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Invalid backend port.')
    try:
        update(Path(__file__).parent, args.port, args.data_dir)
    except (UpdateError, ControlError, OSError) as error:
        print('Update did not complete:', error, flush=True)
        print('If the backend already stopped, run Start.cmd after checking the error.', flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
