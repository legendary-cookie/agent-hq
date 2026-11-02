import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from hq.adapters.deepseek_balance import DeepseekBalanceAdapter
from hq.collector import Collector
from hq.config import Config, SourceCfg
from hq.limits import alerts, limit_cards
from hq.store import Store


class FakeHandler(BaseHTTPRequestHandler):
    calls = 0
    status = 200
    def log_message(self, *args): pass
    def do_GET(self):
        type(self).calls += 1
        self.send_response(type(self).status); self.end_headers()
        self.wfile.write(json.dumps({'balance_infos': [{'currency': 'USD', 'total_balance': '3', 'granted_balance': '1', 'topped_up_balance': '2'}]}).encode())


class BalanceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name) / 'db'); self.addCleanup(self.store.close)
        self.conn = self.store.conn(); self.addCleanup(self.conn.close)
        self.cfg = Config({'deepseek_balance': SourceCfg(True, [])}, [], {}, {}, '', '')
        FakeHandler.calls = 0; FakeHandler.status = 200
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), FakeHandler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True); thread.start()
        self.addCleanup(thread.join); self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)
        self.adapter = DeepseekBalanceAdapter(url=f'http://127.0.0.1:{self.server.server_port}/user/balance')
        self.collector = Collector(self.store, self.cfg, [self.adapter])
        env = patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-secret'}); env.start(); self.addCleanup(env.stop)

    def test_balance_stored_and_card_shown(self):
        self.collector.run_once()
        cards = limit_cards(self.conn, self.cfg, 9999999999)
        self.assertEqual(cards[0]['balance']['total'], 3)
        self.assertEqual(cards[0]['account_id'], 'deepseek-api')

    def test_no_key_no_request(self):
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': ''}): self.collector.run_once()
        self.assertEqual(FakeHandler.calls, 0)

    def test_rate_limited_to_15_min(self):
        self.collector.run_once(); self.collector.run_once()
        self.assertEqual(FakeHandler.calls, 1)

    def test_low_balance_alert(self):
        self.collector.run_once()
        cards = limit_cards(self.conn, self.cfg, 9999999999)
        self.assertEqual(alerts(cards, [], self.cfg.deepseek_alert)[0]['level'], 'low_balance')

    def test_balance_http_error_marks_partial_without_key_in_message(self):
        FakeHandler.status = 401; self.collector.run_once()
        health = self.collector.health()['deepseek_balance']
        self.assertEqual(health['state'], 'partial')
        self.assertNotIn('test-secret', health['error'])
        self.collector.run_once()
        self.assertEqual(self.collector.health()['deepseek_balance']['state'], 'partial')
        self.assertEqual(FakeHandler.calls, 1)
