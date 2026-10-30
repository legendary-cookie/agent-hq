import tempfile
import unittest
from pathlib import Path

from hq.adapters.codex import CodexAdapter
from hq.config import SourceCfg
from hq.store import Store

FIXTURE = Path(__file__).parent / 'fixtures' / 'codex'

class CodexAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'hq.db')
        self.addCleanup(self.store.close)
        self.batch = CodexAdapter().poll(self.store, SourceCfg(True, [FIXTURE]))

    def test_session_from_meta_and_model_from_turn_context(self):
        s = self.batch.sessions[0]
        self.assertEqual((s.id, s.harness, s.provider, s.model, s.project), ('codex:x', 'codex', 'openai', 'gpt-6', '/project'))

    def test_token_normalization(self):
        e = next(e for e in self.batch.events if e.ext_id.startswith('tc#'))
        self.assertEqual((e.tokens_in, e.tokens_cache_read, e.tokens_cache_write, e.tokens_out), (13550, 12672, 0, 42))

    def test_rate_limits_to_snapshots(self):
        self.assertEqual([(x.window, x.used_percent, x.resets_at) for x in self.batch.limits], [('5h', 6.0, 1793218194), ('week', 2.0, 1793220000)])
        self.assertTrue(all(x.source == 'measured' and x.scope == '*' for x in self.batch.limits))

    def test_task_complete_is_turn_end(self):
        self.assertTrue(any(e.kind == 'turn_end' for e in self.batch.events))

    def test_tool_call_named(self):
        self.assertTrue(any(e.kind == 'tool_call' and e.tool == 'shell' for e in self.batch.events))
