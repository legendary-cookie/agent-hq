import hashlib
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from hq.adapters.provider_usage import ProviderUsageAdapter
from hq.config import SourceCfg
from hq.store import Store
from tests.fixtures.pu.make_fixture import NOW, make_provider_usage_db


def iso(s):
    return datetime.fromisoformat(s).timestamp()


class ProviderUsageAdapterTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name) / 'provider-usage'
        self.dir.mkdir()
        self.db = self.dir / 'usage.db'
        make_provider_usage_db(self.db)
        self.store = Store(Path(tmp.name) / 'hq.db')
        self.addCleanup(self.store.close)
        self.adapter = ProviderUsageAdapter()

    def poll(self, path=None):
        return self.adapter.poll(self.store, SourceCfg(True, [path or self.dir]), now=NOW)

    def test_calls_to_delegations(self):
        b = self.poll()
        d = next(d for d in b.delegations if d.provider == 'codex')
        self.assertEqual((d.ext_id, d.model, d.task_kind, d.outcome, d.tokens, d.duration_s),
                         ('1', 'gpt-6.1-terra', 'implementation', 'ok', 5000, 45.0))
        self.assertEqual(d.ts, iso('2026-10-30T10:00:00+00:00'))
        self.assertEqual(b.warnings, 0)

    def test_accepts_db_file_or_its_folder(self):
        self.assertEqual(len(self.poll(self.db).delegations), 2)

    def test_active_block_to_limit(self):
        lim = next(l for l in self.poll().limits if l.scope == 'gemini-4-argon-medium')
        self.assertEqual((lim.harness, lim.provider, lim.scope, lim.window, lim.used_percent, lim.source),
                         ('antigravity', 'antigravity', 'gemini-4-argon-medium', 'block', 100.0, 'blocked'))
        self.assertEqual(lim.resets_at, iso('2026-11-02T17:49:26.741137+00:00'))
        self.assertEqual(lim.recorded_at, iso('2026-10-27T20:32:34+00:00'))

    def test_provider_wide_block_scope_star(self):
        self.assertIn('*', [l.scope for l in self.poll().limits])

    def test_expired_block_ignored(self):
        self.assertNotIn('old-model', [l.scope for l in self.poll().limits])

    def test_hermes_call_becomes_session(self):
        b = self.poll()
        (s,) = b.sessions
        self.assertEqual((s.id, s.harness, s.provider, s.model), ('hermes:2', 'hermes', 'ninerouter', 'kr/claude-sonnet-4.5'))
        self.assertEqual(s.started_at, iso('2026-10-30T11:00:00+00:00') - 120.0)
        e = next(e for e in b.events if e.kind == 'assistant')
        # tracker records one total; the in/out split is unknown, so it is not priced as output
        self.assertEqual((e.tokens_in, e.tokens_out, e.tokens_estimated), (8000, None, True))
        self.assertIn('turn_end', [e.kind for e in b.events])

    def test_never_writes(self):
        before = (self.db.stat().st_mtime, hashlib.sha256(self.db.read_bytes()).hexdigest())
        self.poll()
        self.assertEqual(before, (self.db.stat().st_mtime, hashlib.sha256(self.db.read_bytes()).hexdigest()))

    def test_missing_source_is_empty(self):
        b = self.poll(self.dir / 'nope')
        self.assertEqual((b.delegations, b.limits, b.warnings), ([], [], 0))


if __name__ == '__main__':
    unittest.main()
