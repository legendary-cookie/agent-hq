"""Tests for the Antigravity adapter (plan Task 7)."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from hq.adapters.antigravity import AntigravityAdapter
from hq.config import SourceCfg
from hq.store import Store

try:
    from .fixtures.antigravity.make_fixture import append_steps, build_conversation_db
except ImportError:
    from tests.fixtures.antigravity.make_fixture import append_steps, build_conversation_db

FIXTURES = Path(__file__).parent / 'fixtures' / 'antigravity'
SEED_STEPS = 3


class AntigravityAdapterTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        cli = self.root / 'antigravity-cli'
        self.conv_dir = cli / 'conversations'
        self.conv_dir.mkdir(parents=True)
        self.history_path = cli / 'history.jsonl'
        shutil.copy(FIXTURES / 'history.jsonl', self.history_path)
        self.store = Store(self.root / 'hq.db')
        self.addCleanup(self.store.close)

    def poll(self):
        cfg = SourceCfg(True, [self.conv_dir, self.history_path])
        return AntigravityAdapter().poll(self.store, cfg)

    def test_history_creates_sessions_and_user_events(self):
        batch = self.poll()
        sessions = {s.id: s for s in batch.sessions}
        self.assertEqual(set(sessions), {'antigravity:agy-c1', 'antigravity:agy-c2'})
        s = sessions['antigravity:agy-c1']
        self.assertEqual(s.harness, 'antigravity')
        self.assertEqual(s.provider, 'google')
        self.assertIsNone(s.model)
        self.assertEqual(s.project, 'C:/workers/worker-a')
        self.assertEqual(s.started_at, 1793318400.0)
        prompts = [e for e in batch.events if e.kind == 'user']
        self.assertEqual([p.preview for p in prompts if p.session_id == 'antigravity:agy-c1'],
                         ['Fix the failing login tests', 'Now run the full suite'])

    def test_step_growth_emits_activity(self):
        build_conversation_db(self.conv_dir / 'agy-c1.db', SEED_STEPS)
        first = self.poll()
        self.assertFalse([e for e in first.events if e.ext_id.startswith('steps#')])
        baseline = next(s for s in first.sessions if s.id == 'antigravity:agy-c1')
        self.assertAlmostEqual(baseline.last_event_at, os.path.getmtime(self.conv_dir / 'agy-c1.db'), places=4)
        self.store.apply(first, lambda *a: 'unassigned')
        append_steps(self.conv_dir / 'agy-c1.db', extra=2)
        second = self.poll()
        steps = [e for e in second.events if e.ext_id.startswith('steps#')]
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].ext_id, 'steps#5')
        self.assertEqual(steps[0].kind, 'assistant')
        self.assertAlmostEqual(steps[0].ts, os.path.getmtime(self.conv_dir / 'agy-c1.db'), places=4)
        updated = next(s for s in second.sessions if s.id == 'antigravity:agy-c1')
        self.assertAlmostEqual(updated.last_event_at, steps[0].ts, places=4)

    def test_tokens_unknown(self):
        build_conversation_db(self.conv_dir / 'agy-c1.db', SEED_STEPS)
        batch = self.poll()
        for event in batch.events:
            self.assertEqual((event.tokens_in, event.tokens_out, event.tokens_cache_read,
                              event.tokens_cache_write), (None, None, None, None))
            self.assertFalse(event.tokens_estimated)
            self.assertIsNone(event.model)
            self.assertIsNone(event.tool)

    def test_missing_conversation_db_ok(self):
        batch = self.poll()
        sessions = {s.id for s in batch.sessions}
        self.assertEqual(sessions, {'antigravity:agy-c1', 'antigravity:agy-c2'})


if __name__ == '__main__':
    unittest.main()


def _varint(value):
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    return bytes(result + bytes([value]))


def _int_field(number, value):
    return _varint(number * 8) + _varint(value)


def _bytes_field(number, value):
    return _varint(number * 8 + 2) + _varint(len(value)) + value


def _metadata(response=b'response-1', model=b'gemini-test'):
    usage = _int_field(2, 100) + _int_field(3, 30) + _int_field(5, 12) + _bytes_field(11, response)
    timestamp = _int_field(1, 1700000000) + _int_field(2, 500000000)
    chat = _bytes_field(4, usage) + _bytes_field(9, _bytes_field(4, timestamp)) + _bytes_field(19, model)
    return _bytes_field(1, chat)


class AntigravityMetadataTests(AntigravityAdapterTests):
    def add_metadata(self, blobs):
        import sqlite3
        path = self.conv_dir / 'agy-c1.db'
        build_conversation_db(path, 1)
        conn = sqlite3.connect(path)
        try:
            conn.execute('CREATE TABLE gen_metadata (data BLOB)')
            conn.executemany('INSERT INTO gen_metadata VALUES (?)', [(blob,) for blob in blobs])
            conn.commit()
        finally:
            conn.close()

    def test_usage_from_gen_metadata(self):
        self.add_metadata([_metadata()])
        event = next(e for e in self.poll().events if e.ext_id == 'resp:response-1')
        self.assertEqual((event.tokens_in, event.tokens_out, event.tokens_cache_read, event.ts), (100, 30, 12, 1700000000.5))

    def test_repeated_response_id_counted_once(self):
        self.add_metadata([_metadata(), _metadata()])
        batch = self.poll()
        self.assertEqual(len([e for e in batch.events if e.ext_id == 'resp:response-1']), 1)
        self.store.apply(batch, lambda *args: 'unassigned')
        self.store.apply(self.poll(), lambda *args: 'unassigned')
        conn = self.store.conn()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM events WHERE ext_id='resp:response-1'").fetchone()[0], 1)
        finally:
            conn.close()

    def test_model_from_field_19(self):
        self.add_metadata([_metadata()])
        batch = self.poll()
        self.assertEqual(next(e for e in batch.events if e.ext_id.startswith('resp:')).model, 'gemini-test')
        self.assertEqual(next(s for s in batch.sessions if s.id == 'antigravity:agy-c1').model, 'gemini-test')
