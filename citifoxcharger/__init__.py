"""Local EV charging dashboard. No external Python packages required."""
__version__ = "1.8.12"
__release_date__ = "2026-10-22"


def release_info(directory=None):
    """Public release identity, read once at server startup; no paths or credentials."""
    import json
    import re
    import subprocess
    from datetime import date
    from pathlib import Path
    root = Path(directory).resolve() if directory else Path(__file__).resolve().parent.parent
    result = {'version':__version__,'git_hash':None,'date':__release_date__}
    def valid(commit, day):
        return (isinstance(commit,str) and re.fullmatch(r'[0-9a-f]{40,64}',commit)
                and isinstance(day,str) and date.fromisoformat(day).isoformat()==day)
    try:
        if (root/'.git').exists():
            def git(*args):
                return subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,
                                      check=True,timeout=2).stdout.strip()
            if Path(git('rev-parse','--show-toplevel')).resolve()==root:
                commit,day=git('log','-1','--format=%H%n%cs').splitlines()
                if valid(commit,day):result.update(git_hash=commit[:8],date=day)
        else:
            value=json.loads((root/'citifoxcharger/build-info.json').read_text(encoding='utf-8'))
            if value.get('version')==__version__ and valid(value.get('git_hash'),value.get('date')):
                result.update(git_hash=value['git_hash'][:8],date=value['date'])
    except (OSError,ValueError,TypeError,AttributeError,subprocess.SubprocessError):
        pass
    return result
