"""Standalone POSIX remote run helper; compatible with Python 3.8."""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

TOOLS = {'claude', 'codex', 'opencode', 'agy'}
ALLOWED_ENV = {'PATH', 'HOME', 'USER', 'LOGNAME', 'SHELL', 'TERM', 'LANG', 'LC_ALL',
               'TMP', 'TEMP', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME'}
EXTRA_ENV = {'OPENCODE_CONFIG_CONTENT', 'CLAUDE_CONFIG_DIR', 'CODEX_HOME'}


def checked(request):
    rid = request.get('id')
    if not isinstance(rid, str) or not re.fullmatch(r'run_[A-Za-z0-9_-]{1,80}', rid):
        raise ValueError('Invalid run id')
    projects = request.get('projects')
    if not isinstance(projects, list):
        raise ValueError('Invalid projects')
    matches = [p for p in projects if isinstance(p, dict) and p.get('name') == request.get('project')]
    if len(matches) != 1:
        raise ValueError('Project is not allowlisted')
    path = matches[0].get('path')
    if not isinstance(path, str) or not path.startswith('/') or '..' in path.split('/') or '\0' in path:
        raise ValueError('Invalid project path')
    project = Path(path)
    if project.resolve() != project or not project.is_dir() or not (project / '.git').is_dir():
        raise ValueError('Invalid project repository')
    return rid, project


def git(project, *args, admin=None, workdir=None, check=True):
    with tempfile.TemporaryDirectory(prefix='hq-hooks-') as hooks:
        cmd = ['git', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=' + hooks,
               '-c', 'core.sshCommand=false', '-c', 'protocol.allow=never',
               '-c', 'diff.external=', '-c', 'core.pager=cat', '-c', 'credential.helper=',
               '--git-dir', str(admin or project / '.git'), '--work-tree', str(workdir or project)]
        return subprocess.run(cmd + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check, timeout=60)


def paths(rid):
    root = Path.home() / '.agent-hq'
    return root / 'runs' / (rid + '.json'), root / 'runs' / (rid + '.pid'), root / 'worktrees' / rid


def load(rid, project):
    meta, _, workdir = paths(rid)
    data = json.loads(meta.read_text())
    if data['project'] != str(project) or data['workdir'] != str(workdir):
        raise ValueError('Run project mismatch')
    admin = project / '.git' / 'worktrees' / rid
    dotgit = workdir / '.git'
    lines = dotgit.read_text().splitlines()
    if (dotgit.is_symlink() or len(lines) != 1 or not lines[0].startswith('gitdir: ')
            or Path(lines[0][8:]).resolve() != admin.resolve() or (admin / 'config.worktree').exists()
            or (admin / (admin / 'commondir').read_text().strip()).resolve() != (project / '.git').resolve()):
        raise ValueError('worktree .git was changed by the run')
    return data, workdir, admin


def snapshot(project, rid, workdir, admin):
    if git(project, 'status', '--porcelain', '--untracked-files=normal', admin=admin, workdir=workdir).stdout:
        git(project, 'add', '-A', admin=admin, workdir=workdir)
        if git(project, 'diff', '--cached', '--quiet', admin=admin, workdir=workdir, check=False).returncode:
            git(project, '-c', 'user.name=Agent HQ', '-c', 'user.email=agent-hq@localhost',
                'commit', '-q', '--no-verify', '-m', 'hq run ' + rid, admin=admin, workdir=workdir)


def kill_group(pid):
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    time.sleep(3)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def start(request, rid, project):
    argv = request.get('argv')
    if (not isinstance(argv, list) or not argv or argv[0] not in TOOLS
            or any(not isinstance(a, str) or '\0' in a for a in argv)):
        raise ValueError('Invalid tool argv')
    executable = shutil.which(argv[0])
    if not executable:
        raise ValueError('Tool unavailable')
    level = request.get('level')
    if level not in ('read_only', 'ask', 'edit', 'full_auto'):
        raise ValueError('Invalid level')
    extra = request.get('env', {})
    if not isinstance(extra, dict) or any(k not in EXTRA_ENV or not isinstance(v, str) or '\0' in v for k, v in extra.items()):
        raise ValueError('Invalid environment')
    meta, pidfile, workdir = paths(rid)
    meta.parent.mkdir(parents=True, exist_ok=True)
    workdir.parent.mkdir(parents=True, exist_ok=True)
    branch = 'hq/run-' + rid
    base = git(project, 'rev-parse', '--verify', 'HEAD^{commit}').stdout.decode().strip()
    git(project, 'worktree', 'add', '-b', branch, str(workdir), base)
    meta.write_text(json.dumps(dict(project=str(project), workdir=str(workdir), branch=branch, base=base)))
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            os.setsid()
            os.close(write_fd)
            os.dup2(read_fd, 0)
            os.close(read_fd)
            os.chdir(str(workdir))
            os.environ.clear()
            os.environ.update(child_env)
            os.execvp(executable, argv)
        except BaseException:
            os._exit(127)
    os.close(read_fd)
    pidfile.write_text(str(pid))
    initial = request.get('stdin_text')
    dropped = threading.Event()
    def forward():
        try:
            if initial:
                os.write(write_fd, initial.encode('utf-8'))
            while True:
                chunk = os.read(0, 65536)
                if not chunk:
                    dropped.set()
                    kill_group(pid)
                    return
                os.write(write_fd, chunk)
        except (BrokenPipeError, OSError):
            pass
        finally:
            os.close(write_fd)
    thread = threading.Thread(target=forward, daemon=True)
    thread.start()
    _, status = os.waitpid(pid, 0)
    if dropped.is_set():
        thread.join()
    pidfile.unlink(missing_ok=True)
    return os.WEXITSTATUS(status) if os.WIFEXITED(status) else 128 + os.WTERMSIG(status)


def main(request_text):
    global child_env
    try:
        request = json.loads(request_text)
        action = request.get('action')
        if action == 'tools':
            print(json.dumps({'tools': sorted(t for t in TOOLS if shutil.which(t)), 'home': str(Path.home())}))
            return
        rid, project = checked(request)
        if action == 'start':
            child_env = {k: v for k, v in os.environ.items() if k in ALLOWED_ENV}
            child_env.update(request.get('env', {}))
            sys.exit(start(request, rid, project))
        meta, pidfile, workdir = paths(rid)
        if action == 'stop':
            if pidfile.exists():
                kill_group(int(pidfile.read_text()))
            result = {'ok': True}
        else:
            data, workdir, admin = load(rid, project)
            branch = data['branch']
            if branch != 'hq/run-' + rid or not re.fullmatch(r'[0-9a-f]{40,64}', data['base']):
                raise ValueError('Invalid run metadata')
            if action in ('diff', 'merge'):
                snapshot(project, rid, workdir, admin)
            commit = git(project, 'rev-parse', branch).stdout.decode().strip()
            if action == 'diff':
                stat = git(project, 'diff', '--no-ext-diff', '--no-textconv', '--shortstat', data['base'], branch, '--').stdout.decode()
                if sum(int(n) for n in re.findall(r'(\d+) (?:insertion|deletion)', stat)) > 50000:
                    raise ValueError('diff exceeds 50,000 changed lines')
                diff = git(project, 'diff', '--no-ext-diff', '--no-textconv', data['base'], branch, '--').stdout
                result = {'diff': diff[:2000000].decode('utf-8', 'replace'), 'truncated': len(diff) > 2000000, 'commit': commit, 'branch': branch}
            elif action == 'merge':
                if request.get('commit') != commit:
                    raise ValueError('the run changed after you viewed the diff; view it again')
                if git(project, 'status', '--porcelain', '--untracked-files=normal').stdout:
                    state = 'dirty'
                else:
                    merged = git(project, 'merge', '--no-edit', commit, check=False)
                    state = 'merged' if merged.returncode == 0 else 'conflict'
                    if state == 'conflict':
                        git(project, 'merge', '--abort', check=False)
                result = {'state': state}
            elif action == 'discard':
                git(project, 'worktree', 'remove', '--force', str(workdir))
                git(project, 'branch', '-D', branch)
                meta.unlink()
                result = {'ok': True}
            else:
                raise ValueError('Invalid action')
        print(json.dumps(result))
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
