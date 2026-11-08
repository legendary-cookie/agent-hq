"""SQLite storage. One connection shared behind a lock: the app is small and writes are short."""
import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name TEXT NOT NULL,
  pw_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'staff',           -- admin | manager | staff | a custom role (perms.py)
  disabled INTEGER NOT NULL DEFAULT 0,
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created REAL NOT NULL,
  expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS agents (
  id TEXT PRIMARY KEY,                          -- ct201-assistant
  handle TEXT NOT NULL UNIQUE COLLATE NOCASE,   -- used for @mentions
  name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'zylos',           -- zylos | hermes | openai | demo
  grp TEXT NOT NULL DEFAULT 'Staff',            -- Management | Staff | Department | Specialist | Other
  title TEXT NOT NULL DEFAULT '',
  department TEXT NOT NULL DEFAULT '',
  owner_name TEXT NOT NULL DEFAULT '',
  owner_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  reports_to TEXT,
  visibility TEXT NOT NULL DEFAULT 'everyone',  -- everyone | owner
  color TEXT NOT NULL DEFAULT '',
  config TEXT NOT NULL DEFAULT '{}',            -- per-kind settings (openai: base_url, model, system, key)
  token_hash TEXT,
  last_seen REAL,
  bridge_info TEXT NOT NULL DEFAULT '{}',
  disabled INTEGER NOT NULL DEFAULT 0,
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,                           -- dm | channel | task
  name TEXT NOT NULL DEFAULT '',
  topic TEXT NOT NULL DEFAULT '',
  created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created REAL NOT NULL,
  reply_mode TEXT NOT NULL DEFAULT 'lead',      -- lead | all | mention
  lead_agent TEXT,
  private INTEGER NOT NULL DEFAULT 0,
  archived INTEGER NOT NULL DEFAULT 0,
  last_message_at REAL
);
CREATE TABLE IF NOT EXISTS members (
  conv_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  member_type TEXT NOT NULL,                    -- user | agent
  member_id TEXT NOT NULL,
  last_read_id INTEGER NOT NULL DEFAULT 0,
  joined REAL NOT NULL,
  PRIMARY KEY (conv_id, member_type, member_id)
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  conv_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  author_type TEXT NOT NULL,                    -- user | agent | system
  author_id TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'done',          -- done | streaming | error
  delivery_id INTEGER,
  files TEXT NOT NULL DEFAULT '[]',
  created REAL NOT NULL,
  edited REAL
);
CREATE INDEX IF NOT EXISTS messages_conv ON messages(conv_id, id);
CREATE TABLE IF NOT EXISTS deliveries (
  id INTEGER PRIMARY KEY,
  agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  conv_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  trigger_id INTEGER NOT NULL,
  state TEXT NOT NULL DEFAULT 'queued',         -- queued | sent | working | done | error | cancelled
  depth INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  created REAL NOT NULL,
  picked REAL,
  finished REAL
);
CREATE INDEX IF NOT EXISTS deliveries_agent ON deliveries(agent_id, state);
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'todo',          -- todo | doing | review | done
  priority TEXT NOT NULL DEFAULT 'normal',      -- low | normal | high
  assignee TEXT REFERENCES agents(id) ON DELETE SET NULL,
  conv_id INTEGER REFERENCES conversations(id) ON DELETE SET NULL,
  created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  due TEXT,
  created REAL NOT NULL,
  updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
  id TEXT PRIMARY KEY,
  user_id INTEGER,
  conv_id INTEGER,
  name TEXT NOT NULL,
  mime TEXT NOT NULL,
  size INTEGER NOT NULL,
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS voice_keys (           -- "Hey Siri, ask lead": one key per person and agent
  id INTEGER PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  conv_id INTEGER REFERENCES conversations(id) ON DELETE SET NULL,
  label TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL,
  last_used REAL,
  disabled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS spoken_messages (       -- said out loud (Siri or talk mode): the agent answers for the ear
  message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS roles (                 -- edits to built-in roles, and custom roles (see perms.py)
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  permissions TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS agent_activity (         -- what an agent is doing now: its latest steps (bridge 0.3+)
  agent_id TEXT PRIMARY KEY REFERENCES agents(id) ON DELETE CASCADE,
  steps TEXT NOT NULL DEFAULT '[]',
  log_updated REAL,                              -- when the agent's session log last changed
  received REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS telegram_chats (        -- read-only copies of an agent's Telegram chats
  agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  chat_id TEXT NOT NULL,                         -- Telegram chat id (negative for groups)
  conv_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  last_source_id INTEGER NOT NULL DEFAULT 0,     -- highest c4 row id copied
  PRIMARY KEY (agent_id, chat_id)
);
CREATE TABLE IF NOT EXISTS external_names (        -- who said it, for messages copied from Telegram
  message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
  name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS logins (
  username TEXT PRIMARY KEY COLLATE NOCASE,
  failures INTEGER NOT NULL DEFAULT 0,
  locked_until REAL NOT NULL DEFAULT 0
);
"""


class Db:
    def __init__(self, path):
        self.path = str(path)
        if self.path != ':memory:':
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.execute('PRAGMA journal_mode=WAL')
            self.conn.execute('PRAGMA foreign_keys=ON')
            self.conn.executescript(SCHEMA)
            self._migrate()

    def _migrate(self):
        cols = {r[1] for r in self.conn.execute('PRAGMA table_info(users)')}
        for col, decl in (('team', "TEXT NOT NULL DEFAULT ''"),          # Management | Staff, for the Admin page
                          ('perm_overrides', "TEXT NOT NULL DEFAULT '{}'"),  # this person's changes to their role
                          ('pw_sealed', 'TEXT')):                         # encrypted password an admin can view
            if col not in cols:
                self.conn.execute('ALTER TABLE users ADD COLUMN %s %s' % (col, decl))
        mcols = {r[1] for r in self.conn.execute('PRAGMA table_info(messages)')}
        if 'reply_to' not in mcols:   # the message this one answers, and the bit of it that was highlighted
            self.conn.execute('ALTER TABLE messages ADD COLUMN reply_to INTEGER')
            self.conn.execute("ALTER TABLE messages ADD COLUMN reply_quote TEXT NOT NULL DEFAULT ''")
        # Old roles: "member" becomes "staff" (managers are moved to "manager" by the admin or set-up script).
        self.conn.execute("UPDATE users SET role='staff' WHERE role='member'")

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        with self.lock:
            row = self.conn.execute(sql, args).fetchone()
            return dict(row) if row else None

    def run(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args)
            return cur.lastrowid

    def tx(self):
        return _Tx(self)


class _Tx:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        self.db.lock.acquire()
        self.db.conn.execute('BEGIN IMMEDIATE')
        return self.db

    def __exit__(self, kind, *_):
        try:
            self.db.conn.execute('ROLLBACK' if kind else 'COMMIT')
        finally:
            self.db.lock.release()


def now():
    return time.time()


def loads(value, default):
    try:
        return json.loads(value) if value else default
    except ValueError:
        return default
