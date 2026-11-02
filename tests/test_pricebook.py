import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from hq.config import Config, Price
from hq.kpis import compute_kpis, resolve_range
from hq.pricebook import PriceBook
from hq.store import Store


class PriceBookTests(unittest.TestCase):
    def setUp(self):
        self.user = Price(1, 2, 3, 4)
        self.upstream = Price(5, 6, 7, 8)
        self.book = PriceBook({'m': self.user}, {'m': self.upstream, 'target': self.upstream}, {'alias': 'target'})

    def test_every_alias_target_exists(self):
        import json
        data = json.loads((Path(__file__).resolve().parent.parent / 'config/prices.litellm.json').read_text())
        self.assertTrue(data['aliases'])
        self.assertFalse({v for v in data['aliases'].values() if v not in data['models']})

    def test_provider_prefixed_model_resolves(self):
        self.assertEqual(self.book.get('provider/TARGET'), self.upstream)
        self.assertEqual(self.book.source('provider/ALIAS'), 'litellm')

    def test_synthetic_model_not_listed_for_pricing(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        store = Store(Path(tmp.name) / 'db'); self.addCleanup(store.close)
        conn = store.conn(); self.addCleanup(conn.close)
        now = datetime.now().astimezone(); ts = now.timestamp()
        conn.execute("insert into sessions(id,model,started_at,last_event_at) values ('s','<synthetic>',?,?)", (ts, ts))
        conn.execute("insert into events(session_id,ext_id,ts,kind,tokens_in) values ('s','e',?,'assistant',10)", (ts,)); conn.commit()
        self.book.user['<synthetic>'] = self.user
        self.assertEqual(self.book.source('<synthetic>'), 'unpriced')
        cfg = Config({}, [], {}, {}, '', ''); cfg.pricebook = self.book
        self.assertEqual(compute_kpis(conn, cfg, resolve_range('month', now), None, ts)['top_models'], [])
        import http.client, json, threading
        from hq.collector import Collector
        from hq.server import make_server
        server, token = make_server(store, Collector(store, cfg, []), cfg, Path(tmp.name), port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def cleanup():
            server.shutdown(); server.server_close(); thread.join()
        self.addCleanup(cleanup)
        client = http.client.HTTPConnection('127.0.0.1', server.server_port); self.addCleanup(client.close)
        client.request('GET', '/api/settings', headers={'X-HQ-Token': token})
        response = client.getresponse()
        self.assertEqual(response.status, 200)
        self.assertNotIn('<synthetic>', json.loads(response.read())['price_sources'])

    def test_user_price_wins(self):
        self.assertEqual(self.book.get('m'), self.user)
        self.assertEqual(self.book.source('m'), 'yours')

    def test_litellm_exact(self):
        self.assertEqual(self.book.get('target'), self.upstream)
        self.assertEqual(self.book.source('target'), 'litellm')

    def test_alias_lookup(self):
        self.assertEqual(self.book.get('alias'), self.upstream)
        self.assertEqual(self.book.source('alias'), 'litellm')

    def test_unknown_unpriced(self):
        self.assertIsNone(self.book.get(None))
        self.assertEqual(self.book.source('missing'), 'unpriced')

    def test_kpis_value_uses_litellm_when_no_user_price(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        store = Store(Path(tmp.name) / 'db'); self.addCleanup(store.close)
        conn = store.conn(); self.addCleanup(conn.close)
        now = datetime.now().astimezone(); ts = now.timestamp()
        conn.execute("insert into sessions(id,model,started_at,last_event_at) values ('s','target',?,?)", (ts, ts))
        conn.execute("insert into events(session_id,ext_id,ts,kind,tokens_in) values ('s','e',?,'assistant',1000000)", (ts,)); conn.commit()
        cfg = Config({}, [], {}, {}, '', '')
        cfg.pricebook = self.book
        value = compute_kpis(conn, cfg, resolve_range('month', now), None, ts)['value']
        self.assertEqual(value['total_usd'], 5)
        self.assertEqual(value['unpriced_tokens'], 0)
