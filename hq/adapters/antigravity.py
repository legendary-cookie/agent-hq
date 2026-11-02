"""Antigravity adapter (plan Task 7).

Reads `history.jsonl` (one line per user prompt) and the per-conversation
SQLite databases under `conversations/<id>.db`. Activity is derived from step-count growth; generation metadata supplies usage.
"""
import json
import os
import time
import sqlite3
from pathlib import Path

from .base import ro_connect, preview
from ..config import is_forbidden
from ..records import Batch, EventRec, SessionRec
from ..protobuf_lite import read_fields, first_int, first_bytes
from ..tail import read_new_lines



class AntigravityAdapter:
    name = 'antigravity'
    tested = 'real'  # tokens hand-counted on 40 real conversations (independent decoder), 0 mismatches

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
            sessions.append(SessionRec(f'antigravity:{cid}', self.name, 'google', meta.get('model'),
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
                if is_forbidden(db_path):
                    continue
                conn = ro_connect(db_path)
                try:
                    count = conn.execute('SELECT COUNT(*) FROM steps').fetchone()[0]
                    has_metadata = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='gen_metadata'").fetchone()
                    blobs = conn.execute('SELECT data FROM gen_metadata').fetchall() if has_metadata else []
                finally:
                    conn.close()
            except sqlite3.Error:
                warnings += 1
                continue
            metas.setdefault(cid, {'start': mtime, 'last': mtime, 'ws': None})
            db_mtimes[cid] = mtime
            seen = set()
            newest = None
            for (blob,) in blobs:
                try:
                    top = read_fields(blob)
                    chat = read_fields(first_bytes(top, 1) or b'')
                    usage = read_fields(first_bytes(chat, 4) or b'')
                    response = first_bytes(usage, 11)
                    if not response:
                        continue
                    response_id = response.decode('utf-8', errors='replace').strip()
                    if not response_id or response_id in seen:
                        continue
                    wrapper = read_fields(first_bytes(chat, 9) or b'')
                    timestamp = read_fields(first_bytes(wrapper, 4) or b'')
                    seconds = first_int(timestamp, 1)
                    ts = seconds + (first_int(timestamp, 2) or 0) / 1e9 if seconds is not None else mtime
                    model_bytes = first_bytes(chat, 19)
                    model = model_bytes.decode('utf-8', errors='replace') if model_bytes else None
                    output = first_int(usage, 3)
                    if output is None:
                        output = (first_int(usage, 9) or 0) + (first_int(usage, 10) or 0)
                    seen.add(response_id)
                    events.append(EventRec(f'antigravity:{cid}', f'resp:{response_id}', ts, 'assistant', None, None, first_int(usage, 2) or 0, output, first_int(usage, 5) or 0, None, False, model))
                    if model and (newest is None or ts > newest[0]):
                        newest = (ts, model)
                except (ValueError, TypeError):
                    warnings += 1
            if newest:
                metas[cid]['model'] = newest[1]
            if old is None:
                cursors.append((self.name, key, count, mtime))
            elif count > old[0]:
                events.append(EventRec(f'antigravity:{cid}', f'steps#{count}', mtime,
                                       'assistant', None, None, None, None, None, None, False, None))
                cursors.append((self.name, key, count, mtime))
            else:
                cursors.append((self.name, key, count, mtime))
        return warnings
