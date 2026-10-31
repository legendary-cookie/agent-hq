"""Gemini CLI adapter (plan Task 8).

Line 1 of each chat file is a header; later lines are `$set` snapshots that
repeat the full message list. Message ids are deduped: within a pass by the
adapter (first occurrence wins), across passes by the store's
UNIQUE(session_id, ext_id).
"""
import json
from datetime import datetime
from pathlib import Path

from .base import iter_files, preview
from ..records import Batch, EventRec, SessionRec
from ..tail import read_new_lines

def _ts(value, default=0.0):
    """Gemini writes ISO-8601 strings; accept epoch milliseconds too."""
    if isinstance(value, (int, float)):
        return value / 1000.0
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    return default


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ' '.join(str(part.get('text', '')) for part in content if isinstance(part, dict)) or None
    return None


_KINDS = {'user': 'user', 'gemini': 'assistant'}


class GeminiCliAdapter:
    name = 'gemini_cli'

    def poll(self, store, cfg):
        sessions, events, warnings = [], [], 0
        cursors = []
        for path in iter_files(cfg, '**/*.jsonl'):
            key = str(path)
            old = store.get_cursor(self.name, key)
            new_lines, cursor = read_new_lines(path, old)
            start = old[0] if old and cursor[0] >= old[0] else 0
            file_session, file_events, file_warnings = self._scan(path, start, new_lines)
            sessions.append(file_session)
            events.extend(file_events)
            warnings += file_warnings
            cursors.append((self.name, key, *cursor))
        return Batch([s for s in sessions if s is not None], events, [], [], warnings, cursors)

    def _scan(self, path: Path, start: int, new_lines: list[str]):
        warnings = 0
        header = None
        if start:
            header = self._first_line(path, start)
        messages = []
        offset = start
        for line in new_lines:
            begin = offset
            offset += len(line.encode('utf-8')) + 1
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError('not an object')
                if '$set' in row:
                    for message in row['$set'].get('messages') or []:
                        if isinstance(message, dict):
                            messages.append(message)
                elif 'sessionId' in row:
                    # Gemini rewrites its header as the chat updates; keep the latest.
                    header = {**(header or {}), **row}
                else:
                    warnings += 1
            except (ValueError, TypeError, KeyError, AttributeError):
                warnings += 1
        if not isinstance(header, dict):
            return None, [], warnings
        sid = f'gemini_cli:{path.stem}'
        started_at = _ts(header.get('startTime'))
        session = SessionRec(sid, self.name, 'google', None, None, None,
                             started_at, started_at, None, None, str(path))
        model = None
        seen = set()
        file_events = []
        for message in messages:
            mid = message.get('id')
            if not mid or mid in seen:
                continue
            seen.add(mid)
            kind = _KINDS.get(message.get('type'))
            if kind is None:
                warnings += 1
                continue
            ts = _ts(message.get('timestamp'), started_at)
            tokens = message.get('tokens') or {}
            if message.get('model'):
                model = message['model']
            session = SessionRec(sid, self.name, 'google', model, None, None,
                                 started_at, max(started_at, ts), None, None, str(path))
            file_events.append(EventRec(sid, mid, ts, kind, None, preview(_text(message.get('content'))),
                                        tokens.get('input'), tokens.get('output'),
                                        None, None, False, message.get('model')))
        return session, file_events, warnings

    def _first_line(self, path: Path, upto: int):
        try:
            head = path.read_bytes()[:upto].split(b'\n', 1)[0].decode('utf-8', errors='replace')
            row = json.loads(head)
            kind = row.get('kind')
            return row if isinstance(row, dict) and kind is not None else None
        except (ValueError, OSError):
            return None
