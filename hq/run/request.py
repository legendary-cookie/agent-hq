import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from hq.config import is_forbidden
from hq.run.config import Project, RunConfig


@dataclass(frozen=True)
class RunRequest:
    tool: str
    model: str | None
    project: str
    prompt: str
    level: str
    guard_mode: str
    profile: str | None
    time_limit_min: int


def parse_request(data: dict, run_cfg: RunConfig, projects: list[Project], tools: dict[str, set[str]]) -> RunRequest:
    tool = data.get('tool')
    if not isinstance(tool, str) or tool not in tools:
        raise ValueError('Unknown tool')
    level = data.get('level', 'edit')
    if not isinstance(level, str) or level not in tools[tool]:
        raise ValueError('Level is not offered for this tool')
    model = data.get('model')
    if model is not None and (not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,79}', model)):
        raise ValueError('Invalid model')
    prompt = data.get('prompt')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 100000:
        raise ValueError('Prompt must contain 1 to 100000 characters')
    name = data.get('project')
    matches = [p for p in projects if p.name == name]
    if len(matches) != 1:
        raise ValueError('Project is not allowlisted')
    path = Path(matches[0].path)
    if is_forbidden(path) or not path.is_dir() or path != path.resolve():
        raise ValueError('Project path has changed or is invalid')
    guard = data.get('guard_mode', 'block')
    if guard not in ('warn', 'block', 'auto'):
        raise ValueError('Invalid guard mode')
    profile = data.get('profile')
    if profile is not None and not isinstance(profile, str):
        raise ValueError('Invalid profile')
    limit = data.get('time_limit_min')
    if limit is None:
        limit = run_cfg.default_time_limit_min
    if type(limit) is not int or not 1 <= limit <= min(240, run_cfg.max_time_limit_min):
        raise ValueError('Invalid time limit')
    return RunRequest(tool, model, name, prompt, level, guard, profile, limit)


def request_hash(req: RunRequest) -> str:
    payload = json.dumps(asdict(req), sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()
