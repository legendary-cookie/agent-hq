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


try:
    from compression import zstd
except ImportError:  # Python < 3.14
    zstd = None


@unittest.skipIf(zstd is None, 'compression.zstd needs Python 3.14+')
class CodexZstdTests(unittest.TestCase):
    """Codex compresses older sessions to .jsonl.zst; they must be read too."""

    def setUp(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / 'sessions' / '2026' / '08' / '27'
        root.mkdir(parents=True)
        raw = next(FIXTURE.rglob('*.jsonl')).read_bytes()
        (root / 'rollout-old.jsonl.zst').write_bytes(zstd.compress(raw))
        self.sessions = Path(tmp.name) / 'sessions'
        self.store = Store(Path(tmp.name) / 'hq.db')
        self.addCleanup(self.store.close)
        self.plain = CodexAdapter().poll(self.store, SourceCfg(True, [FIXTURE]))

    def poll(self):
        return CodexAdapter().poll(self.store, SourceCfg(True, [self.sessions]))

    def test_zst_session_read_like_plain(self):
        batch = self.poll()
        self.assertEqual([s.id for s in batch.sessions], [s.id for s in self.plain.sessions])
        self.assertEqual(len(batch.events), len(self.plain.events))
        self.assertEqual(len(batch.limits), len(self.plain.limits))
        self.assertEqual(batch.warnings, 0)

    def test_unchanged_zst_not_reread(self):
        first = self.poll()
        self.store.apply(first, lambda *a: 'chatgpt-plus')
        again = self.poll()
        self.assertEqual((again.sessions, again.events, again.limits), ([], [], []))


class CodexSubagentTests(unittest.TestCase):
    """A subagent file starts with its own session_meta and then copies its parent's.
    Only the first one defines the session; the parent comes from thread_spawn."""

    def setUp(self):
        import json
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        day = Path(tmp.name) / 'sessions' / '2026' / '09' / '22'
        day.mkdir(parents=True)
        spawn = {'subagent': {'thread_spawn': {'parent_thread_id': 'parent-1', 'depth': 1,
                                               'agent_path': '/root/refs', 'agent_nickname': 'Euclid'}}}
        rows = [
            {'type': 'session_meta', 'timestamp': '2026-09-22T12:00:00Z',
             'payload': {'id': 'child-1', 'cwd': '/p', 'timestamp': '2026-09-22T12:00:00Z', 'source': spawn}},
            {'type': 'session_meta', 'timestamp': '2026-09-22T12:00:00Z',
             'payload': {'id': 'parent-1', 'cwd': '/p', 'timestamp': '2026-09-22T10:00:00Z', 'source': 'vscode'}},
            {'type': 'response_item', 'timestamp': '2026-09-22T12:00:05Z',
             'payload': {'type': 'function_call', 'name': 'shell'}},
        ]
        (day / 'rollout-child.jsonl').write_text(''.join(json.dumps(r) + chr(10) for r in rows), encoding='utf-8')
        self.store = Store(Path(tmp.name) / 'hq.db')
        self.addCleanup(self.store.close)
        self.batch = CodexAdapter().poll(self.store, SourceCfg(True, [Path(tmp.name) / 'sessions']))

    def test_first_session_meta_defines_session(self):
        self.assertEqual([s.id for s in self.batch.sessions], ['codex:child-1'])
        self.assertEqual({e.session_id for e in self.batch.events}, {'codex:child-1'})

    def test_subagent_links_to_parent(self):
        (session,) = self.batch.sessions
        self.assertEqual(session.parent_id, 'codex:parent-1')
        self.assertEqual(session.title, 'Euclid')
