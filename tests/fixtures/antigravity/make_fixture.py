"""Builds Antigravity conversation databases for tests (Task 7 helper).

The real `conversations/<id>.db` files carry a `steps` table whose protobuf
blobs are never parsed by the adapter.  These helpers recreate that shape
with invented rows in a temp directory.
"""
import sqlite3
from pathlib import Path

_SCHEMA = 'CREATE TABLE IF NOT EXISTS steps (idx INTEGER PRIMARY KEY, step_type TEXT, status TEXT, proto BLOB)'


def _insert(conn, idx):
    conn.execute('INSERT INTO steps (idx, step_type, status, proto) VALUES (?,?,?,?)',
                 (idx, 'llm', 'STATUS_OK', b'\x08\x01'))


def build_conversation_db(db_path: Path, steps: int = 3):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.execute(_SCHEMA)
            for idx in range(steps):
                _insert(conn, idx)
    finally:
        conn.close()


def append_steps(db_path: Path, extra: int = 2):
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            top = conn.execute('SELECT COALESCE(MAX(idx), -1) FROM steps').fetchone()[0]
            for i in range(extra):
                _insert(conn, top + 1 + i)
    finally:
        conn.close()
