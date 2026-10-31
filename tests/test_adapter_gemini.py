"""Tests for the Gemini CLI adapter (plan Task 8)."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from hq.adapters.gemini_cli import GeminiCliAdapter
from hq.config import SourceCfg
from hq.store import Store

FIXTURES = Path(__file__).parent / 'fixtures' / 'gemini'


class GeminiCliAdapterTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name) / 'hq.db')
        self.addCleanup(self.store.close)
        self.cfg = SourceCfg(True, [FIXTURES])

    def poll(self):
        return GeminiCliAdapter().poll(self.store, self.cfg)

    def test_header_and_messages(self):
        batch = self.poll()
        self.assertEqual(len(batch.sessions), 1)
        s = batch.sessions[0]
        self.assertEqual((s.id, s.provider), ('gemini_cli:session-x', 'google'))
        self.assertEqual(s.started_at, 1793318400.0)
        kinds = [(e.ext_id, e.kind) for e in batch.events]
        self.assertEqual(kinds, [('u1', 'user'), ('a1', 'assistant'), ('u2', 'user')])
        self.assertEqual(batch.events[0].preview, 'Summarize the plan doc')

    def test_repeated_set_snapshots_do_not_duplicate(self):
        first = self.poll()
        self.assertEqual(len({e.ext_id for e in first.events}), 3)
        self.assertTrue(self.store.apply(first, lambda *a: 'unassigned'))
        self.store.derive_tasks(['gemini_cli:session-x'])
        second = self.poll()
        self.assertEqual(second.events, [])
        self.assertFalse(self.store.apply(second, lambda *a: 'unassigned'))
        db = self.store.path
        conn = sqlite3.connect(db)
        try:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM events WHERE session_id='gemini_cli:session-x'").fetchone()[0], 3)
        finally:
            conn.close()

    def test_tokens_when_present(self):
        batch = self.poll()
        by_id = {e.ext_id: e for e in batch.events}
        a1 = by_id['a1']
        self.assertEqual((a1.model, a1.tokens_in, a1.tokens_out), ('gemini-4-argon-high', 1000, 50))
        self.assertEqual((a1.tokens_cache_read, a1.tokens_cache_write), (None, None))
        self.assertFalse(a1.tokens_estimated)
        u1 = by_id['u1']
        self.assertEqual((u1.model, u1.tokens_in, u1.tokens_out), (None, None, None))


if __name__ == '__main__':
    unittest.main()


class GeminiRepeatedHeaderTests(unittest.TestCase):
    def test_repeated_header_is_an_update_not_a_warning(self):
        import tempfile
        from hq.store import Store
        from hq.config import SourceCfg
        from hq.adapters.gemini_cli import GeminiCliAdapter
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        store = Store(Path(tmp.name) / 'hq.db'); self.addCleanup(store.close)
        b = GeminiCliAdapter().poll(store, SourceCfg(True, [Path(__file__).parent / 'fixtures/gemini/tmp/*/chats']))
        self.assertEqual(b.warnings, 0)

