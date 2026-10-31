"""opencode adapter (plan Task 6).

Reads opencode's SQLite database read-only. Only the `session`, `message`
and `part` tables are queried, with fixed SQL. Each table has its own cursor
on `time_updated` (milliseconds), because opencode fills in tokens and
`finish` on a message after creating it.

Database errors (locked, missing table) propagate to the collector, which
marks this adapter partial; a row with bad JSON is skipped and counted.
"""
import json
from contextlib import closing
from pathlib import Path

from .base import preview, ro_connect
from ..config import is_forbidden
from ..records import Batch, EventRec, SessionRec

_SESSION_SQL = ('SELECT id, parent_id, directory, title, model, time_created, time_updated, time_archived '
                'FROM session WHERE time_updated > ? OR id IN (SELECT value FROM json_each(?)) ORDER BY time_updated')
_MESSAGE_SQL = 'SELECT id, session_id, time_created, time_updated, data FROM message WHERE time_updated > ? ORDER BY time_updated'
_LATEST_SQL = ("SELECT session_id, data FROM message WHERE session_id = ? AND json_extract(data, '$.role') = 'assistant' "
               'ORDER BY time_created DESC LIMIT 1')
_PART_SQL = 'SELECT id, session_id, time_created, time_updated, data FROM part WHERE time_updated > ? ORDER BY time_updated'

# A step that ended only to call tools is not the end of the agent's turn.
_NOT_TURN_END = {'tool-calls', 'tool_calls', 'tool_use'}


def _ev(sid, ext_id, ts, kind, tool=None, text=None, tokens=(None, None, None, None), model=None):
    return EventRec(sid, ext_id, ts, kind, tool, preview(text), *tokens, False, model)


class OpencodeAdapter:
    name = 'opencode'

    def poll(self, store, cfg):
        sessions, events, warnings = [], [], 0
        cursors = []
        for root in cfg.paths:
            path = Path(root)
            if is_forbidden(path) or not path.is_file():
                continue
            with closing(ro_connect(path)) as conn:
                s, e, w = self._read(conn, store, str(path), cursors)
            sessions += s
            events += e
            warnings += w
        return Batch(sessions, events, [], [], warnings, cursors)

    def _cursor(self, store, key):
        cur = store.get_cursor(self.name, key)
        return cur[0] if cur else 0

    def _read(self, conn, store, ref, cursors):
        warnings = 0
        events = []
        keys = {t: f'{ref}:{t}' for t in ('session', 'message', 'part')}
        msg_cur, part_cur, sess_cur = (self._cursor(store, keys[t]) for t in ('message', 'part', 'session'))
        touched = set()

        new_msg_cur = msg_cur
        for mid, sid, created, updated, raw in conn.execute(_MESSAGE_SQL, (msg_cur,)).fetchall():
            new_msg_cur = max(new_msg_cur, updated)
            try:
                data = json.loads(raw)
            except (TypeError, ValueError):
                warnings += 1
                continue
            hq_sid = f'opencode:{sid}'
            touched.add(sid)
            ts = created / 1000
            role = data.get('role')
            if role == 'user':
                events.append(_ev(hq_sid, mid, ts, 'user'))
            elif role == 'assistant' and data.get('finish'):
                tok = data.get('tokens') or {}
                cache = tok.get('cache') or {}
                out = None if tok.get('output') is None else tok.get('output', 0) + (tok.get('reasoning') or 0)
                events.append(_ev(hq_sid, mid, ts, 'assistant', model=data.get('modelID'),
                                  tokens=(tok.get('input'), out, cache.get('read'), cache.get('write'))))
                if data['finish'] not in _NOT_TURN_END:
                    events.append(_ev(hq_sid, f'{mid}:turn_end', updated / 1000, 'turn_end'))

        new_part_cur = part_cur
        for pid, sid, created, updated, raw in conn.execute(_PART_SQL, (part_cur,)).fetchall():
            new_part_cur = max(new_part_cur, updated)
            try:
                data = json.loads(raw)
            except (TypeError, ValueError):
                warnings += 1
                continue
            touched.add(sid)
            hq_sid = f'opencode:{sid}'
            if data.get('type') == 'tool':
                events.append(_ev(hq_sid, pid, created / 1000, 'tool_call', tool=data.get('tool')))
                if (data.get('state') or {}).get('status') == 'error':
                    events.append(_ev(hq_sid, f'{pid}:error', updated / 1000, 'error', tool=data.get('tool')))

        sessions = []
        new_sess_cur = sess_cur
        for row in conn.execute(_SESSION_SQL, (sess_cur, json.dumps(sorted(touched)))).fetchall():
            sid, parent, directory, title, model, created, updated, archived = row
            new_sess_cur = max(new_sess_cur, updated)
            provider, latest_model = 'opencode', model
            latest = conn.execute(_LATEST_SQL, (sid,)).fetchone()
            if latest:
                try:
                    d = json.loads(latest[1])
                    provider = d.get('providerID') or provider
                    latest_model = d.get('modelID') or latest_model
                except (TypeError, ValueError):
                    warnings += 1
            sessions.append(SessionRec(f'opencode:{sid}', self.name, provider, latest_model, directory, title,
                                       created / 1000, updated / 1000, archived / 1000 if archived else None,
                                       f'opencode:{parent}' if parent else None, ref))

        cursors.append((self.name, keys['message'], new_msg_cur, 0.0))
        cursors.append((self.name, keys['part'], new_part_cur, 0.0))
        cursors.append((self.name, keys['session'], new_sess_cur, 0.0))
        return sessions, events, warnings
