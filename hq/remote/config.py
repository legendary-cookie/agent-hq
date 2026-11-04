import json
import re
from dataclasses import dataclass
from pathlib import Path

SOURCES = {
    'claude_code': ('.claude/projects',),
    'codex': ('.codex/sessions',),
    'opencode': ('.local/share/opencode/opencode.db',),
    'antigravity': ('.gemini/antigravity-cli/conversations', '.gemini/antigravity-cli/history.jsonl'),
    'gemini_cli': ('.gemini/tmp',),
    'kimi': ('.kimi-code/sessions', '.kimi/sessions'),
    'dsh': ('.dsh/sessions',),
}


@dataclass(frozen=True)
class Host:
    name: str
    ssh: str
    os: str
    container: str | None
    interval_s: int
    sources: tuple[str, ...]
    run: bool = False
    projects: tuple[dict, ...] = ()


def _host(entry):
    if not isinstance(entry, dict):
        raise ValueError('entry must be an object')
    name, ssh, os_name = (entry.get(key) for key in ('name', 'ssh', 'os'))
    container = entry.get('container')
    interval = entry.get('interval_s', 60)
    sources = entry.get('sources')
    for value, pattern in ((name, r'[a-z0-9][a-z0-9-]{0,31}'),
                           (ssh, r'[A-Za-z0-9][A-Za-z0-9._@-]{0,127}')):
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise ValueError('invalid name or ssh')
    if container is not None and (not isinstance(container, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,62}', container)):
        raise ValueError('invalid container')
    if os_name not in ('linux', 'macos', 'windows'):
        raise ValueError('invalid os')
    if type(interval) is not int or not 30 <= interval <= 3600:
        raise ValueError('invalid interval')
    if not isinstance(sources, list) or any(not isinstance(s, str) or s not in SOURCES for s in sources):
        raise ValueError('invalid sources')
    run = entry.get('run', False)
    projects = entry.get('projects', [])
    if type(run) is not bool or not isinstance(projects, list):
        raise ValueError('invalid run or projects')
    names = set()
    for project in projects:
        if not isinstance(project, dict) or set(project) != {'name', 'path'}:
            raise ValueError('invalid project')
        pname, path = project['name'], project['path']
        if (not isinstance(pname, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', pname)
                or pname in names or not isinstance(path, str) or not path.startswith('/')
                or '..' in path.split('/') or '\0' in path):
            raise ValueError('invalid project')
        names.add(pname)
    return Host(name, ssh, os_name, container, interval, tuple(sources), run, tuple(projects))


def load_hosts(config_dir: Path) -> tuple[list[Host], list[str]]:
    try:
        data = json.loads((config_dir / 'hosts.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        return [], []
    except (OSError, ValueError) as exc:
        return [], [f'hosts.json: {exc}']
    if not isinstance(data, dict) or not isinstance(data.get('hosts'), list):
        return [], ['hosts.json: hosts must be a list']
    hosts, warnings, names = [], [], set()
    for index, entry in enumerate(data['hosts']):
        try:
            host = _host(entry)
            if host.name in names:
                raise ValueError('duplicate name')
            names.add(host.name)
            hosts.append(host)
        except (ValueError, TypeError) as exc:
            warnings.append(f'host {index}: {exc}')
    return hosts, warnings
