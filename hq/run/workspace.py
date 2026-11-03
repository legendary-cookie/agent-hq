"""Manage isolated Git workspaces without repository discovery in worktrees."""
import re
import subprocess
import tempfile
import threading
from pathlib import Path
from hq.config import is_forbidden

_lock = threading.RLock()
_hooks = Path(tempfile.gettempdir()) / 'agent-hq-empty-hooks'

def configure(data_dir):
    global _hooks
    _hooks = Path(data_dir) / 'empty-hooks'
    _hooks.mkdir(parents=True, exist_ok=True)

def _path(path):
    path = Path(path)
    if is_forbidden(path): raise ValueError('Forbidden workspace path')
    return path

def _command(repo_args, *args):
    _hooks.mkdir(parents=True, exist_ok=True)
    return ['git', '-c', 'core.fsmonitor=false', '-c', f'core.hooksPath={_hooks}',
            '-c', 'core.sshCommand=false', '-c', 'protocol.allow=never',
            '-c', 'diff.external=', '-c', 'core.pager=cat', '-c', 'credential.helper=',
            *repo_args, *args]

def _repo(project):
    return ['--git-dir', str(project / '.git'), '--work-tree', str(project)]

def _git(repo_args, *args, check=True):
    return subprocess.run(_command(repo_args, *args), timeout=60, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)

def _branch(branch):
    if not re.fullmatch(r'hq/run-[A-Za-z0-9_-]+', branch): raise ValueError('Invalid run branch')
    return branch

def is_git_repo(path):
    path = _path(path)
    result = _git(_repo(path), 'rev-parse', '--git-dir', check=False)
    return result.returncode == 0

def create(project, run_id, root):
    project, root = _path(project), _path(root)
    branch = _branch('hq/run-' + run_id)
    workdir = _path(root / run_id)
    with _lock:
        if not is_git_repo(project): return project, None, None, None
        base = _git(_repo(project), 'rev-parse', 'HEAD').stdout.decode('ascii').strip()
        common = Path(_git(_repo(project), 'rev-parse', '--git-common-dir').stdout.decode().strip()).resolve()
        root.mkdir(parents=True, exist_ok=True)
        _git(_repo(project), 'worktree', 'add', '-b', branch, str(workdir), base)
        return workdir, branch, base, str(common / 'worktrees' / workdir.name)

def _validate(workdir, admin, project):
    try:
        dotgit = workdir / '.git'
        common = (admin / 'commondir').read_text().strip()
        if dotgit.is_symlink() or not dotgit.is_file():
            raise ValueError
        # Exactly the one line git wrote. Git on Windows writes forward slashes, so compare paths.
        lines = dotgit.read_text().splitlines()
        if (len(lines) != 1 or not lines[0].startswith('gitdir: ')
                or Path(lines[0][len('gitdir: '):]).resolve() != admin.resolve()
                or (admin / 'config.worktree').exists() or (admin / common).resolve() != (project / '.git').resolve()):
            raise ValueError
    except (OSError, UnicodeError, ValueError):
        raise ValueError('worktree .git was changed by the run') from None

def snapshot(workdir, run_id, *, project=None, git_dir=None):
    workdir = _path(workdir)
    _branch('hq/run-' + run_id)
    if project is None:
        raise ValueError('project required for snapshot')
    project = _path(project)
    admin = Path(git_dir) if git_dir else project / '.git' / 'worktrees' / workdir.name
    _validate(workdir, admin, project)
    repo = ['--git-dir', str(admin), '--work-tree', str(workdir)]
    with _lock:
        if not _git(repo, 'status', '--porcelain', '--untracked-files=normal').stdout: return False
        _git(repo, 'add', '-A')
        _git(repo, '-c', 'user.name=Agent HQ', '-c', 'user.email=agent-hq@localhost', 'commit', '-q', '--no-verify', '-m', 'hq run ' + run_id)
        return True

def revision(project, branch):
    return _git(_repo(_path(project)), 'rev-parse', _branch(branch)).stdout.decode('ascii').strip()

def diff(project, branch, base_commit, cap=2_000_000, *, workdir=None, git_dir=None):
    project = _path(project); _branch(branch)
    if not re.fullmatch(r'[0-9a-fA-F]{40,64}', base_commit) or cap < 0: raise ValueError('Invalid base commit or diff cap')
    if workdir is not None: snapshot(workdir, branch.removeprefix('hq/run-'), project=project, git_dir=git_dir)
    repo = _repo(project)
    stat = _git(repo, 'diff', '--no-ext-diff', '--no-textconv', '--shortstat', base_commit, branch, '--').stdout.decode()
    changed = sum(int(n) for n in re.findall(r'(\d+) (?:insertion|deletion)', stat))
    if changed > 50000: raise ValueError('diff exceeds 50,000 changed lines')
    proc = subprocess.Popen(_command(repo, 'diff', '--no-ext-diff', '--no-textconv', base_commit, branch, '--'), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        output = proc.stdout.read(cap + 1)
        if len(output) > cap: proc.kill()
        proc.wait(timeout=60)
        if len(output) <= cap and proc.returncode: raise subprocess.CalledProcessError(proc.returncode, proc.args)
    finally:
        if proc.poll() is None: proc.kill(); proc.wait()
        proc.stdout.close()
    return output[:cap].decode('utf-8', errors='replace'), len(output) > cap

def merge(project, branch, *, workdir=None, git_dir=None, commit=None):
    project, branch = _path(project), _branch(branch)
    with _lock:
        if workdir is not None: snapshot(workdir, branch.removeprefix('hq/run-'), project=project, git_dir=git_dir)
        if commit is not None and revision(project, branch) != commit: raise ValueError('the run changed after you viewed the diff; view it again')
        if _git(_repo(project), 'status', '--porcelain', '--untracked-files=normal').stdout: return 'dirty'
        result = _git(_repo(project), 'merge', '--no-edit', commit or branch, check=False)
        if result.returncode == 0: return 'merged'
        if _git(_repo(project), 'diff', '--name-only', '--diff-filter=U').stdout:
            _git(_repo(project), 'merge', '--abort'); return 'conflict'
        result.check_returncode()

def discard(project, workdir, branch):
    project, workdir, branch = _path(project), _path(workdir), _branch(branch)
    with _lock:
        _git(_repo(project), 'worktree', 'remove', '--force', str(workdir))
        _git(_repo(project), 'branch', '-D', branch)
