"""Manage the isolated Git workspace for an editing run."""
import re
import subprocess
import threading
from pathlib import Path

from hq.config import is_forbidden


_lock = threading.RLock()


def _path(path: Path) -> Path:
    path = Path(path)
    if is_forbidden(path):
        raise ValueError('Forbidden workspace path')
    return path


def _git(project: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(['git', '-C', str(project), *args], timeout=60,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)


def _branch(branch: str) -> str:
    if not re.fullmatch(r'hq/run-[A-Za-z0-9_-]+', branch):
        raise ValueError('Invalid run branch')
    return branch


def is_git_repo(path: Path) -> bool:
    path = _path(path)
    result = _git(path, 'rev-parse', '--is-inside-work-tree', check=False)
    return result.returncode == 0 and result.stdout.strip() == b'true'


def create(project: Path, run_id: str, root: Path) -> tuple[Path, str | None, str | None]:
    project, root = _path(project), _path(root)
    branch = _branch('hq/run-' + run_id)
    workdir = _path(root / run_id)
    with _lock:
        if not is_git_repo(project):
            return project, None, None
        base = _git(project, 'rev-parse', 'HEAD').stdout.decode('ascii').strip()
        root.mkdir(parents=True, exist_ok=True)
        _git(project, 'worktree', 'add', '-b', branch, str(workdir), base)
    return workdir, branch, base


def snapshot(workdir: Path, run_id: str) -> bool:
    workdir = _path(workdir)
    _branch('hq/run-' + run_id)
    with _lock:
        if not _git(workdir, 'status', '--porcelain', '--untracked-files=normal').stdout:
            return False
        _git(workdir, 'add', '-A')
        _git(workdir, '-c', 'user.name=Agent HQ', '-c', 'user.email=agent-hq@localhost',
             'commit', '-q', '--no-verify', '-m', 'hq run ' + run_id)
        return True


def diff(project: Path, branch: str, base_commit: str, cap: int = 2_000_000,
         *, workdir: Path | None = None) -> tuple[str, bool]:
    project = _path(project)
    _branch(branch)
    if not re.fullmatch(r'[0-9a-fA-F]{40,64}', base_commit) or cap < 0:
        raise ValueError('Invalid base commit or diff cap')
    if workdir is not None:
        snapshot(workdir, branch.removeprefix('hq/run-'))
    output = _git(project, 'diff', '--no-ext-diff', base_commit, branch, '--').stdout
    return output[:cap].decode('utf-8', errors='replace'), len(output) > cap


def merge(project: Path, branch: str, *, workdir: Path | None = None) -> str:
    project, branch = _path(project), _branch(branch)
    with _lock:
        if workdir is not None:
            snapshot(workdir, branch.removeprefix('hq/run-'))
        if _git(project, 'status', '--porcelain', '--untracked-files=normal').stdout:
            return 'dirty'
        result = _git(project, 'merge', '--no-edit', branch, check=False)
        if result.returncode == 0:
            return 'merged'
        if _git(project, 'diff', '--name-only', '--diff-filter=U').stdout:
            _git(project, 'merge', '--abort')
            return 'conflict'
        result.check_returncode()
    raise AssertionError('unreachable')


def discard(project: Path, workdir: Path, branch: str) -> None:
    project, workdir, branch = _path(project), _path(workdir), _branch(branch)
    with _lock:
        _git(project, 'worktree', 'remove', '--force', str(workdir))
        _git(project, 'branch', '-D', branch)
