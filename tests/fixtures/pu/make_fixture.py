"""Builds an invented provider-usage DB with the exact schema of tracker.py
(integer ids, ISO-8601 text timestamps)."""
import sqlite3
from pathlib import Path

NOW = 1793400000.0  # 2026-10-30T... UTC, the reference time for the tests


def make_provider_usage_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript('''
        CREATE TABLE calls (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, provider TEXT NOT NULL,
                            model TEXT NOT NULL, task_kind TEXT NOT NULL, outcome TEXT NOT NULL,
                            tokens INTEGER, duration_s REAL, note TEXT);
        CREATE TABLE limits (provider TEXT NOT NULL, model TEXT NOT NULL, blocked_until TEXT NOT NULL,
                             reason TEXT, recorded_ts TEXT NOT NULL, PRIMARY KEY (provider, model));
    ''')
    conn.execute("INSERT INTO calls (ts, provider, model, task_kind, outcome, tokens, duration_s, note) VALUES "
                 "('2026-10-30T10:00:00+00:00', 'codex', 'gpt-6.1-terra', 'implementation', 'ok', 5000, 45.0, 'n')")
    conn.execute("INSERT INTO calls (ts, provider, model, task_kind, outcome, tokens, duration_s, note) VALUES "
                 "('2026-10-30T11:00:00+00:00', 'hermes', 'kr/claude-sonnet-4.5', 'implementation', 'ok', 8000, 120.0, NULL)")
    # Active block: ends 2026-11-02, after NOW. Expired block: ended before NOW.
    conn.execute("INSERT INTO limits VALUES ('antigravity', 'gemini-4-argon-medium', '2026-11-02T17:49:26.741137+00:00', '429', '2026-10-27T20:32:34+00:00')")
    conn.execute("INSERT INTO limits VALUES ('antigravity', 'old-model', '2026-10-01T00:00:00+00:00', '429', '2026-09-30T00:00:00+00:00')")
    conn.execute("INSERT INTO limits VALUES ('antigravity', '', '2026-11-02T17:51:32+00:00', '429', '2026-10-28T08:07:07+00:00')")
    conn.commit()
    conn.close()
