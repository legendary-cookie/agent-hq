import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from hq.config import Config, SourceCfg
from hq.records import Batch, SessionRec, EventRec
from hq.store import Store
from hq.collector import Collector

class CollectorTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(); self.addCleanup(t.cleanup)
        self.store = Store(Path(t.name)/'db'); self.addCleanup(self.store.close)
        self.cfg = Config({'a': SourceCfg(True, []), 'bad': SourceCfg(True, []), 'off': SourceCfg(False, [])}, [], {}, {}, 'isometric', 'coding')
        s = SessionRec('s','a','p',None,None,None,1,2,None,None,'fixture')
        e = [EventRec('s',str(i),i,k,None,None,None,None,None,None,False,None) for i,k in [(1,'user'),(2,'turn_end')]]
        class Adapter:
            name = 'a'
            def poll(self, store, cfg): return Batch([s], e, [], [])
        self.a = Adapter()
    def test_run_once_applies_batches_and_derives_tasks(self):
        self.assertTrue(Collector(self.store,self.cfg,[self.a]).run_once())
        with closing(self.store.conn()) as c: self.assertEqual(c.execute('select outcome from tasks').fetchone(), ('ok',))
    def test_failing_adapter_isolated(self):
        class Bad:
            name = 'bad'
            calls = 0
            def poll(self, store, cfg): self.calls += 1; raise OSError('locked')
        bad = Bad(); col = Collector(self.store,self.cfg,[bad,self.a]); self.assertTrue(col.run_once()); col.run_once()
        self.assertEqual(bad.calls,2); self.assertEqual(col.health()['bad']['error'],'locked'); self.assertEqual(col.health()['bad']['state'],'partial')
    def test_on_change_called_only_when_changed(self):
        col = Collector(self.store,self.cfg,[self.a]); calls = []; col.on_change(lambda: calls.append(1)); col.run_once(); col.run_once(); self.assertEqual(calls,[1])
    def test_disabled_adapter_skipped(self):
        self.a.name = 'off'; col = Collector(self.store,self.cfg,[self.a]); self.assertFalse(col.run_once()); self.assertEqual(col.health()['off']['state'],'disabled')
