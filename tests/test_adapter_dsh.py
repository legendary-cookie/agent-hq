import json
import tempfile
import unittest
from pathlib import Path
from hq.adapters.dsh import DshAdapter
from hq.config import SourceCfg
from hq.store import Store
from hq import zstd_frames

ROWS = [
    {'type':'session','id':'abc','createdAt':1772445600000,'cwd':'/project'},
    {'type':'session/title-llm-request','time':1772445601000,'data':{'route':{'provider':'other','model':'deepseek-chat'}}},
    {'type':'assistant/message','seq':2,'time':1772445602000,'data':{'usage':{'inputTokens':10,'outputTokens':5,'reasoningTokens':3,'cacheReadTokens':4,'cacheWriteTokens':2},'toolCalls':[{'name':'bash'}]}},
]
def lines(rows): return ''.join(json.dumps(row) + '\n' for row in rows).encode()

class DshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.store = Store(self.root / 'test.db'); self.addCleanup(self.store.close)
        self.cfg = SourceCfg(True, [self.root / 'sessions']); self.cfg.paths[0].mkdir()
        self.adapter = DshAdapter()
    def plain(self):
        path = self.cfg.paths[0] / 'session.jsonl'; path.write_bytes(lines(ROWS)); return path
    def test_usage_mapping(self):
        self.plain(); batch = self.adapter.poll(self.store, self.cfg)
        event = next(e for e in batch.events if e.kind == 'assistant')
        self.assertEqual((event.tokens_in,event.tokens_out,event.tokens_cache_read,event.tokens_cache_write),(10,8,4,2))
        self.assertEqual(next(e.tool for e in batch.events if e.kind == 'tool_call'), 'bash')
    def test_route_sets_provider_model(self):
        self.plain(); session = self.adapter.poll(self.store, self.cfg).sessions[0]
        self.assertEqual((session.id,session.provider,session.model),('dsh:abc','deepseek','deepseek-chat'))
    @unittest.skipUnless(zstd_frames.available, 'compression.zstd unavailable')
    def test_plain_and_zstd_logs_equal(self):
        from compression import zstd
        path = self.plain(); plain = self.adapter.poll(self.store,self.cfg)
        path.unlink(); (self.cfg.paths[0] / 'session.jsonl.zstd').write_bytes(zstd.compress(lines(ROWS)))
        compressed = self.adapter.poll(self.store,self.cfg)
        self.assertEqual(plain.events, compressed.events)
    @unittest.skipUnless(zstd_frames.available, 'compression.zstd unavailable')
    def test_torn_tail_read_on_next_pass(self):
        from compression import zstd
        path = self.cfg.paths[0] / 'session.jsonl.zstd'
        frame = zstd.compress(lines([{**ROWS[2], 'seq':3}]))
        path.write_bytes(zstd.compress(lines(ROWS)) + frame[:len(frame)//2])
        batch = self.adapter.poll(self.store,self.cfg); self.store.apply(batch,lambda *args:'unassigned')
        with path.open('ab') as stream: stream.write(frame[len(frame)//2:])
        batch = self.adapter.poll(self.store,self.cfg); self.store.apply(batch,lambda *args:'unassigned')
        conn = self.store.conn(); self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT count(*) FROM events WHERE kind='assistant'").fetchone()[0], 2)
