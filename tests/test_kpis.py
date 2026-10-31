import tempfile, unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from hq.config import Config, Account, Price
from hq.store import Store
from hq.kpis import resolve_range, value_usd, compute_kpis, session_value

class KpiTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory(); self.addCleanup(t.cleanup); self.store=Store(Path(t.name)/'db'); self.addCleanup(self.store.close)
        self.cfg=Config({},[Account('a','p','A','',20,1,[],{}),Account('b','p','B','',None,1,[],{})],{'m':Price(3,15,.3,3.75)}, {},'','')
        self.now=datetime(2026,10,30,12).astimezone(); self.rng=resolve_range('month',self.now)
        self.c=self.store.conn(); self.addCleanup(self.c.close)
    def seed(self):
        ts=self.now.timestamp()-2000
        self.c.execute("insert into sessions(id,harness,provider,model,account_id,project,started_at,last_event_at) values ('s','h','p','m','a','proj',?,?)",(ts,ts+2000))
        self.c.executemany("insert into events(session_id,ext_id,ts,kind,tokens_in,tokens_cache_read,model) values ('s',?,?,?, ?,?,?)",[(str(i),ts+d,'tool_call',100 if i==0 else 0,300 if i==0 else 0,'m') for i,d in enumerate([0,60,120,2000])])
        self.c.executemany("insert into tasks(session_id,started_at,ended_at,outcome) values ('s',?,?,?)",[(ts,ts+1,x) for x in ['ok','ok','ok','error']]); self.c.commit()
    def test_resolve_week_starts_monday(self):
        r=resolve_range('week',datetime(2026,10,29,12).astimezone()); self.assertEqual(datetime.fromtimestamp(r.start).day,26); self.assertEqual(datetime.fromtimestamp(r.prev_start).day,19)
    def test_value_usd_basic(self): self.assertAlmostEqual(value_usd(1000000,100000,2000000,0,self.cfg.prices['m']),5.1)
    def test_success_rate(self): self.seed(); self.assertEqual(compute_kpis(self.c,self.cfg,self.rng,None,self.now.timestamp())['quality']['success_rate'],.75)
    def test_cache_hit_ratio(self): self.seed(); self.assertEqual(compute_kpis(self.c,self.cfg,self.rng,None,self.now.timestamp())['tokens']['cache_hit_ratio'],.75)
    def test_agent_hours_excludes_gaps(self): self.seed(); self.assertAlmostEqual(compute_kpis(self.c,self.cfg,self.rng,None,self.now.timestamp())['activity']['agent_hours'],120/3600)
    def test_value_excludes_unpriced(self):
        self.seed(); self.c.execute("insert into events(session_id,ext_id,ts,kind,tokens_in,model) values ('s','unknown',?,'assistant',100,'unknown')",(self.now.timestamp(),)); self.c.commit(); v=compute_kpis(self.c,self.cfg,self.rng,None,self.now.timestamp())['value']; self.assertEqual(v['unpriced_tokens'],100); self.assertGreater(v['total_usd'],0)
    def test_effective_cost_split_by_tokens(self):
        self.seed(); self.c.execute("insert into sessions(id,account_id,started_at,last_event_at) values ('s2','a',?,?)",(self.now.timestamp(),self.now.timestamp())); self.c.execute("insert into events(session_id,ext_id,ts,kind,tokens_in,model) values ('s2','e',?,'assistant',1200,'m')",(self.now.timestamp(),)); self.c.commit(); self.assertEqual(session_value(self.c,self.cfg,'s')['effective_cost_usd'],5)
    def test_group_by_provider(self): self.seed(); self.assertIn('p',compute_kpis(self.c,self.cfg,self.rng,'provider',self.now.timestamp())['groups'])
    def test_quota_use_average_peak(self):
        for n,p in [(1,60),(2,90)]: self.c.execute("insert into limit_snapshots(account_id,scope,window,used_percent,resets_at,source,recorded_at) values ('a','*','5h',?,?,'measured',?)",(p,n,self.now.timestamp()-n))
        self.c.commit(); self.assertEqual(compute_kpis(self.c,self.cfg,self.rng,None,self.now.timestamp())['quota_use']['a']['5h'],75)
    def test_routing_counts_blocks_and_hours(self):
        now=self.now.timestamp(); self.c.execute("insert into limit_snapshots(account_id,scope,window,used_percent,resets_at,source,recorded_at) values ('a','m','block',100,?,'blocked',?)",(now+3600,now)); self.c.commit(); r=compute_kpis(self.c,self.cfg,self.rng,None,now)['routing']; self.assertEqual(r['blocked_count'],1); self.assertEqual(r['hours_blocked']['m'],1)
    def test_plan_roi(self):
        self.seed(); self.c.execute("insert into events(session_id,ext_id,ts,kind,tokens_in,model) values ('s','roi',?,'assistant',?,'m')",(self.now.timestamp(), 47666666)); self.c.commit(); roi=compute_kpis(self.c,self.cfg,self.rng,None,self.now.timestamp())['plan_roi']; self.assertAlmostEqual(roi['a']['roi'],7.15,places=3); self.assertIsNone(roi['b']['roi'])
