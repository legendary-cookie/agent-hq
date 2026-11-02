import json
from datetime import datetime

from .base import iter_files, preview
from ..records import Batch, EventRec, LimitRec, SessionRec
from ..tail import read_new_lines

try:  # Python 3.14+ ships zstd in the standard library.
    from compression import zstd
except ImportError:
    zstd = None


def _time(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def _content(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ' '.join(str(x.get('text', '')) for x in value if isinstance(x, dict))
    return None


def _read_zst(path, old):
    """Codex compresses finished sessions to .jsonl.zst. They do not grow, so the
    cursor is (compressed size, mtime): an unchanged file is skipped, a changed one
    is read whole. Line offsets are in the decompressed stream, which matches the
    plain .jsonl offsets, so events from a session seen in both forms dedupe."""
    stat = path.stat()
    cursor = (stat.st_size, stat.st_mtime)
    if old and tuple(old) == cursor:
        return [], cursor
    text = zstd.decompress(path.read_bytes()).decode('utf-8', errors='replace')
    lines = text.split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    return lines, cursor


def _session_from_meta(row, key, last_event_at=None):
    """Session record from a file's FIRST session_meta. Subagent files start with
    their own session_meta (source.subagent.thread_spawn names the parent) and then
    copy the parent's session_meta as context; that copy must not switch sessions."""
    payload = row['payload']
    started = _time(payload.get('timestamp') or row['timestamp'])
    source = payload.get('source')
    spawn = source.get('subagent', {}).get('thread_spawn', {}) if isinstance(source, dict) else {}
    parent = spawn.get('parent_thread_id')
    return dict(
        id='codex:' + payload['id'],
        harness='codex',
        provider='openai',
        model=None,
        project=payload.get('cwd'),
        title=spawn.get('agent_nickname') or None,
        started_at=started,
        last_event_at=started if last_event_at is None else last_event_at,
        ended_at=None,
        parent_id='codex:' + parent if parent else None,
        source_ref=key,
    )


class CodexAdapter:
    name = 'codex'

    def poll(self, store, cfg):
        sessions, events, limits, warnings = [], [], [], 0
        cursors = []
        paths = list(iter_files(cfg, '**/*.jsonl')) + list(iter_files(cfg, '**/*.jsonl.zst'))
        for path in paths:
            key = str(path)
            old = store.get_cursor(self.name, key)
            compressed = path.name.endswith('.zst')
            if compressed and zstd is None:
                warnings += 1  # needs Python 3.14+; counted so health shows it
                continue
            lines, cursor = _read_zst(path, old) if compressed else read_new_lines(path, old)
            if not lines:
                cursors.append((self.name, key, *cursor))
                continue
            # Metadata may have been read in a previous pass; recover it from the
            # complete prefix without emitting its events again. Compressed files
            # are always read whole, so they have no prefix.
            start = old[0] if not compressed and old and cursor[0] >= old[0] else 0
            if start:
                with path.open('rb') as f:
                    prefix = f.read(start).decode('utf-8', errors='replace').splitlines()
            else:
                prefix = []
            state = None
            model = None
            for line in prefix:
                try:
                    row = json.loads(line)
                    payload = row.get('payload', {})
                    if row.get('type') == 'session_meta':
                        if state is None:
                            state = _session_from_meta(row, key)
                    elif row.get('type') == 'turn_context' and state:
                        model = payload.get('model') or model
                        state['project'] = payload.get('cwd') or state['project']
                except (ValueError, TypeError, KeyError, AttributeError):
                    continue
            offset = start
            for line in lines:
                line_offset = offset
                offset += len(line.encode('utf-8')) + 1
                try:
                    row = json.loads(line)
                    payload = row['payload']
                    typ = row['type']
                    ts = _time(row['timestamp'])
                    if typ == 'session_meta':
                        if state is None:
                            state = _session_from_meta(row, key, last_event_at=ts)
                        continue
                    if state is None:
                        continue
                    state['last_event_at'] = max(state['last_event_at'], ts)
                    if typ == 'turn_context':
                        model = payload.get('model') or model
                        state['project'] = payload.get('cwd') or state['project']
                    sid = state['id']
                    if typ == 'event_msg' and payload.get('type') == 'token_count':
                        usage = (payload.get('info') or {}).get('last_token_usage')
                        if usage is not None:
                            cached = usage.get('cached_input_tokens')
                            total = usage.get('input_tokens')
                            events.append(EventRec(sid, f'tc#{line_offset}', ts, 'assistant', None, None, total - (cached or 0) if total is not None else None, usage.get('output_tokens'), cached, usage.get('cache_write_input_tokens'), False, model))
                        for limit in (payload.get('rate_limits') or {}).values():
                            if not isinstance(limit, dict) or limit.get('window_minutes') is None or limit.get('used_percent') is None:
                                continue
                            minutes = limit['window_minutes']
                            window = '5h' if minutes == 300 else 'week' if minutes == 10080 else 'day' if minutes <= 1440 else 'month'
                            limits.append(LimitRec(self.name, 'openai', '*', window, limit['used_percent'], limit.get('resets_at'), 'measured', ts))
                    elif typ == 'event_msg' and payload.get('type') == 'task_complete':
                        events.append(EventRec(sid, f'end#{line_offset}', ts, 'turn_end', None, None, None, None, None, None, False, model))
                    elif typ == 'response_item':
                        subtype = payload.get('type')
                        kind = 'tool_call' if subtype in ('function_call', 'custom_tool_call') else 'tool_result' if subtype == 'custom_tool_call_output' else payload.get('role') if subtype == 'message' and payload.get('role') in ('user', 'assistant') else None
                        if kind:
                            events.append(EventRec(sid, f'item#{line_offset}', ts, kind, payload.get('name') if kind == 'tool_call' else None, preview(_content(payload.get('content') or payload.get('output'))), None, None, None, None, False, model))
                except (ValueError, TypeError, KeyError, AttributeError):
                    warnings += 1
            if state:
                state['model'] = model
                sessions.append(SessionRec(**state))
            cursors.append((self.name, key, *cursor))
        return Batch(sessions, events, limits, [], warnings, cursors)
