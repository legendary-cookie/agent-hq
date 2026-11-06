import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from hq.__main__ import build_adapters
from hq.config import load_config
from hq.remote.collector import RemoteCollector
from hq.remote.config import Host, load_hosts
from hq.remote.ssh import run_scan
from hq.remote.zylos_metrics import to_batch
from hq.store import Store

ROOT = Path(__file__).resolve().parent.parent
SECRET = 'SECRET-CHAT-TEXT-7f3a'


class ZylosMetricsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home = self.root / 'home'
        self.db = self.home / 'zylos/comm-bridge/c4.db'
        self.db.parent.mkdir(parents=True)
        self.make_db()
        self.store = Store(self.root / 'hq.db')
        self.addCleanup(self.store.close)
        self.cfg = load_config(ROOT / 'config')
        self.host = Host('agent-ada', 'zylos@agent-ada', 'linux', None, 60, ('zylos_metrics',))
        self.answers = []
        self.col = RemoteCollector(self.store, self.cfg, [self.host], self.root, build_adapters, run_scan=self.scan)

    def make_db(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('CREATE TABLE IF NOT EXISTS conversations (id INTEGER PRIMARY KEY, direction TEXT, channel TEXT, content TEXT, status TEXT, timestamp TEXT)')

    def add(self, *rows):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.executemany('INSERT INTO conversations (direction, channel, content, status, timestamp) VALUES (?,?,?,?,?)', rows)

    def scan(self, host, request, scanner_text):
        env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home))
        code = '_HQ_REQUEST = None\n' + scanner_text + '\nmain(' + repr(json.dumps(request)) + ')\n'
        result = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, check=True)
        self.answers.append(result.stdout.decode('utf-8'))
        return json.loads(result.stdout)

    def rows(self, sql):
        with closing(self.store.conn()) as conn:
            return conn.execute(sql).fetchall()

    def test_hosts_json_accepts_source(self):
        (self.root / 'hosts.json').write_text(json.dumps({'hosts': [
            {'name': 'agent-ada', 'ssh': 'zylos@agent-ada', 'os': 'linux', 'sources': ['zylos_metrics']}]}), encoding='utf-8')
        hosts, warnings = load_hosts(self.root)
        self.assertEqual((len(hosts), warnings), (1, []))

    def test_counts_direction_channel_and_failures(self):
        self.add(('in', 'telegram', SECRET, 'done', '2026-11-05T01:00:00Z'),
                 ('out', 'telegram', SECRET, 'failed', '2026-11-05T01:01:00Z'))
        self.col.run_once()
        self.assertEqual(self.rows('select kind, tool, count(*) from events group by kind, tool order by kind'),
                         [('assistant', 'telegram', 1), ('error', 'telegram', 1), ('user', 'telegram', 1)])
        self.assertEqual(self.rows('select id, harness, host from sessions'), [('agent-ada/zylos:comm', 'zylos', 'agent-ada')])

    def test_message_log_is_not_a_live_session(self):
        self.add(('in', 'telegram', 'a', 'done', '2026-11-05T01:00:00Z'))
        self.col.run_once()
        self.add(('in', 'telegram', 'b', 'done', '2026-11-05T02:00:00Z'))
        self.col.run_once()
        (started, last, ended), = self.rows('select started_at, last_event_at, ended_at from sessions')
        self.assertLess(started, last)
        self.assertEqual(ended, last)

    def test_message_text_never_leaves_scanner_or_reaches_store(self):
        self.add(('in', 'telegram', SECRET, 'done', '2026-11-05T01:00:00Z'))
        self.col.run_once()
        self.assertEqual(len(self.answers), 1)
        self.assertNotIn(SECRET, self.answers[0])
        self.assertEqual(self.rows('select preview from events'), [(None,)])
        self.store.close()
        self.assertNotIn(SECRET.encode(), (self.root / 'hq.db').read_bytes())
        self.assertFalse((self.root / 'remote/agent-ada/zylos').exists())
        self.store = Store(self.root / 'hq.db')
        self.addCleanup(self.store.close)

    def test_real_payload_through_python_dash(self):
        self.add(('in', 'telegram', SECRET, 'done', '2026-11-05T01:00:00Z'))
        env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home))
        def local(argv, **kwargs):
            return subprocess.Popen([sys.executable, '-'], env=env, **kwargs)
        scanner = (ROOT / 'hq/remote/scanner.py').read_text(encoding='utf-8')
        answer = run_scan(self.host, {'sources': ['zylos_metrics'], 'zylos_after': 0}, scanner, local)
        self.assertEqual(answer['zylos_metrics']['rows'], [[1, 'in', 'telegram', False, '2026-11-05T01:00:00Z']])

    def test_database_behind_a_symlink_outside_home(self):
        # Real Zylos installs link ~/zylos/comm-bridge to /opt/zylos/comm-bridge.
        outside = self.root / 'opt/comm-bridge'
        outside.parent.mkdir()
        self.db.parent.rename(outside)
        try:
            self.db.parent.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('symlinks unavailable')
        self.add(('in', 'telegram', 'a', 'done', '2026-11-05T01:00:00Z'))
        self.col.run_once()
        self.assertEqual(self.rows('select count(*) from events'), [(1,)])

    def test_previews_setting_cannot_turn_text_on(self):
        self.cfg.zylos_previews = True
        self.add(('in', 'telegram', SECRET, 'done', '2026-11-05T01:00:00Z'))
        self.col.run_once()
        self.assertEqual(self.rows('select preview from events'), [(None,)])

    def test_only_new_rows_are_read(self):
        self.add(('in', 'telegram', 'a', 'done', '2026-11-05T01:00:00Z'))
        self.col.run_once()
        self.col.run_once()
        self.assertEqual(json.loads(self.answers[1])['zylos_metrics']['rows'], [])
        self.add(('out', 'telegram', 'b', 'done', '2026-11-05T01:02:00Z'))
        self.col.run_once()
        self.assertEqual(len(json.loads(self.answers[2])['zylos_metrics']['rows']), 1)
        self.assertEqual(self.rows('select count(*) from events'), [(2,)])

    def test_replaced_database_is_counted_again_without_collisions(self):
        self.add(('in', 'telegram', 'a', 'done', '2026-11-05T01:00:00Z'),
                 ('in', 'telegram', 'b', 'done', '2026-11-05T01:01:00Z'))
        self.col.run_once()
        self.db.unlink()
        self.make_db()
        self.add(('in', 'telegram', 'c', 'done', '2026-11-05T02:00:00Z'))
        self.col.run_once()
        self.assertTrue(self.col.health()['agent-ada']['catching_up'])
        self.col.run_once()
        self.assertEqual(self.rows('select count(*) from events'), [(3,)])

    def test_missing_database_is_a_note_not_an_error(self):
        self.db.unlink()
        self.col.run_once()
        health = self.col.health()['agent-ada']
        self.assertEqual(health['state'], 'ok')
        self.assertIn('not found', ' '.join(health['notes']))
        self.assertEqual(self.rows('select count(*) from events'), [(0,)])

    def test_wrong_schema_is_reported_and_does_not_stop_the_pass(self):
        self.db.unlink()
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('CREATE TABLE other (id INTEGER)')
        self.col.run_once()
        self.assertIn('zylos_metrics', ' '.join(self.col.health()['agent-ada']['notes']))

    def test_bad_timestamp_skipped_but_cursor_moves_on(self):
        self.add(('in', 'telegram', 'a', 'done', 'not a date'), ('in', 'telegram', 'b', 'done', '2026-11-05T01:00:00Z'))
        self.col.run_once()
        self.col.run_once()
        self.assertEqual(self.rows('select count(*) from events'), [(1,)])
        self.assertEqual(json.loads(self.answers[1])['zylos_metrics']['rows'], [])

    def test_unexpected_channel_is_not_stored(self):
        self.add(('in', 'Jane Doe <jane@example.com>', 'a', 'done', '2026-11-05T01:00:00Z'))
        self.col.run_once()
        self.assertEqual(self.rows('select tool from events'), [('other',)])

    def test_hostile_answers_do_not_move_cursor(self):
        for rows in ([[1, 'in', 'x', False]], [['1', 'in', 'x', False, '2026-11-05T01:00:00Z']],
                     [[0, 'in', 'x', False, '2026-11-05T01:00:00Z']], 'rows'):
            batch, _, notes = to_batch({'found': True, 'max_id': 5, 'rows': rows}, self.host, (0, 0))
            self.assertEqual((batch.events, batch.cursors), ([], []))
            self.assertTrue(notes)

    def test_large_backlog_arrives_in_steps(self):
        self.add(*[('in', 'telegram', 'x', 'done', '2026-11-05T01:00:00Z')] * 3)
        scanner = (ROOT / 'hq/remote/scanner.py').read_text(encoding='utf-8').replace('ZYLOS_ROW_LIMIT = 20000', 'ZYLOS_ROW_LIMIT = 2')
        col = RemoteCollector(self.store, self.cfg, [self.host], self.root, build_adapters, run_scan=self.scan, scanner_text=scanner)
        col.run_once()
        self.assertTrue(col.health()['agent-ada']['catching_up'])
        self.assertEqual(self.rows('select count(*) from events'), [(2,)])
        col.run_once()
        self.assertFalse(col.health()['agent-ada']['catching_up'])
        self.assertEqual(self.rows('select count(*) from events'), [(3,)])
