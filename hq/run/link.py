import os
from contextlib import closing
from pathlib import Path

from hq.config import is_forbidden
from hq.run.wsl import to_wsl_path


def _normal(path):
    return os.path.normcase(os.path.normpath(str(path)))


def find_session(store, harness, workdir, started_at, window_s=60):
    paths = {_normal(workdir)}
    try:
        paths.add(_normal(to_wsl_path(workdir)))
    except ValueError:
        pass
    with closing(store.conn()) as conn:
        rows = conn.execute(
            'SELECT id, project FROM sessions WHERE harness=? AND started_at>=? '
            'AND started_at<=? ORDER BY started_at DESC',
            (harness, started_at, started_at + window_s),
        )
        return next((sid for sid, project in rows if project and _normal(project) in paths), None)


def add_profile_paths(cfg):
    for account in cfg.accounts:
        for profile in account.profiles:
            if profile.tool == 'claude':
                source, path = cfg.sources.get('claude_code'), Path(profile.config_dir) / 'projects'
            elif profile.tool == 'codex':
                source = cfg.sources.get('codex')
                directory = profile.config_dir
                if os.name == 'nt' and directory.startswith('/'):
                    directory = '\\\\wsl.localhost\\Debian' + directory.replace('/', '\\')
                path = Path(directory) / 'sessions'
            else:
                continue
            if source is not None and not is_forbidden(path) and all(_normal(path) != _normal(p) for p in source.paths):
                source.paths.append(path)
