"""Builds an invented opencode.db with the four tables the adapter reads.

Times are milliseconds, as in opencode. Nothing here comes from real logs.
"""
import json
import sqlite3
from pathlib import Path

T0 = 1730000000000


def make_opencode_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript('''
        CREATE TABLE session (id TEXT PRIMARY KEY, parent_id TEXT, directory TEXT, title TEXT, model TEXT,
                              time_created INTEGER, time_updated INTEGER, time_archived INTEGER);
        CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT);
        CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT);
        CREATE TABLE todo (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER);
        CREATE TABLE credential (id TEXT PRIMARY KEY, secret TEXT);
    ''')
    conn.execute('INSERT INTO credential VALUES (?, ?)', ('c1', 'must-never-be-read'))
    conn.execute('INSERT INTO session VALUES (?,?,?,?,?,?,?,?)',
                 ('sess1', None, '/home/user/proj', 'Test session', 'kr/auto', T0, T0 + 100000, None))
    conn.execute('INSERT INTO session VALUES (?,?,?,?,?,?,?,?)',
                 ('sess2', 'sess1', '/home/user/proj', 'Child', None, T0 + 1000, T0 + 2000, None))
    conn.execute('INSERT INTO message VALUES (?,?,?,?,?)',
                 ('m0', 'sess1', T0 + 40000, T0 + 40000, json.dumps({'role': 'user'})))
    conn.execute('INSERT INTO message VALUES (?,?,?,?,?)',
                 ('m1', 'sess1', T0 + 50000, T0 + 60000, json.dumps({
                     'role': 'assistant', 'modelID': 'kr/auto', 'providerID': 'ninerouter',
                     'tokens': {'input': 20303, 'output': 3, 'reasoning': 0, 'cache': {'read': 0, 'write': 0}},
                     'finish': 'stop'})))
    conn.execute('INSERT INTO part VALUES (?,?,?,?,?,?)',
                 ('p1', 'm1', 'sess1', T0 + 51000, T0 + 51000, json.dumps({'type': 'tool', 'tool': 'read_file', 'state': {'status': 'completed'}})))
    conn.execute('INSERT INTO part VALUES (?,?,?,?,?,?)',
                 ('p2', 'm1', 'sess1', T0 + 52000, T0 + 52000, json.dumps({'type': 'tool', 'tool': 'bash', 'state': {'status': 'error'}})))
    conn.commit()
    conn.close()
