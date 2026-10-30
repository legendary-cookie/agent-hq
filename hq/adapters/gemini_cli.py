"""Gemini CLI adapter (plan Task 8).

Line 1 of each chat file is a header; later lines are `$set` snapshots that
repeat the full message list. Message ids are deduped: within a pass by the
adapter (first occurrence wins), across passes by the store's
UNIQUE(session_id, ext_id).
"""
import json
from pathlib import Path

from .base import iter_files, preview
from ..records import Batch, EventRec, SessionRec
from ..tail import read_new_lines

_KINDS = {'user': 'user', 'gemini': 'assistant'}


class GeminiCliAdapter:
    name = 'gemini_cli'

    def poll(self, store, cfg):
        sessions, events, warnings = [], [], 0
        for path in iter_files(cfg, '**/*.jsonl'):
            key = str(path)
            old = store.get_cursor(self.name, key)
            new_lines, cursor = read_new_lines(path, old)
            start = old[0] if old and cursor[0] >= old[0] else 0
            file_session, file_events, file_warnings = self._scan(path, start, new_lines)
            sessions.append(file_session)
            events.extend(file_events)
            warnings += file_warnings
            store.set_cursor(self.name, key, *cursor)
        return Batch([s for s in sessions if s is not None], events, [], [], warnings)

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
                elif header is None:
                    header = row
                else:
                    warnings += 1
            except (ValueError, TypeError, KeyError, AttributeError):
                warnings += 1
        if not isinstance(header, dict):
            return None, [], warnings
        sid = f'gemini_cli:{path.stem}'
        started_at = (header.get('startTime') or 0) / 1000.0
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
            ts = (message.get('timestamp') or started_at * 1000) / 1000.0
            tokens = message.get('tokens') or {}
            if message.get('model'):
                model = message['model']
            session = SessionRec(sid, self.name, 'google', model, None, None,
                                 started_at, max(started_at, ts), None, None, str(path))
            file_events.append(EventRec(sid, mid, ts, kind, None, preview(message.get('content')),
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
