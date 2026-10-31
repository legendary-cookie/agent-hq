import tempfile, unittest, sqlite3
from pathlib import Path
from hq.store import Store
from hq.config import Config, Account
from hq.limits import limit_cards, forecast_seconds, alerts, add_manual, recommend_bridge

class LimitTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory(); self.addCleanup(t.cleanup); self.root=Path(t.name); self.store=Store(self.root/'db'); self.addCleanup(self.store.close); self.c=self.store.conn(); self.addCleanup(self.c.close)
        self.cfg=Config({},[Account('a','p','A','',None,1,[],{})],{}, {},'',''); self.now=100000
    def add(self, pct, ts, window='5h', source='measured', reset=None):
        self.c.execute('insert into limit_snapshots(account_id,scope,window,used_percent,resets_at,source,recorded_at) values (?,?,?,?,?,?,?)',('a','*',window,pct,reset,source,ts)); self.c.commit()
    def cards(self): return limit_cards(self.c,self.cfg,self.now)
    def test_latest_snapshot_per_window(self):
        self.add(20,self.now-10); self.add(30,self.now); self.assertEqual(self.cards()[0]['windows'][0]['used_percent'],30)
    def test_stale_when_older_than_window(self): self.add(50,self.now-18001); self.assertTrue(self.cards()[0]['windows'][0]['stale'])
    def test_forecast_linear(self):
        self.assertAlmostEqual(forecast_seconds([(self.now-1800,50),(self.now,80)],self.now),1200); self.assertIsNone(forecast_seconds([(1,1)],self.now)); self.assertIsNone(forecast_seconds([(1,1),(2,1)],self.now))
    def test_blocked_counts_as_100_until_reset(self): self.add(25,self.now,'block','blocked',self.now+10); self.assertEqual(self.cards()[0]['windows'][0]['used_percent'],100)
    def test_unknown_when_no_data(self): self.assertEqual(self.cards()[0]['windows'][0]['source'],'unknown')
    def test_alert_levels(self):
        for n,expected in [(79,None),(80,'warn'),(95,'critical')]:
            card=[{'account_id':'a','windows':[{'used_percent':n,'source':'manual'}]}]; a=alerts(card,[80,95]); self.assertEqual(a[0]['level'] if a else None,expected)
        self.assertEqual(alerts([{'account_id':'a','windows':[{'used_percent':100,'source':'blocked'}]}],[80,95])[0]['level'],'blocked')
    def test_manual_entry_validated(self):
        with self.assertRaises(ValueError): add_manual(self.store,'a','5h',101,None,self.now)
        add_manual(self.store,'a','5h',80,None,self.now); self.assertEqual(self.cards()[0]['windows'][0]['source'],'manual')
    def test_recommend_bridge_import_failure_returns_none(self): self.assertIsNone(recommend_bridge(self.root/'missing','coding'))
    def test_recommend_bridge_explicit_paths(self):
        (self.root/'tracker.py').write_text('def recommend(task_kind, needs_execution=False, min_tier="cheap", prefer_free=False, now=None, db_path=None, providers_path=None):\n return [{"task":task_kind,"db":str(db_path),"providers":str(providers_path)}]\n')
        sqlite3.connect(self.root/'usage.db').close()
        result=recommend_bridge(self.root,'coding'); self.assertNotEqual(result[0]['db'],str(self.root/'usage.db')); self.assertEqual(result[0]['providers'],str(self.root/'providers.json'))
