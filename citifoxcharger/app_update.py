"""Local-admin update jobs; a detached worker survives the backend restart."""
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from .config import atomic_write
from . import __version__

MESSAGES = {
    'idle':'No update running.', 'queued':'Update queued.',
    'checking':'Checking GitHub. GitHub sign-in may be required.',
    'stopping':'Stopping the backend safely.', 'applying':'Applying the Git update.',
    'starting':'Starting the backend. This page will reload.',
    'complete':'Update complete. Reloading the dashboard.',
    'failed':'Update did not complete. Check update.log or run Update.cmd on the tablet.'}


class AppUpdater:
    def __init__(self, directory, data_dir, port, spawn=None):
        self.directory, self.data_dir, self.port = Path(directory).resolve(), Path(data_dir).resolve(), port
        self.path = self.data_dir/'app-update.json'
        self.spawn = spawn or subprocess.Popen
        self.process = None

    def state(self):
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(value,dict) or value.get('phase') not in MESSAGES:
                raise ValueError()
            job = value.get('job_id')
            if not isinstance(job,str) or not re.fullmatch('[0-9a-f]{32}',job):
                raise ValueError()
            phase = value['phase']
            if phase not in ('complete','failed') and self.process is not None and self.process.poll() is not None:
                self.write(job,'failed')
                phase = 'failed'
            return {'job_id':job, 'phase':phase, 'message':MESSAGES[phase]}
        except (OSError,ValueError,TypeError):
            return {'job_id':None,'phase':'idle','message':MESSAGES['idle']}

    def write(self, job, phase):
        if phase not in MESSAGES or not re.fullmatch('[0-9a-f]{32}',job):
            raise ValueError('Invalid update job.')
        atomic_write(self.path,json.dumps({'job_id':job,'phase':phase,'at':time.time(),
                                         'version':__version__}).encode())

    def launch(self, service):
        with service.lock:
            current = self.state()
            if current['phase'] not in ('idle','complete','failed'):
                return current
            if service.stopping or (service.charging.active and not service.charging.state()['can_shutdown']):
                raise ValueError('Stop charging and wait for confirmation before updating.')
            if service.secrets.key and not service.secrets.persisted:
                raise ValueError('First save the API key with Windows encryption in Settings.')
            job = uuid.uuid4().hex
            self.write(job,'queued')
            try:
                options = {'cwd':str(self.directory),'stdin':subprocess.DEVNULL}
                if os.name=='nt':options['creationflags']=subprocess.CREATE_NO_WINDOW
                else:options['start_new_session']=True
                env = os.environ.copy()
                env['GIT_TERMINAL_PROMPT']='0'
                options['env']=env
                with (self.data_dir/'update.log').open('ab',buffering=0) as log:
                    self.process=self.spawn([sys.executable,'-u',str(self.directory/'update.py'),
                        '--app-job',job,'--port',str(self.port),'--data-dir',str(self.data_dir)],
                        stdout=log,stderr=log,**options)
                service.app_update_pending=True
            except OSError:
                self.write(job,'failed')
                raise ValueError('The update helper could not start. Run Update.cmd on the tablet.') from None
            return self.state()
