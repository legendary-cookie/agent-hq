import sqlite3
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Iterable
from .records import Batch

_SCHEMA = '''
CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, provider TEXT, label TEXT, plan TEXT, monthly_price_usd REAL, billing_day INTEGER, "match" TEXT, cap_tokens TEXT);
CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, harness TEXT, provider TEXT, model TEXT, account_id TEXT, project TEXT, title TEXT, started_at REAL, last_event_at REAL, ended_at REAL, status TEXT, parent_id TEXT, source_ref TEXT);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, session_id TEXT, ext_id TEXT, ts REAL, kind TEXT, tool TEXT, preview TEXT, tokens_in INTEGER, tokens_out INTEGER, tokens_cache_read INTEGER, tokens_cache_write INTEGER, tokens_estimated INTEGER, model TEXT, UNIQUE(session_id, ext_id));
CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, session_id TEXT, started_at REAL, ended_at REAL, outcome TEXT);
CREATE TABLE IF NOT EXISTS delegations (id INTEGER PRIMARY KEY, ext_id TEXT UNIQUE, ts REAL, provider TEXT, model TEXT, task_kind TEXT, outcome TEXT, tokens INTEGER, duration_s REAL, note TEXT);
CREATE TABLE IF NOT EXISTS limit_snapshots (id INTEGER PRIMARY KEY, account_id TEXT, harness TEXT, provider TEXT, scope TEXT, window TEXT, used_percent REAL, resets_at REAL, source TEXT, recorded_at REAL, UNIQUE(account_id, harness, provider, scope, window, used_percent, resets_at, source, recorded_at));
CREATE TABLE IF NOT EXISTS cursors (adapter TEXT, key TEXT, offset INTEGER, mtime REAL, PRIMARY KEY(adapter, key));
CREATE TABLE IF NOT EXISTS prices (model TEXT PRIMARY KEY, input REAL, output REAL, cache_read REAL, cache_write REAL);
'''

class Store:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._writer = sqlite3.connect(path, check_same_thread=False)
        self._writer.execute('PRAGMA journal_mode=WAL')
        self._writer.executescript(_SCHEMA)

    def conn(self):
        return sqlite3.connect(self.path)

    def apply(self, batch: Batch, account_of: Callable[[str,str,str | None],str]) -> bool:
        c = self._writer
        with self._lock, c:
            before = c.total_changes
            for session in batch.sessions:
                d = asdict(session)
                d['account_id'] = account_of(session.harness, session.provider, session.model)
                c.execute('''INSERT INTO sessions (id,harness,provider,model,account_id,project,title,started_at,last_event_at,ended_at,parent_id,source_ref)
                  VALUES (:id,:harness,:provider,:model,:account_id,:project,:title,:started_at,:last_event_at,:ended_at,:parent_id,:source_ref)
                  ON CONFLICT(id) DO UPDATE SET harness=excluded.harness, provider=excluded.provider,
                  model=coalesce(excluded.model,sessions.model), account_id=excluded.account_id,
                  project=coalesce(excluded.project,sessions.project), title=coalesce(excluded.title,sessions.title),
                  started_at=min(sessions.started_at,excluded.started_at), last_event_at=max(sessions.last_event_at,excluded.last_event_at),
                  ended_at=coalesce(excluded.ended_at,sessions.ended_at), parent_id=coalesce(excluded.parent_id,sessions.parent_id), source_ref=excluded.source_ref
                  WHERE sessions.harness IS NOT excluded.harness OR sessions.provider IS NOT excluded.provider OR
                  (excluded.model IS NOT NULL AND sessions.model IS NOT excluded.model) OR sessions.account_id IS NOT excluded.account_id OR
                  (excluded.project IS NOT NULL AND sessions.project IS NOT excluded.project) OR (excluded.title IS NOT NULL AND sessions.title IS NOT excluded.title) OR
                  excluded.started_at < sessions.started_at OR excluded.last_event_at > sessions.last_event_at OR
                  (excluded.ended_at IS NOT NULL AND sessions.ended_at IS NOT excluded.ended_at) OR
                  (excluded.parent_id IS NOT NULL AND sessions.parent_id IS NOT excluded.parent_id) OR sessions.source_ref IS NOT excluded.source_ref''', d)
            for event in batch.events:
                d = asdict(event)
                if d['preview'] is not None: d['preview'] = d['preview'][:200]
                c.execute('''INSERT INTO events (session_id,ext_id,ts,kind,tool,preview,tokens_in,tokens_out,tokens_cache_read,tokens_cache_write,tokens_estimated,model)
                VALUES (:session_id,:ext_id,:ts,:kind,:tool,:preview,:tokens_in,:tokens_out,:tokens_cache_read,:tokens_cache_write,:tokens_estimated,:model) ON CONFLICT(session_id,ext_id) DO NOTHING''',d)
            for limit in batch.limits:
                d = asdict(limit)
                d['account_id'] = account_of(limit.harness, limit.provider, limit.scope)
                c.execute('''INSERT INTO limit_snapshots (account_id,harness,provider,scope,window,used_percent,resets_at,source,recorded_at)
                SELECT :account_id,:harness,:provider,:scope,:window,:used_percent,:resets_at,:source,:recorded_at
                WHERE NOT EXISTS (SELECT 1 FROM limit_snapshots WHERE account_id IS :account_id AND harness IS :harness AND provider IS :provider AND scope IS :scope AND window IS :window AND used_percent IS :used_percent AND resets_at IS :resets_at AND source IS :source AND recorded_at IS :recorded_at)''',d)
            for delegation in batch.delegations:
                c.execute('''INSERT INTO delegations (ext_id,ts,provider,model,task_kind,outcome,tokens,duration_s,note)
                VALUES (:ext_id,:ts,:provider,:model,:task_kind,:outcome,:tokens,:duration_s,:note) ON CONFLICT(ext_id) DO NOTHING''',asdict(delegation))
            return c.total_changes > before

    def get_cursor(self, adapter, key) -> tuple[int,float] | None:
        with self._lock:
            return self._writer.execute('SELECT offset,mtime FROM cursors WHERE adapter=? AND key=?',(adapter,key)).fetchone()

    def set_cursor(self, adapter, key, offset, mtime):
        with self._lock, self._writer:
            self._writer.execute('INSERT INTO cursors VALUES (?,?,?,?) ON CONFLICT(adapter,key) DO UPDATE SET offset=excluded.offset,mtime=excluded.mtime',(adapter,key,offset,mtime))

    def derive_tasks(self, session_ids: Iterable[str]):
        with self._lock, self._writer as c:
            for sid in session_ids:
                c.execute('DELETE FROM tasks WHERE session_id=?',(sid,))
                rows = []
                start = None; last_error = None
                for ts, kind in c.execute('SELECT ts,kind FROM events WHERE session_id=? ORDER BY ts,id',(sid,)).fetchall():
                    if kind == 'user':
                        if start is not None:
                            rows.append((start, last_error, 'error') if last_error is not None else (start, None, 'open'))
                        start, last_error = ts, None
                    elif start is not None and kind == 'error':
                        last_error = ts
                    elif start is not None and kind == 'turn_end':
                        rows.append((start, ts, 'error' if last_error is not None else 'ok'))
                        start, last_error = None, None
                if start is not None:
                    rows.append((start, last_error, 'error') if last_error is not None else (start, None, 'open'))
                c.executemany('INSERT INTO tasks(session_id,started_at,ended_at,outcome) VALUES (?,?,?,?)',[(sid,)+r for r in rows])
