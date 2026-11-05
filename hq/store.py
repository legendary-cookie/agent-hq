import sqlite3
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Iterable
from .records import Batch
from .providers import normalize_provider

_SCHEMA = '''
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, tool TEXT NOT NULL, model TEXT, account TEXT,
  project TEXT NOT NULL, workdir TEXT NOT NULL, branch TEXT,
  level TEXT NOT NULL, guard_mode TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'dispatch', status TEXT NOT NULL,
  session_id TEXT, prompt_preview TEXT, started_at REAL, ended_at REAL,
  exit_code INTEGER, time_limit_s INTEGER NOT NULL, merge_state TEXT
);
CREATE TABLE IF NOT EXISTS goals (
  id TEXT PRIMARY KEY, project TEXT NOT NULL, text_preview TEXT, max_level TEXT NOT NULL,
  status TEXT NOT NULL, branch TEXT, base_commit TEXT, plan_json TEXT,
  step_limit INTEGER NOT NULL, time_limit_s INTEGER NOT NULL, created_at REAL NOT NULL,
  ended_at REAL
);
CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, tool_name TEXT NOT NULL,
  summary TEXT NOT NULL, requested_at REAL NOT NULL, answered_at REAL, state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, provider TEXT, label TEXT, plan TEXT, monthly_price_usd REAL, billing_day INTEGER, "match" TEXT, cap_tokens TEXT);
CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, harness TEXT, provider TEXT, model TEXT, account_id TEXT, project TEXT, title TEXT, started_at REAL, last_event_at REAL, ended_at REAL, status TEXT, parent_id TEXT, source_ref TEXT, imported INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, session_id TEXT, ext_id TEXT, ts REAL, kind TEXT, tool TEXT, preview TEXT, tokens_in INTEGER, tokens_out INTEGER, tokens_cache_read INTEGER, tokens_cache_write INTEGER, tokens_estimated INTEGER, model TEXT, UNIQUE(session_id, ext_id));
CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, session_id TEXT, started_at REAL, ended_at REAL, outcome TEXT);
CREATE TABLE IF NOT EXISTS delegations (id INTEGER PRIMARY KEY, ext_id TEXT UNIQUE, ts REAL, provider TEXT, model TEXT, task_kind TEXT, outcome TEXT, tokens INTEGER, duration_s REAL, note TEXT);
CREATE TABLE IF NOT EXISTS limit_snapshots (id INTEGER PRIMARY KEY, account_id TEXT, harness TEXT, provider TEXT, scope TEXT, window TEXT, used_percent REAL, resets_at REAL, source TEXT, recorded_at REAL, UNIQUE(account_id, harness, provider, scope, window, used_percent, resets_at, source, recorded_at));
CREATE TABLE IF NOT EXISTS balances (account_id TEXT, currency TEXT, total REAL, granted REAL, topped_up REAL, recorded_at REAL, PRIMARY KEY(account_id, currency, recorded_at));
CREATE TABLE IF NOT EXISTS cursors (adapter TEXT, key TEXT, offset INTEGER, mtime REAL, PRIMARY KEY(adapter, key));
CREATE INDEX IF NOT EXISTS idx_events_session_ts ON events(session_id, ts, id);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_sessions_account ON sessions(account_id);
CREATE TABLE IF NOT EXISTS prices (model TEXT PRIMARY KEY, input REAL, output REAL, cache_read REAL, cache_write REAL);
'''

class Store:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._writer = sqlite3.connect(path, check_same_thread=False)
        self._writer.execute('PRAGMA journal_mode=WAL')
        self._writer.executescript(_SCHEMA)
        goal_columns = {row[1] for row in self._writer.execute('PRAGMA table_info(goals)')}
        if 'stop_reason' not in goal_columns:
            self._writer.execute('ALTER TABLE goals ADD COLUMN stop_reason TEXT')
            self._writer.commit()
        run_columns = {row[1] for row in self._writer.execute('PRAGMA table_info(runs)')}
        if 'turns' not in run_columns:
            self._writer.execute('ALTER TABLE runs ADD COLUMN turns INTEGER NOT NULL DEFAULT 1')
            self._writer.commit()
        if 'base_commit' not in run_columns:
            self._writer.execute('ALTER TABLE runs ADD COLUMN base_commit TEXT')
            self._writer.commit()
        if 'git_dir' not in run_columns:
            self._writer.execute('ALTER TABLE runs ADD COLUMN git_dir TEXT')
            self._writer.commit()
        for name in ('goal_id', 'step_id', 'host'):
            if name not in run_columns:
                self._writer.execute(f'ALTER TABLE runs ADD COLUMN {name} TEXT')
        self._writer.commit()
        columns = {row[1] for row in self._writer.execute('PRAGMA table_info(sessions)')}
        if 'host' not in columns:
            self._writer.execute('ALTER TABLE sessions ADD COLUMN host TEXT')
            self._writer.commit()
        if 'imported' not in columns:
            self._writer.execute('ALTER TABLE sessions ADD COLUMN imported INTEGER DEFAULT 0')
            self._writer.commit()

    def close(self):
        with self._lock:
            self._writer.close()

    def conn(self):
        return sqlite3.connect(self.path)

    def apply(self, batch: Batch, account_of: Callable[[str,str,str | None],str], relabel: list[tuple[Path, str]] = (), host=None) -> bool:
        c = self._writer
        with self._lock, c:
            before = c.total_changes
            for session in batch.sessions:
                d = asdict(session)
                d['host'] = host
                d['provider'] = normalize_provider(session.provider, session.model)
                if session.project:
                    project = Path(session.project).expanduser().resolve()
                    for under, harness in relabel:
                        if project.is_relative_to(under.expanduser().resolve()):
                            d['harness'] = harness
                            break
                d['account_id'] = account_of(d['harness'], d['provider'], session.model)
                c.execute('''INSERT INTO sessions (id,harness,provider,model,account_id,project,title,started_at,last_event_at,ended_at,parent_id,source_ref,imported,host)
                  VALUES (:id,:harness,:provider,:model,:account_id,:project,:title,:started_at,:last_event_at,:ended_at,:parent_id,:source_ref,:imported,:host)
                  ON CONFLICT(id) DO UPDATE SET harness=excluded.harness, provider=excluded.provider,
                  model=coalesce(excluded.model,sessions.model), account_id=excluded.account_id,
                  project=coalesce(excluded.project,sessions.project), title=coalesce(excluded.title,sessions.title),
                  started_at=min(sessions.started_at,excluded.started_at), last_event_at=max(sessions.last_event_at,excluded.last_event_at),
                  ended_at=coalesce(excluded.ended_at,sessions.ended_at), parent_id=coalesce(excluded.parent_id,sessions.parent_id), source_ref=excluded.source_ref, imported=excluded.imported
                  WHERE sessions.host IS excluded.host AND (sessions.harness IS NOT excluded.harness OR sessions.provider IS NOT excluded.provider OR
                  (excluded.model IS NOT NULL AND sessions.model IS NOT excluded.model) OR sessions.account_id IS NOT excluded.account_id OR
                  (excluded.project IS NOT NULL AND sessions.project IS NOT excluded.project) OR (excluded.title IS NOT NULL AND sessions.title IS NOT excluded.title) OR
                  excluded.started_at < sessions.started_at OR excluded.last_event_at > sessions.last_event_at OR
                  (excluded.ended_at IS NOT NULL AND sessions.ended_at IS NOT excluded.ended_at) OR
                  (excluded.parent_id IS NOT NULL AND sessions.parent_id IS NOT excluded.parent_id) OR sessions.source_ref IS NOT excluded.source_ref OR sessions.imported IS NOT excluded.imported)''', d)
            for event in batch.events:
                d = asdict(event)
                owner = c.execute('SELECT host FROM sessions WHERE id=?', (event.session_id,)).fetchone()
                if owner is not None and owner[0] != host:
                    continue
                if d['preview'] is not None: d['preview'] = d['preview'][:200]
                c.execute('''INSERT INTO events (session_id,ext_id,ts,kind,tool,preview,tokens_in,tokens_out,tokens_cache_read,tokens_cache_write,tokens_estimated,model)
                VALUES (:session_id,:ext_id,:ts,:kind,:tool,:preview,:tokens_in,:tokens_out,:tokens_cache_read,:tokens_cache_write,:tokens_estimated,:model) ON CONFLICT(session_id,ext_id) DO UPDATE SET
                tokens_in=coalesce(excluded.tokens_in,events.tokens_in), tokens_out=coalesce(excluded.tokens_out,events.tokens_out),
                tokens_cache_read=coalesce(excluded.tokens_cache_read,events.tokens_cache_read), tokens_cache_write=coalesce(excluded.tokens_cache_write,events.tokens_cache_write), model=coalesce(excluded.model,events.model)
                WHERE (excluded.tokens_in IS NOT NULL AND excluded.tokens_in IS NOT events.tokens_in) OR (excluded.tokens_out IS NOT NULL AND excluded.tokens_out IS NOT events.tokens_out) OR (excluded.tokens_cache_read IS NOT NULL AND excluded.tokens_cache_read IS NOT events.tokens_cache_read) OR (excluded.tokens_cache_write IS NOT NULL AND excluded.tokens_cache_write IS NOT events.tokens_cache_write) OR (excluded.model IS NOT NULL AND excluded.model IS NOT events.model)''',d)
            for limit in batch.limits:
                d = asdict(limit)
                d['account_id'] = account_of(limit.harness, limit.provider, limit.scope)
                c.execute('''INSERT INTO limit_snapshots (account_id,harness,provider,scope,window,used_percent,resets_at,source,recorded_at)
                SELECT :account_id,:harness,:provider,:scope,:window,:used_percent,:resets_at,:source,:recorded_at
                WHERE NOT EXISTS (SELECT 1 FROM limit_snapshots WHERE account_id IS :account_id AND harness IS :harness AND provider IS :provider AND scope IS :scope AND window IS :window AND used_percent IS :used_percent AND resets_at IS :resets_at AND source IS :source AND recorded_at IS :recorded_at)''',d)
            for delegation in batch.delegations:
                c.execute('''INSERT INTO delegations (ext_id,ts,provider,model,task_kind,outcome,tokens,duration_s,note)
                VALUES (:ext_id,:ts,:provider,:model,:task_kind,:outcome,:tokens,:duration_s,:note) ON CONFLICT(ext_id) DO NOTHING''',asdict(delegation))
            changed = c.total_changes > before
            for cursor in batch.cursors:
                c.execute('INSERT INTO cursors VALUES (?,?,?,?) ON CONFLICT(adapter,key) DO UPDATE SET offset=excluded.offset,mtime=excluded.mtime', cursor)
            return changed

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

    def insert_run(self, rec: dict) -> None:
        columns = self._run_columns()
        if not rec or not set(rec) <= columns:
            raise ValueError('Unknown run fields')
        values = dict(rec)
        if values.get('prompt_preview') is not None:
            values['prompt_preview'] = values['prompt_preview'][:200]
        names = list(values)
        with self._lock, self._writer:
            self._writer.execute(f"INSERT INTO runs ({','.join(names)}) VALUES ({','.join('?' for _ in names)})", tuple(values[n] for n in names))

    def _run_columns(self) -> set[str]:
        with self._lock:
            return {row[1] for row in self._writer.execute('PRAGMA table_info(runs)')}

    def update_run(self, id: str, **fields) -> None:
        if not set(fields) <= self._run_columns() - {'id'}:
            raise ValueError('Unknown or immutable run fields')
        if not fields:
            return
        if fields.get('prompt_preview') is not None:
            fields['prompt_preview'] = fields['prompt_preview'][:200]
        with self._lock, self._writer:
            self._writer.execute(f"UPDATE runs SET {','.join(name + '=?' for name in fields)} WHERE id=?", (*fields.values(), id))

    def get_run(self, id: str) -> dict | None:
        with self._lock:
            cursor = self._writer.execute('SELECT * FROM runs WHERE id=?', (id,))
            row = cursor.fetchone()
            return dict(zip((col[0] for col in cursor.description), row)) if row else None

    def list_runs(self, limit: int = 100) -> list[dict]:
        with self._lock:
            cursor = self._writer.execute('SELECT * FROM runs ORDER BY started_at DESC, rowid DESC LIMIT ?', (max(0, limit),))
            names = [col[0] for col in cursor.description]
            return [dict(zip(names, row)) for row in cursor.fetchall()]

    def mark_interrupted(self) -> int:
        with self._lock, self._writer:
            return self._writer.execute("UPDATE runs SET status='interrupted' WHERE status IN ('queued','starting','running','idle','needs_you','closing')").rowcount
