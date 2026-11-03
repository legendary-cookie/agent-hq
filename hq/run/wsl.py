import re
import subprocess
import time
from pathlib import Path, PureWindowsPath


def to_wsl_path(p: Path) -> str:
    windows = PureWindowsPath(str(p))
    if re.fullmatch(r'[A-Za-z]:', windows.drive):
        return '/mnt/' + windows.drive[0].lower() + '/' + '/'.join(windows.parts[1:])
    if windows.drive:
        raise ValueError('Unsupported Windows path')
    return str(p).replace('\\', '/')


def wsl_stop(pidfile: str) -> None:
    if not re.fullmatch(r'/tmp/hq-runs/run_[A-Za-z0-9_-]+\.pid', pidfile):
        raise ValueError('Invalid WSL pidfile')
    result = subprocess.run(['wsl', '-d', 'Debian', '--', 'cat', pidfile],
                            capture_output=True, text=True, check=False)
    pgid = result.stdout.strip()
    if result.returncode != 0 or not re.fullmatch(r'[1-9][0-9]*', pgid):
        return
    for signal in ('TERM', 'KILL'):
        subprocess.run(['wsl', '-d', 'Debian', '--', '/bin/kill', '-' + signal, '--', '-' + pgid], check=False)
        if signal == 'TERM': time.sleep(3)
