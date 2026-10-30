import json
from datetime import datetime

from .base import iter_files, preview
from ..records import Batch, EventRec, SessionRec
from ..tail import read_new_lines


def _time(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ' '.join(str(b.get('text', b.get('content', ''))) for b in content if isinstance(b, dict) and b.get('type') in ('text', 'tool_result'))
    return None


class ClaudeCodeAdapter:
    name = 'claude_code'

    def poll(self, store, cfg):
        sessions, events, warnings = [], [], 0
        for path in iter_files(cfg, '**/*.jsonl'):
            key = str(path)
            old = store.get_cursor(self.name, key)
            lines, cursor = read_new_lines(path, old)
            state = None
            title = None
            for line in lines:
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError('not an object')
                    typ = row.get('type')
                    if typ == 'custom-title':
                        title = row.get('customTitle')
                        continue
                    if typ not in ('user', 'assistant'):
                        continue
                    sid = row['sessionId']
                    parent = f'claude_code:{sid}'
                    is_sub = 'subagents' in path.parts
                    session_id = f'{parent}:{path.stem}' if is_sub else parent
                    ts = _time(row['timestamp'])
                    msg = row['message']
                    uid = row['uuid']
                    content = msg.get('content', [])
                    model = msg.get('model') if typ == 'assistant' else None
                    if state is None:
                        state = dict(id=session_id, harness=self.name, provider='anthropic', model=model, project=row.get('cwd'), title=None, started_at=ts, last_event_at=ts, ended_at=None, parent_id=parent if is_sub else None, source_ref=key)
                    else:
                        state['started_at'] = min(state['started_at'], ts)
                        state['last_event_at'] = max(state['last_event_at'], ts)
                        state['model'] = model or state['model']
                        state['project'] = row.get('cwd') or state['project']
                    usage = msg.get('usage') or {}
                    blocks = content if isinstance(content, list) else []
                    # Tool results are events in their own right, not new prompts.
                    # Claude Code writes one line per content block and repeats the
                    # API message's usage on each, so key assistant events by the
                    # message id: the store keeps one event per message.
                    msg_id = msg.get('id') if typ == 'assistant' else None
                    if typ == 'assistant' or not any(isinstance(b, dict) and b.get('type') == 'tool_result' for b in blocks):
                        events.append(EventRec(session_id, f'msg#{msg_id}' if msg_id else uid, ts, typ, None, preview(_text(content)), usage.get('input_tokens'), usage.get('output_tokens'), usage.get('cache_read_input_tokens'), usage.get('cache_creation_input_tokens'), False, model))
                    for i, block in enumerate(blocks):
                        if not isinstance(block, dict):
                            continue
                        kind = block.get('type')
                        if kind == 'tool_use':
                            events.append(EventRec(session_id, f'{uid}#{i}', ts, 'tool_call', block.get('name'), None, None, None, None, None, False, model))
                        elif kind == 'tool_result':
                            events.append(EventRec(session_id, f'{uid}#{i}', ts, 'error' if block.get('is_error') else 'tool_result', None, preview(_text([block])), None, None, None, None, False, None))
                    if typ == 'assistant' and msg.get('stop_reason') == 'end_turn':
                        events.append(EventRec(session_id, f'{uid}#end', ts, 'turn_end', None, None, None, None, None, None, False, model))
                except (ValueError, TypeError, KeyError, AttributeError):
                    warnings += 1
            if state is None and title is not None and old and cursor[0] >= old[0]:
                with path.open('rb') as stream:
                    prefix = stream.read(old[0]).decode('utf-8', errors='replace').splitlines()
                for previous in prefix:
                    try:
                        row = json.loads(previous)
                        if row.get('type') not in ('user', 'assistant'):
                            continue
                        sid = row['sessionId']
                        parent = f'claude_code:{sid}'
                        is_sub = 'subagents' in path.parts
                        ts = _time(row['timestamp'])
                        if state is None:
                            state = dict(id=f'{parent}:{path.stem}' if is_sub else parent, harness=self.name, provider='anthropic', model=None, project=None, title=None, started_at=ts, last_event_at=ts, ended_at=None, parent_id=parent if is_sub else None, source_ref=key)
                        state['started_at'] = min(state['started_at'], ts)
                        state['last_event_at'] = max(state['last_event_at'], ts)
                        state['project'] = row.get('cwd') or state['project']
                        state['model'] = row.get('message', {}).get('model') or state['model']
                    except (ValueError, TypeError, KeyError, AttributeError):
                        continue
            if state:
                state['title'] = title
                sessions.append(SessionRec(**state))
            store.set_cursor(self.name, key, *cursor)
        return Batch(sessions, events, [], [], warnings)
