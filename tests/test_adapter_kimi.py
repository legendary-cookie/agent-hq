import json
import tempfile
import unittest
from pathlib import Path
from shutil import copyfile

from hq.adapters.kimi import KimiAdapter
from hq.config import SourceCfg
from hq.store import Store

FIX = Path(__file__).parent / 'fixtures' / 'kimi'

class KimiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'test.db'); self.addCleanup(self.store.close)
        self.cfg = SourceCfg(True, [self.root / 'sessions'])
        self.adapter = KimiAdapter()

    def write(self, rel, rows):
        path = self.root / 'sessions' / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        return path

    def test_kimi_code_usage_record(self):
        path = self.root / 'sessions/ws/s/agents/main/wire.jsonl'; path.parent.mkdir(parents=True)
        copyfile(FIX / 'code/workspace-a/session-code/agents/agent-a/wire.jsonl', path)
        batch = self.adapter.poll(self.store, self.cfg)
        event = batch.events[0]
        self.assertEqual((event.tokens_in, event.tokens_out, event.tokens_cache_read, event.tokens_cache_write), (11, 7, 2, 3))
        self.assertEqual(event.model, 'kimi-code/moonshot-v1-128k')

    def test_subagent_dir_links_parent(self):
        self.write('ws/s/agents/worker/wire.jsonl', [{'type': 'metadata', 'created_at': '2026-01-01T00:00:00Z'}])
        session = self.adapter.poll(self.store, self.cfg).sessions[0]
        self.assertEqual((session.id, session.parent_id), ('kimi:s:worker', 'kimi:s'))

    def test_status_updates_keep_newest_per_message(self):
        path = self.root / 'sessions/group/session/wire.jsonl'; path.parent.mkdir(parents=True)
        copyfile(FIX / 'cli/group-a/session-a/wire.jsonl', path)
        batch = self.adapter.poll(self.store, self.cfg)
        self.store.apply(batch, lambda *args: 'unassigned')
        conn = self.store.conn(); self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT tokens_in FROM events WHERE ext_id='message-1'").fetchone(), (35,))
        self.assertEqual(conn.execute("SELECT tokens_in FROM events WHERE ext_id='message-3'").fetchone(), (10,))

    def test_empty_real_session_no_warnings(self):
        path = self.root / 'sessions/ws/s/agents/main/wire.jsonl'; path.parent.mkdir(parents=True)
        copyfile(FIX / 'empty.jsonl', path)
        batch = self.adapter.poll(self.store, self.cfg)
        self.assertEqual((len(batch.sessions), len(batch.events), batch.warnings), (1, 0, 0))

    def test_incremental(self):
        row = {'type':'usage.record','usageScope':'turn','time':1772445660000,'usage':{'inputOther':1}}
        path = self.write('ws/s/agents/main/wire.jsonl', [row])
        batch = self.adapter.poll(self.store, self.cfg); self.store.apply(batch, lambda *args: 'unassigned')
        with path.open('a') as stream: stream.write(json.dumps({**row, 'time':1772445661000}) + '\n')
        batch = self.adapter.poll(self.store, self.cfg)
        self.assertEqual(len(batch.events), 1)
        self.assertNotEqual(batch.events[0].ext_id, '0')

    def test_locked_kimi_file_counts_warning(self):
        import sqlite3
        from unittest.mock import patch
        self.write('a/wire.jsonl', [])
        with patch('hq.adapters.kimi.read_new_lines', side_effect=sqlite3.OperationalError('locked')):
            batch = self.adapter.poll(self.store, self.cfg)
        self.assertEqual(batch.warnings, 1)
        self.assertFalse(batch.cursors)
