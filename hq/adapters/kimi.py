"""Read Kimi Code and Kimi CLI wire logs without modifying them."""
import json
from datetime import datetime

from .base import iter_files
from ..config import is_forbidden
from ..records import Batch, EventRec, SessionRec
from ..tail import read_new_lines


def _timestamp(value, default=0.0):
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return value / 1000 if abs(value) >= 100_000_000_000 else float(value)
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


class KimiAdapter:
    name = 'kimi'
    tested = 'upstream'

    def poll(self, store, cfg):
        sessions, events, cursors = [], [], []
        warnings = 0
        for path in iter_files(cfg, '**/wire.jsonl'):
            if is_forbidden(path):
                continue
            key = str(path)
            old = store.get_cursor(self.name, key)
            lines, cursor = read_new_lines(path, old)
            start = old[0] if old and cursor[0] >= old[0] else 0
            parts = path.parts
            code = len(parts) >= 4 and parts[-3] == 'agents'
            session_name = parts[-4] if code else parts[-2]
            agent = parts[-2] if code else 'main'
            parent = f'kimi:{session_name}'
            sid = parent if agent == 'main' else f'{parent}:{agent}'
            offset = start
            times, model = [], None
            statuses = {}
            for line in lines:
                begin = offset
                offset += len(line.encode('utf-8')) + 1
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError('not an object')
                    kind = row.get('type')
                    value = row.get('time', row.get('timestamp', row.get('created_at')))
                    if kind == 'usage.record' and row.get('usageScope') == 'turn':
                        ts = _timestamp(value)
                        usage = row['usage']; model = row.get('model') or model
                        events.append(EventRec(sid, str(begin), ts, 'assistant', None, None,
                                               usage.get('inputOther'), usage.get('output'),
                                               usage.get('inputCacheRead'), usage.get('inputCacheCreation'), False, row.get('model')))
                        times.append(ts)
                    elif (row.get('message') or {}).get('type') == 'StatusUpdate':
                        payload = row['message']['payload']; usage = payload['token_usage']
                        ts = _timestamp(value)
                        mid = str(payload['message_id'])
                        status_key = f'{key}:message:{mid}'
                        previous = statuses.get(mid)
                        saved = store.get_cursor(self.name, status_key)
                        if (not saved or ts * 1_000_000 >= saved[0]) and (previous is None or ts >= previous.ts):
                            statuses[mid] = EventRec(sid, mid, ts, 'assistant', None, None,
                                                     usage.get('input_other'), usage.get('output'),
                                                     usage.get('input_cache_read'), usage.get('input_cache_creation'), False, None)
                        times.append(ts)
                    elif kind != 'usage.record' and value is not None:
                        times.append(_timestamp(value))
                except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                    warnings += 1
            for mid, event in statuses.items():
                events.append(event)
                cursors.append((self.name, f'{key}:message:{mid}', int(event.ts * 1_000_000), 0.0))
            if not old or lines:
                ts = min(times) if times else 0.0
                sessions.append(SessionRec(sid, self.name, 'moonshot', model, None, None, ts,
                                           max(times) if times else ts, None, parent if agent != 'main' else None, key))
            cursors.append((self.name, key, *cursor))
        return Batch(sessions, events, [], [], warnings, cursors)
