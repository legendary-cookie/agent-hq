import http.client, json, tempfile, threading, unittest
from pathlib import Path
from hq.config import Config, Account, save_config
from hq.store import Store
from hq.collector import Collector
from hq.server import make_server

class ServerTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory(); self.addCleanup(t.cleanup); self.root=Path(t.name); self.store=Store(self.root/'db'); self.addCleanup(self.store.close)
        self.cfg=Config({},[Account('a','p','A','',20,1,[],{})],{}, {'thresholds':[80,95],'toast':False},'isometric','coding'); save_config(self.root,self.cfg)
        self.col=Collector(self.store,self.cfg,[]); self.server,self.token=make_server(self.store,self.col,self.cfg,self.root,port=0,static_dir=None); self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start(); self.addCleanup(self.cleanup_server)
        self.port=self.server.server_port
    def cleanup_server(self): self.server.shutdown(); self.server.server_close(); self.thread.join()
    def req(self,path,method='GET',data=None,headers=None):
        if headers is None and method == 'GET': headers={'X-HQ-Token':self.token}
        c=http.client.HTTPConnection('127.0.0.1',self.port,timeout=3); self.addCleanup(c.close); c.request(method,path,json.dumps(data) if data is not None else None,headers or {}); r=c.getresponse(); return r.status,json.loads(r.read())
    def test_overview_shape(self): self.assertIn('status_counts',self.req('/api/overview')[1])
    def test_sessions_filter_by_status(self): self.assertEqual(self.req('/api/sessions?status=working')[1],[])
    def test_session_detail_includes_children_and_value(self):
        with self.store._lock, self.store._writer as c: c.execute("insert into sessions(id,harness,provider,started_at,last_event_at) values ('s','h','p',1,1)")
        data=self.req('/api/sessions/s')[1]; self.assertIn('children',data); self.assertIn('value',data)
    def test_kpis_week(self): self.assertIn('previous',self.req('/api/kpis?range=week')[1])
    def test_limits_cards(self): self.assertEqual(self.req('/api/limits')[1][0]['account_id'],'a')
    def test_health(self): self.assertIn('recommend',self.req('/api/health')[1])
    def test_bad_host_rejected(self): self.assertEqual(self.req('/api/health',headers={'Host':'evil.com'})[0],403)
    def test_post_without_token_rejected(self):
        self.assertEqual(self.req('/api/limits/manual','POST',{})[0],403); self.assertEqual(self.req('/api/limits/manual','POST',{}, {'X-HQ-Token':self.token,'Origin':'http://evil.com'})[0],403)
    def test_manual_limit_post_ok(self):
        h={'X-HQ-Token':self.token,'Origin':f'http://127.0.0.1:{self.port}'}; self.assertEqual(self.req('/api/limits/manual','POST',{'account_id':'a','window':'5h','used_percent':80},h)[0],200)
    def test_settings_put_rejects_forbidden_path(self):
        data=self.req('/api/settings')[1]; data['sources']['x']={'enabled':True,'paths':['/tmp/secrets/foo']}; h={'X-HQ-Token':self.token,'Origin':f'http://127.0.0.1:{self.port}'}; self.assertEqual(self.req('/api/settings','PUT',data,h)[0],400)
    def test_unknown_route_404_json(self): self.assertIn('error',self.req('/bad')[1])
    def test_sse_receives_changed(self):
        c=http.client.HTTPConnection('127.0.0.1',self.port,timeout=3); self.addCleanup(c.close); c.request('GET','/api/stream?token='+self.token); r=c.getresponse(); self.col._callbacks[0](); self.assertEqual(r.readline(),b'event: changed\n'); self.assertTrue(r.readline().startswith(b'data: '))
