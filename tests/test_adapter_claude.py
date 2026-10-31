from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from hq.adapters.claude_code import ClaudeCodeAdapter
from hq.config import SourceCfg
from hq.store import Store

FIXTURE = Path(__file__).parent / 'fixtures' / 'claude'

class ClaudeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'hq.db')
        self.addCleanup(self.store.close)
        self.adapter = ClaudeCodeAdapter()
        self.cfg = SourceCfg(True, [FIXTURE])
        self.batch = self.adapter.poll(self.store, self.cfg)

    def test_session_fields(self):
        s = next(s for s in self.batch.sessions if s.id == 'claude_code:session')
        self.assertEqual((s.harness, s.provider, s.model, s.title, s.project), ('claude_code', 'anthropic', 'claude-opus-5-5', 'Example title', '/project'))

    def test_tool_call_and_error_events(self):
        self.assertTrue(any(e.kind == 'tool_call' and e.tool == 'Read' and e.ext_id == 'a1#1' for e in self.batch.events))
        self.assertTrue(any(e.kind == 'error' for e in self.batch.events))

    def test_tokens_mapped(self):
        e = next(e for e in self.batch.events if e.ext_id == 'a1')
        self.assertEqual((e.tokens_in, e.tokens_out, e.tokens_cache_read, e.tokens_cache_write), (2, 389, 183880, 3992))

    def test_turn_end_emitted(self):
        self.assertTrue(any(e.kind == 'turn_end' for e in self.batch.events))

    def test_subagent_parent_link(self):
        s = next(s for s in self.batch.sessions if s.id == 'claude_code:session:agent')
        self.assertEqual(s.parent_id, 'claude_code:session')

    def test_incremental_second_poll_empty(self):
        self.store.apply(self.batch, lambda *a: 'unassigned')
        batch = self.adapter.poll(self.store, self.cfg)
        self.assertEqual((batch.sessions, batch.events, batch.limits, batch.warnings), ([], [], [], 0))

    def test_garbage_line_counted_as_warning(self):
        path = Path(self.tmp.name) / 'bad.jsonl'
        path.write_text('not json\n', encoding='utf-8')
        self.assertEqual(self.adapter.poll(self.store, SourceCfg(True, [path])).warnings, 1)

    def test_tool_result_nested_content_preview_is_text(self):
        import json
        root = Path(self.tmp.name)
        row = {'type': 'user', 'sessionId': 'nested', 'timestamp': '2026-10-30T10:00:00Z',
               'uuid': 'result', 'message': {'content': [{'type': 'tool_result', 'content': [
                   {'type': 'text', 'text': 'first'}, {'type': 'text', 'content': [{'text': 'second'}]}]}]}}
        (root / 'nested.jsonl').write_text(json.dumps(row) + '\n', encoding='utf-8')
        batch = self.adapter.poll(self.store, SourceCfg(True, [root]))
        self.assertEqual(next(e.preview for e in batch.events if e.kind == 'tool_result'), 'first second')


class ClaudeSplitMessageTests(unittest.TestCase):
    def test_split_message_tokens_counted_once(self):
        import json
        from hq.config import SourceCfg
        from hq.adapters.claude_code import ClaudeCodeAdapter
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / 'proj'; root.mkdir()
        usage = {"input_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 100, "output_tokens": 50}
        base = {"type": "assistant", "sessionId": "s1", "timestamp": "2026-10-30T10:00:00Z", "cwd": "/p", "isSidechain": False}
        lines = [dict(base, uuid="u1", message={"id": "msg_1", "model": "claude-x", "content": [{"type": "text", "text": "hi"}], "usage": usage}),
                 dict(base, uuid="u2", message={"id": "msg_1", "model": "claude-x", "content": [{"type": "tool_use", "name": "Bash"}], "usage": usage, "stop_reason": "tool_use"})]
        (root / 's1.jsonl').write_text(''.join(json.dumps(l) + chr(10) for l in lines), encoding='utf-8')
        store = Store(Path(tmp.name) / 'hq.db'); self.addCleanup(store.close)
        batch = ClaudeCodeAdapter().poll(store, SourceCfg(True, [Path(tmp.name)]))
        store.apply(batch, lambda *a: 'claude')
        with closing(store.conn()) as c:
            self.assertEqual(c.execute('select sum(tokens_in), sum(tokens_out), sum(tokens_cache_read) from events').fetchone(), (10, 50, 100))
            self.assertEqual(c.execute("select count(*) from events where kind='tool_call'").fetchone()[0], 1)
