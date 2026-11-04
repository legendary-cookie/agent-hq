"""The agent's own words from a run's stream output, per tool (real stream shapes)."""
import json


def _claude(data):
    if data.get('type') == 'assistant' and data.get('parent_tool_use_id') is None:  # subagents never supply the plan
        content = (data.get('message') or {}).get('content') or []
        return [block.get('text') for block in content if isinstance(block, dict) and block.get('type') == 'text']
    if data.get('type') == 'result' and isinstance(data.get('result'), str):
        return [data['result']]
    return []


def _codex(data):
    item = data.get('item') if isinstance(data.get('item'), dict) else {}
    if data.get('type') == 'item.completed' and item.get('type') == 'agent_message':
        return [item.get('text')]
    return []


def _opencode(data):
    part = data.get('part') if isinstance(data.get('part'), dict) else {}
    return [part.get('text')] if data.get('type') == 'text' else []


def _agy(data):
    result = data.get('result') if isinstance(data.get('result'), dict) else {}
    return [result.get('response')] if data.get('event') == 'result' else []


_EXTRACT = {'claude': _claude, 'codex': _codex, 'opencode': _opencode, 'agy': _agy}


def text_of(tool, lines):
    """Join the assistant text of a run. System, hook, stderr and Agent HQ lines are never included,
    so tool output or hooks can't plant text that looks like the agent's answer."""
    extract = _EXTRACT.get(tool)
    if extract is None:
        return ''
    parts = []
    for line in lines:
        if not isinstance(line, str) or not line.startswith('{'):
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            parts.extend(p for p in extract(data) if isinstance(p, str))
    return '\n'.join(parts)
