import http.client
from contextlib import closing
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from hq.config import Config
from hq.store import Store
from hq.collector import Collector
from hq.server import make_server
from hq.kpis import resolve_range, compute_kpis

class UiApiTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name)/'db'); self.addCleanup(self.store.close)
        self.conn = self.store.conn(); self.addCleanup(self.conn.close)
        self.cfg = Config({}, [], {}, {}, 'isometric', 'coding')
        self.now = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
        self.rng = resolve_range('custom', self.now, (self.now-timedelta(days=2)).timestamp(), (self.now+timedelta(days=1)).timestamp())
        for provider in ('a', 'b'):
            self.conn.execute('insert into sessions(id,harness,provider,started_at,last_event_at) values (?,?,?,?,?)', (provider,'h',provider,self.now.timestamp(),self.now.timestamp()))
            for i in range(2):
                ts=(self.now-timedelta(days=i)).timestamp()
                self.conn.execute('insert into events(session_id,ext_id,ts,kind,tool,preview,tokens_in,tokens_out) values (?,?,?,?,?,?,?,?)',(provider,str(i),ts,'tool_call','shell','hello',100,20))
                self.conn.execute('insert into tasks(session_id,started_at,outcome) values (?,?,?)',(provider,ts,'ok'))
        self.conn.commit()
    def start_server(self):
        server, token = make_server(self.store, Collector(self.store,self.cfg,[]), self.cfg, '.', port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def close(): server.shutdown(); server.server_close(); thread.join()
        self.addCleanup(close)
        return server.server_port, token
    def test_series_by_provider_per_day(self):
        from hq.kpis import series
        rows=series(self.conn,self.cfg,self.rng,'day','provider')
        self.assertEqual(len(rows),4); self.assertTrue(all(r['tokens']==120 and r['tasks']==1 for r in rows))
    def test_series_value_none_when_unpriced(self):
        from hq.kpis import series
        self.assertTrue(all(r['value_usd'] is None for r in series(self.conn,self.cfg,self.rng,'day','provider')))
    def test_series_errors_counted(self):
        from hq.kpis import series
        self.conn.execute("insert into events(session_id,ext_id,ts,kind) values ('a','err',?,'error')",(self.now.timestamp(),)); self.conn.commit()
        self.assertEqual(sum(r['errors'] for r in series(self.conn,self.cfg,self.rng,'day','provider')),1)
    def test_sessions_rows_have_last_tool_and_tokens(self):
        import json
        port,token=self.start_server()
        with closing(http.client.HTTPConnection('127.0.0.1',port)) as c:
            c.request('GET','/api/sessions',headers={'X-HQ-Token':token}); rows=json.loads(c.getresponse().read())
        self.assertEqual(rows[0]['last_tool'],'shell'); self.assertEqual(rows[0]['tokens'],240); self.assertEqual(rows[0]['last_preview'],'hello'); self.assertEqual(rows[0]['last_kind'],'tool_call')
    def test_top_sessions_listed_without_prices(self):
        rows=compute_kpis(self.conn,self.cfg,self.rng,None,self.now.timestamp())['top_sessions']
        self.assertEqual(len(rows),2); self.assertEqual(rows[0]['tokens'],240); self.assertIsNone(rows[0]['value_usd'])
    def test_series_requires_token(self):
        port,_=self.start_server()
        with closing(http.client.HTTPConnection('127.0.0.1',port)) as c:
            c.request('GET','/api/series'); self.assertEqual(c.getresponse().status,403)

    def test_session_events_desc_returns_latest_first(self):
        import json
        port, token = self.start_server()
        with closing(http.client.HTTPConnection('127.0.0.1', port)) as c:
            c.request('GET', '/api/sessions/a?limit=1&order=desc', headers={'X-HQ-Token': token})
            events = json.loads(c.getresponse().read())['events']
            self.assertEqual(events[0]['ext_id'], '0')
            c.request('GET', '/api/sessions/a?order=invalid', headers={'X-HQ-Token': token})
            self.assertEqual(c.getresponse().status, 400)
