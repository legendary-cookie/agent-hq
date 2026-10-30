import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from hq.adapters.opencode import OpencodeAdapter
from hq.config import SourceCfg
from hq.store import Store
from tests.fixtures.opencode.make_fixture import T0, make_opencode_db


class OpencodeAdapterTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / 'opencode.db'
        make_opencode_db(self.db)
        self.store = Store(Path(tmp.name) / 'hq.db')
        self.addCleanup(self.store.close)
        self.cfg = SourceCfg(True, [self.db])
        self.adapter = OpencodeAdapter()

    def poll(self):
        return self.adapter.poll(self.store, self.cfg)

    def test_reads_sessions_messages_parts(self):
        batch = self.poll()
        ids = {s.id: s for s in batch.sessions}
        self.assertEqual(set(ids), {'opencode:sess1', 'opencode:sess2'})
        self.assertEqual(ids['opencode:sess2'].parent_id, 'opencode:sess1')
        self.assertEqual(ids['opencode:sess1'].project, '/home/user/proj')
        self.assertEqual(ids['opencode:sess1'].started_at, T0 / 1000)
        kinds = sorted(e.kind for e in batch.events)
        self.assertEqual(kinds, ['assistant', 'error', 'tool_call', 'tool_call', 'turn_end', 'user'])
        self.assertEqual(batch.warnings, 0)

    def test_tokens_from_message_json(self):
        e = next(e for e in self.poll().events if e.kind == 'assistant')
        self.assertEqual((e.tokens_in, e.tokens_out, e.tokens_cache_read, e.tokens_cache_write), (20303, 3, 0, 0))
        self.assertEqual(e.model, 'kr/auto')

    def test_provider_and_model_from_latest_assistant_message(self):
        s = next(s for s in self.poll().sessions if s.id == 'opencode:sess1')
        self.assertEqual((s.provider, s.model), ('ninerouter', 'kr/auto'))

    def test_second_poll_is_incremental(self):
        self.store.apply(self.poll(), lambda *a: 'opencode-free')
        again = self.poll()
        self.assertEqual((again.sessions, again.events, again.warnings), ([], [], 0))

    def test_updated_message_picked_up_when_finished(self):
        conn = sqlite3.connect(self.db)
        conn.execute('INSERT INTO message VALUES (?,?,?,?,?)', ('m2', 'sess1', T0 + 70000, T0 + 70000,
                     json.dumps({'role': 'assistant', 'modelID': 'kr/auto', 'providerID': 'ninerouter'})))
        conn.commit()
        self.store.apply(self.poll(), lambda *a: 'opencode-free')
        self.assertFalse(any(e.ext_id == 'm2' for e in self.poll().events))
        conn.execute('UPDATE message SET time_updated=?, data=? WHERE id=?', (T0 + 90000, json.dumps({
            'role': 'assistant', 'modelID': 'kr/auto', 'providerID': 'ninerouter',
            'tokens': {'input': 10, 'output': 5, 'reasoning': 2, 'cache': {'read': 1, 'write': 0}}, 'finish': 'tool-calls'}), 'm2'))
        conn.commit()
        conn.close()
        events = self.poll().events
        e = next(e for e in events if e.ext_id == 'm2')
        self.assertEqual((e.tokens_in, e.tokens_out, e.tokens_cache_read), (10, 7, 1))
        self.assertFalse(any(x.kind == 'turn_end' and x.ext_id.startswith('m2') for x in events))

    def test_only_whitelisted_tables_queried(self):
        seen = []
        real = sqlite3.connect

        def traced(*args, **kwargs):
            conn = real(*args, **kwargs)
            conn.set_trace_callback(seen.append)
            return conn

        sqlite3.connect = traced
        try:
            self.poll()
        finally:
            sqlite3.connect = real
        reads = [q for q in seen if q.lstrip().upper().startswith('SELECT')]
        self.assertTrue(reads)
        for q in reads:
            tables = q.upper().split(' FROM ')[1].split()[0]
            self.assertIn(tables, {'SESSION', 'MESSAGE', 'PART', 'TODO'}, q)

    def test_locked_db_raises_cleanly(self):
        locker = sqlite3.connect(self.db)
        locker.execute('BEGIN EXCLUSIVE')
        try:
            with self.assertRaises(sqlite3.OperationalError):
                self.poll()
        finally:
            locker.rollback()
            locker.close()


if __name__ == '__main__':
    unittest.main()
