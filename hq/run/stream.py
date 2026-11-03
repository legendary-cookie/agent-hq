import re
import json
from dataclasses import dataclass, replace


def valid_session_id(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value) is not None


def _session(value):
    return StreamEvent('session', session_id=value) if valid_session_id(value) else StreamEvent('warning')


@dataclass(frozen=True)
class StreamEvent:
    kind: str
    session_id: str | None = None
    text: str | None = None
    rate_limited: bool = False
    request: dict | None = None


def _limited(data, tool):
    kind = data.get('type', data.get('event', ''))
    err = data.get('error')
    info = err if isinstance(err, dict) else {}
    code = info.get('code', info.get('status'))
    if tool == 'claude':
        if kind == 'rate_limit_event': return (data.get('rate_limit_info') or {}).get('status') == 'rejected'
        return kind == 'result' and data.get('is_error') is True and (err == 429 or code == 429 or data.get('api_error_status') == 429)
    if tool == 'codex':
        return kind in ('turn.failed', 'error') and (code in (429, 'rate_limit_exceeded', 'usage_limit_reached') or data.get('status') in (429, 'rate_limit_exceeded', 'usage_limit_reached') or str(info.get('message', data.get('message', ''))).startswith("You've hit your usage limit"))
    if tool == 'agy':
        result = data.get('result') if isinstance(data.get('result'), dict) else data
        error = result.get('error')
        error_code = error.get('code') if isinstance(error, dict) else result.get('code')
        return (kind == 'result' and result.get('status') == 'RATE_LIMITED') or error_code in (429, 'RESOURCE_EXHAUSTED')
    return kind == 'error' and (type(data.get('status')) is int and data['status'] == 429 or type(data.get('code')) is int and data['code'] == 429 or type(code) is int and code == 429)


def parse_json(line, tool):
    try:
        data = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return StreamEvent('working')
    if data.get('type') == 'control_request':
        request = data.get('request')
        request = request if isinstance(request, dict) else {}
        details = {'request_id': data.get('request_id'), 'tool_name': request.get('tool_name'),
                   'input': request.get('input'), 'tool_use_id': request.get('tool_use_id')}
        kind = 'permission' if request.get('subtype') == 'can_use_tool' else 'control_unknown'
        return StreamEvent(kind, request=details)
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
    if session is not None:
        return _session(session)
    if kind == 'rate_limit_event':
        # Claude Code / Antigravity print this on every run; only 'rejected' means blocked.
        info = data.get('rate_limit_info') or {}
        return StreamEvent('working', rate_limited=info.get('status') == 'rejected')
    failed = kind in ('error', 'turn.failed') or (kind == 'result' and data.get('is_error') is True)
    if failed:
        return StreamEvent('error', text=json.dumps(data)[:2000])
    if (tool in ('claude', 'agy') and kind == 'result') or (tool == 'codex' and kind == 'turn.completed'):
        return StreamEvent('turn_end')
    mapped = {'result': 'done', 'turn.completed': 'done',
              'started': 'started', 'waiting': 'waiting'}.get(kind, 'working')
    return StreamEvent(mapped)


def _agy(data):
    """Antigravity 1.4.9 stream: {"event": "init"|"step_update"|"result", ...} (seen on real runs)."""
    event = data.get('event')
    if event == 'init' and 'conversation_id' in data:
        return _session(data['conversation_id'])
    if event == 'result':
        result = data.get('result') or {}
        if result.get('status') == 'SUCCESS':
            return StreamEvent('turn_end')
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
    try:
        data = json.loads(line)
    except (ValueError, TypeError):
        data = None
    if isinstance(data, dict) and _limited(data, tool):
        event = replace(event, rate_limited=True)
    return event
