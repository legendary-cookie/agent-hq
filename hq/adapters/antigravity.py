"""Antigravity adapter (plan Task 7).

Reads `history.jsonl` (one line per user prompt) and the per-conversation
SQLite databases under `conversations/<id>.db`. The `steps` protobuf blobs
are never parsed: tokens and model stay unknown, and activity is derived
from step-count growth.
"""
import json
import os
import time
import sqlite3
from pathlib import Path

from .base import ro_connect, preview
from ..config import is_forbidden
from ..records import Batch, EventRec, SessionRec
from ..tail import read_new_lines


def _steps_count(db_path: Path) -> int:
    conn = ro_connect(db_path)
    try:
        return conn.execute('SELECT COUNT(*) FROM steps').fetchone()[0]
    finally:
        conn.close()


class AntigravityAdapter:
    name = 'antigravity'

    def poll(self, store, cfg):
        sessions, events, db_mtimes, refs = [], [], {}, {}
        metas = {}
        cursors = []
        warnings = 0
        for root in cfg.paths:
            path = Path(root)
            if is_forbidden(path): continue
            if path.is_file():
                warnings += self._history(path, store, metas, events, refs, cursors)
            elif path.is_dir():
                warnings += self._conversations(path, store, metas, db_mtimes, refs, events, cursors)
        for cid, meta in sorted(metas.items()):
            last = max(meta['last'], db_mtimes.get(cid, 0.0))
            sessions.append(SessionRec(f'antigravity:{cid}', self.name, 'google', None,
                                       meta['ws'], None, meta['start'], last, None, None, refs[cid]))
        return Batch(sessions, events, [], [], warnings, cursors)

    def _history(self, path, store, metas, events, refs, cursors):
        warnings = 0
        key = str(path)
        old = store.get_cursor(self.name, key)
        lines, cursor = read_new_lines(path, old)
        if old and old[0] and cursor[0] >= old[0]:
            warnings += self._scan_prefix(path, old[0], metas, refs)
        offset = old[0] if old else 0
        for line in lines:
            start = offset
            offset += len(line.encode('utf-8')) + 1
            try:
                row = json.loads(line)
                cid = row['conversationId']
                ts = row['timestamp'] / 1000.0
                meta = metas.setdefault(cid, {'start': ts, 'last': ts, 'ws': row.get('workspace')})
                meta['start'] = min(meta['start'], ts)
                meta['last'] = max(meta['last'], ts)
                meta['ws'] = meta['ws'] or row.get('workspace')
                refs[cid] = key
                events.append(EventRec(f'antigravity:{cid}', f'hist#{start}', ts, 'user', None,
                                       preview(row.get('display')), None, None, None, None, False, None))
            except (ValueError, TypeError, KeyError, AttributeError):
                warnings += 1
        cursors.append((self.name, key, *cursor))
        return warnings

    def _scan_prefix(self, path, upto, metas, refs):
        warnings = 0
        for line in path.read_bytes()[:upto].decode('utf-8', errors='replace').splitlines():
            try:
                row = json.loads(line)
                cid = row['conversationId']
                ts = row['timestamp'] / 1000.0
                meta = metas.setdefault(cid, {'start': ts, 'last': ts, 'ws': row.get('workspace')})
                meta['start'] = min(meta['start'], ts)
                meta['last'] = max(meta['last'], ts)
                meta['ws'] = meta['ws'] or row.get('workspace')
                refs.setdefault(cid, str(path))
            except (ValueError, TypeError, KeyError, AttributeError):
                warnings += 1
        return warnings

    def _conversations(self, directory, store, metas, db_mtimes, refs, events, cursors):
        warnings = 0
        for db_path in sorted(directory.glob('*.db')):
            if is_forbidden(db_path): continue
            key = str(db_path)
            cid = db_path.stem
            refs.setdefault(cid, key)
            mtime = os.stat(db_path).st_mtime
            old = store.get_cursor(self.name, key)
            if old is not None and old[1] == mtime and time.time() - mtime > 2:
                continue  # unchanged and settled: do not open it (2 s guards coarse mtimes)
            try:
                count = _steps_count(db_path)
            except sqlite3.Error:
                warnings += 1
                continue
            metas.setdefault(cid, {'start': mtime, 'last': mtime, 'ws': None})
            db_mtimes[cid] = mtime
            if old is None:
                cursors.append((self.name, key, count, mtime))
            elif count > old[0]:
                events.append(EventRec(f'antigravity:{cid}', f'steps#{count}', mtime,
                                       'assistant', None, None, None, None, None, None, False, None))
                cursors.append((self.name, key, count, mtime))
        return warnings
