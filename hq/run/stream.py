import json
import re
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class StreamEvent:
    kind: str
    session_id: str | None = None
    text: str | None = None
    rate_limited: bool = False


_LIMIT = re.compile(r'\b(429|rate.?limit|usage limit|quota)\b', re.I)


def parse_json(line, tool):
    try:
        data = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return StreamEvent('working')
    kind = data.get('type', '')
    session = None
    if tool in ('claude', 'agy') and kind == 'system' and data.get('subtype') == 'init':
        session = data.get('session_id')
    elif tool == 'codex' and kind == 'thread.started':
        session = data.get('thread_id')
    elif tool == 'kimi' and kind == 'session':
        session = data.get('session_id')
    elif tool == 'opencode':
        session = data.get('sessionID')
    if session:
        return StreamEvent('session', session_id=str(session))
    mapped = {'error': 'error', 'result': 'done', 'turn.completed': 'done',
              'started': 'started', 'waiting': 'waiting'}.get(kind, 'working')
    return StreamEvent(mapped)


def parse_line(tool, line, launcher=None):
    if launcher is None:
        from hq.run.launchers import REGISTRY
        launcher = REGISTRY[tool]
    event = launcher.parse(line)
    if event is not None and _LIMIT.search(line):
        event = replace(event, rate_limited=True)
    return event
