"""DeepSeek Harness v4 logs (session.v4.jsonl.zstd). Record shapes taken from real
v4 logs on this machine (2026-11-02); every value below is invented."""
import json
import tempfile
import unittest
from pathlib import Path

from hq import zstd_frames
from hq.adapters.dsh import DshAdapter
from hq.config import SourceCfg
from hq.store import Store

T = '2026-11-02T10:00:{:02d}Z'


def v4_parent():
    return [
        {'type': 'session', 'id': 'p1', 'createdAt': T.format(0), 'cwd': '/w', 'delegationDepth': 0,
         'isSeeded': False, 'version': 4},
        {'type': 'request/header', 'seq': 1, 'time': T.format(1),
         'data': {'reason': 'turn', 'header': {'config': {'provider': 'codex', 'model': 'gpt-6.1-sol', 'maxTokens': 1}, 'tools': []}}},
        {'type': 'user/message', 'seq': 2, 'time': T.format(2), 'data': {'source': {'kind': 'user', 'rpcId': 'r'}}},
        {'type': 'user/message', 'seq': 3, 'time': T.format(3), 'data': {'source': {'kind': 'time-context', 'form': 'f', 'sections': []}}},
        {'type': 'tool/call', 'seq': 4, 'time': T.format(4), 'data': {'callId': 'c1', 'name': 'shell', 'arguments': {}, 'step': 1, 'turn': 1}},
        {'type': 'assistant/message', 'seq': 5, 'time': T.format(5), 'surfaceOp': None,
         'data': {'message': {}, 'step': 1, 'stream': [], 'turn': 1,
                  'usage': {'inputTokens': 100, 'outputTokens': 20, 'reasoningTokens': 5, 'cacheReadTokens': 300}}},
        {'type': 'compaction/summary', 'seq': 6, 'time': T.format(6),
         'data': {'provider': 'codex', 'model': 'gpt-6.1-sol', 'usage': {'inputTokens': 50, 'outputTokens': 10, 'totalTokens': 60}}},
        {'type': 'user/message', 'seq': 7, 'time': T.format(7),
         'data': {'source': {'kind': 'agent-message', 'form': 'f', 'senderSessionId': 'c1'}}},
        {'type': 'turn/end', 'seq': 8, 'time': T.format(8), 'data': {'reason': 'completed', 'turn': 1}},
    ]


def v4_child():
    return [
        {'type': 'session', 'id': 'c1', 'createdAt': T.format(10), 'cwd': '/w', 'delegationDepth': 1,
         'isSeeded': False, 'version': 4, 'agentPreset': 'x'},
        {'type': 'user/message', 'seq': 1, 'time': T.format(11), 'data': {'source': {'kind': 'user'}}},
    ]


@unittest.skipUnless(zstd_frames.available, 'compression.zstd needs Python 3.14+')
class DshV4Tests(unittest.TestCase):
    def setUp(self):
        from compression import zstd
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / 'sessions'
        for name, rows in (('p1', v4_parent()), ('c1', v4_child())):
            folder = root / '--w--' / name
            folder.mkdir(parents=True)
            frames = b''.join(zstd.compress((json.dumps(r) + chr(10)).encode()) for r in rows)
            (folder / 'session.v4.jsonl.zstd').write_bytes(frames)
        self.store = Store(Path(tmp.name) / 'hq.db')
        self.addCleanup(self.store.close)
        self.batch = DshAdapter().poll(self.store, SourceCfg(True, [root]))
        self.sessions = {s.id: s for s in self.batch.sessions}
        self.kinds = [(e.session_id, e.kind) for e in self.batch.events]

    def test_provider_and_model_from_request_header(self):
        s = self.sessions['dsh:p1']
        self.assertEqual((s.provider, s.model), ('codex', 'gpt-6.1-sol'))

    def test_tool_calls_become_events(self):
        tools = [e for e in self.batch.events if e.kind == 'tool_call']
        self.assertEqual([(t.tool, t.ext_id) for t in tools], [('shell', 'call:c1')])

    def test_real_user_prompts_only(self):
        self.assertEqual(self.kinds.count(('dsh:p1', 'user')), 2)

    def test_turn_end_emitted(self):
        self.assertIn(('dsh:p1', 'turn_end'), self.kinds)

    def test_compaction_usage_counted(self):
        totals = sum((e.tokens_in or 0) + (e.tokens_out or 0) + (e.tokens_cache_read or 0)
                     for e in self.batch.events if e.session_id == 'dsh:p1')
        self.assertEqual(totals, 100 + 25 + 300 + 50 + 10)

    def test_child_reporting_to_parent_links_parent(self):
        self.assertEqual(self.sessions['dsh:c1'].parent_id, 'dsh:p1')
