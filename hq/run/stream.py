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
    if tool == 'agy' and 'event' in data:
        return _agy(data)
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
    if kind == 'rate_limit_event':
        # Claude Code / Antigravity print this on every run; only 'rejected' means blocked.
        info = data.get('rate_limit_info') or {}
        return StreamEvent('working', rate_limited=info.get('status') == 'rejected')
    failed = kind in ('error', 'turn.failed') or (kind == 'result' and data.get('is_error') is True)
    if failed:
        return StreamEvent('error', text=json.dumps(data)[:2000])
    mapped = {'result': 'done', 'turn.completed': 'done',
              'started': 'started', 'waiting': 'waiting'}.get(kind, 'working')
    return StreamEvent(mapped)


def _agy(data):
    """Antigravity 1.4.9 stream: {"event": "init"|"step_update"|"result", ...} (seen on real runs)."""
    event = data.get('event')
    if event == 'init' and data.get('conversation_id'):
        return StreamEvent('session', session_id=str(data['conversation_id']))
    if event == 'result':
        result = data.get('result') or {}
        if result.get('status') == 'SUCCESS':
            return StreamEvent('done')
        return StreamEvent('error', text=json.dumps(result)[:2000])
    return StreamEvent('working')


def parse_line(tool, line, launcher=None):
    if launcher is None:
        from hq.run.launchers import REGISTRY
        launcher = REGISTRY[tool]
    event = launcher.parse(line)
    if event is None:
        return None
    if event.session_id:
        # Store ids the way the readers do ('<harness>:<id>') so the run joins its session.
        event = replace(event, session_id=f'{launcher.harness}:{event.session_id}')
    # Only an error event can mean a rate limit: an agent that merely mentions "429" or "quota"
    # in its work must not block an account.
    if event.kind == 'error' and _LIMIT.search(event.text or line):
        event = replace(event, rate_limited=True)
    return event
