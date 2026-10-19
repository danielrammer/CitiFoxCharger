"""Safe Git update; runtime data stay outside the checkout and no charging is started."""
import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from citifoxcharger.profile import default_data_dir
from citifoxcharger.lifecycle import LocalControl, ControlError

REPOSITORY = 'https://github.com/danielrammer/CitiFoxCharger.git'
REMOTES = {REPOSITORY, REPOSITORY.removesuffix('.git'), 'git@github.com:danielrammer/CitiFoxCharger.git'}


class UpdateError(Exception):
    pass


def update(directory, port=8765, data_dir=None, *, control=None, git_binary=None, remotes=REMOTES, progress=None, timeout=None):
    progress = progress or (lambda phase: None)
    progress('checking')
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
            result = subprocess.run([binary, 'clone', '--branch', 'main', '--single-branch', '--', REPOSITORY, str(target)],timeout=timeout)
            if result.returncode:
                raise UpdateError('Clone failed. Check GitHub sign-in and the internet connection. The existing program folder is unchanged.')
        elif not (target / '.git').exists():
            raise UpdateError('The Git folder already exists without Git history. Nothing will be overwritten: ' + str(target))

    def git(*args, capture=False):
        result = subprocess.run([binary, '-C', str(target), *args], capture_output=capture,
                                text=True, encoding='utf-8', errors='replace',timeout=timeout)
        if result.returncode:
            raise UpdateError('Git step failed: ' + args[0] + '. Nothing will be reset.')
        return (result.stdout if '-z' in args else result.stdout.strip()) if capture else None

    if Path(git('rev-parse', '--show-toplevel', capture=True)).resolve() != target:
        raise UpdateError('The program folder is not the repository root.')
    if git('remote', 'get-url', 'origin', capture=True) not in remotes:
        raise UpdateError('origin is not the expected private CitiFoxCharger repository. Update cancelled.')
    if git('symbolic-ref', '--short', 'HEAD', capture=True) != 'main':
        raise UpdateError('This updater requires main. The current branch is unchanged.')

    def require_tracked_clean():
        if git('status', '--porcelain', '--untracked-files=no', capture=True):
            raise UpdateError('Tracked program changes exist. Check them first. No reset, stash or overwrite was performed.')

    def protect_local_files():
        # Include ignored files: Git may otherwise overwrite them during a merge.
        local = git('ls-files', '--others', '-z', capture=True).split('\0')
        incoming = git('ls-tree', '-r', '--name-only', '-z', 'FETCH_HEAD', capture=True).split('\0')
        def normal(path):
            path = path.rstrip('/')
            return path.casefold() if os.name == 'nt' else path
        incoming = [normal(path) for path in incoming if path]
        for path in local:
            if not path:
                continue
            name = normal(path)
            if any(name == other or name.startswith(other+'/') or other.startswith(name+'/') for other in incoming):
                raise UpdateError(f'A local file conflicts with an incoming program path: {path!r}. '
                                  'Move that file outside the program folder first. Nothing was overwritten.')

    require_tracked_clean()
    print('Checking GitHub for updates ...', flush=True)
    git('fetch', 'origin', 'main')
    ancestor = subprocess.run([binary, '-C', str(target), 'merge-base', '--is-ancestor', 'HEAD', 'FETCH_HEAD'], capture_output=True,timeout=timeout)
    if ancestor.returncode:
        raise UpdateError('Local and GitHub history cannot be updated by fast-forward. Check Git history. No reset was performed.')
    protect_local_files()

    client = control or LocalControl(port)
    if client.health() is not None:
        client.request('/api/session')
        key = client.request('/api/config')['key']
        if key.get('configured') and not key.get('persisted'):
            raise UpdateError('First save the API key with Windows encryption in Settings. The backend stays active.')
        print('Stopping the backend cleanly. This does not stop an active charge.', flush=True)
        progress('stopping')
        print(client.action('stop', data_dir, port, no_browser=True), flush=True)

    require_tracked_clean()
    protect_local_files()
    # Apply exactly the preflighted fetch; a second fetch could introduce new collisions.
    progress('applying')
    git('merge', '--ff-only', '--no-autostash', '--no-overwrite-ignore', 'FETCH_HEAD')
    print('\nUpdate complete. Program folder:', target, flush=True)
    print('No charging start was sent. You can use Start.cmd or Start-Background.cmd in THIS folder.', flush=True)
    return target


def app_job(job, directory, port, data_dir, *, update_function=None, client=None, runner=None):
    from citifoxcharger.app_update import AppUpdater
    updater=AppUpdater(directory,data_dir,port)
    state=updater.state()
    if state['job_id']!=job or state['phase']!='queued':
        return 1
    client=client or LocalControl(port)
    runner=runner or subprocess.run
    target=Path(directory)
    previous=None
    def start_backend(folder):
        updater.write(job,'starting')
        result=runner([sys.executable,str(Path(folder)/'run.py'),'--background','--no-browser',
                       '--port',str(port),'--data-dir',str(Path(data_dir).resolve())],cwd=folder,timeout=30)
        if result.returncode:
            raise UpdateError('Backend startup failed.')
        health=client.health()
        if health is None or (previous and health.get('instance_id')==previous.get('instance_id')):
            raise UpdateError('A new backend instance is not confirmed.')
    try:
        time.sleep(1) # Let the initiating HTTP response reach the browser before shutdown.
        previous=client.health()
        if previous is None:
            raise UpdateError('The original backend is not running.')
        target=(update_function or update)(directory,port,data_dir,control=client,
                          progress=lambda phase:updater.write(job,phase),timeout=120)
        start_backend(target)
        updater.write(job,'complete')
        return 0
    except (UpdateError,ControlError,OSError,subprocess.TimeoutExpired) as error:
        print('App update failed:',error,flush=True)
        # A failed merge/start must not silently leave the tablet backend stopped.
        if previous is not None:
            try:
                if client.health() is None:start_backend(target)
            except (UpdateError,ControlError,OSError,subprocess.TimeoutExpired):
                print('Backend recovery is unconfirmed. Run Start.cmd on the tablet.',flush=True)
        updater.write(job,'failed')
        return 1


def choose_start(directory, port, data_dir):
    """Start only after an explicit choice, using the updated code and same profile."""
    print('\nWhat would you like to do next?')
    print('  1  Start - open the dashboard in Brave full screen')
    print('  2  Start-Background - backend only, without opening a browser')
    print('  3  Leave the backend stopped (default)')
    while True:
        try:
            choice = input('Choose 1, 2 or 3 [3]: ').strip()
        except (EOFError, KeyboardInterrupt):
            choice = '3'
        if choice in ('', '3'):
            print('Backend left stopped. Run Start.cmd or Start-Background.cmd when ready.')
            return 0
        if choice in ('1', '2'):
            break
        print('Please enter 1, 2 or 3.')
    command = [sys.executable, str(Path(directory) / 'run.py'), '--background',
               '--port', str(port), '--data-dir', str(Path(data_dir).resolve())]
    command += ['--browser', 'brave', '--fullscreen'] if choice == '1' else ['--no-browser']
    result = subprocess.run(command, cwd=directory)
    if result.returncode:
        print('Update completed, but startup is unconfirmed. Check the message above or background.log.')
        print('Run Start.cmd or Start-Background.cmd again after checking the error.')
        return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description='Safely update CitiFoxCharger from GitHub')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data-dir', type=Path, default=default_data_dir())
    parser.add_argument('--choose-start', action='store_true', help='Offer a startup choice after a successful update')
    parser.add_argument('--app-job', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Invalid backend port.')
    args.data_dir = args.data_dir.resolve()
    if args.app_job:
        return app_job(args.app_job,Path(__file__).parent,args.port,args.data_dir)
    try:
        target = update(Path(__file__).parent, args.port, args.data_dir)
    except (UpdateError, ControlError, OSError) as error:
        print('Update did not complete:', error, flush=True)
        print('If the backend already stopped, run Start.cmd after checking the error.', flush=True)
        return 1
    if args.choose_start:
        try:
            return choose_start(target, args.port, args.data_dir)
        except OSError:
            print('Update completed, but startup failed. Run Start.cmd or Start-Background.cmd in the updated folder.')
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
