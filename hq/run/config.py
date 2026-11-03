import json
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path

from hq.config import is_forbidden


@dataclass
class RunConfig:
    run_mode: bool = False
    max_concurrent: int = 3
    default_time_limit_min: int = 30
    max_time_limit_min: int = 240
    pass_env: dict[str, list[str]] = field(default_factory=dict)
    show_untested: bool = False
    approval_timeout_min: int = 15


@dataclass(frozen=True)
class Project:
    name: str
    path: Path


def _clamp(value: object, default: int, maximum: int) -> int:
    return max(1, min(maximum, value)) if type(value) is int else default


def load_run_config(config_dir: Path) -> RunConfig:
    path = config_dir / 'run.json'
    if not path.exists():
        return RunConfig()
    data = json.loads(path.read_text(encoding='utf-8'))
    maximum = _clamp(data.get('max_time_limit_min'), 240, 240)
    names = data.get('pass_env', {})
    if not isinstance(names, dict) or any(
        not isinstance(tool, str) or not isinstance(values, list) or
        any(not isinstance(name, str) or not re.fullmatch(r'[A-Z_][A-Z0-9_]*', name) for name in values)
        for tool, values in names.items()
    ):
        raise ValueError('Invalid pass_env names')
    return RunConfig(data.get('run_mode') is True,
                     _clamp(data.get('max_concurrent'), 3, 240),
                     _clamp(data.get('default_time_limit_min'), min(30, maximum), maximum),
                     maximum, names, data.get('show_untested') is True,
                     _clamp(data.get('approval_timeout_min'), 15, 120))


def load_projects(config_dir: Path) -> list[Project]:
    path = config_dir / 'projects.json'
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding='utf-8'))
    projects = []
    for item in data['projects']:
        project_path = Path(item['path']).expanduser()
        if is_forbidden(project_path) or not project_path.is_dir():
            warnings.warn(f"Ignoring invalid project: {item['name']}", stacklevel=2)
            continue
        projects.append(Project(item['name'], project_path.resolve()))
    return projects
