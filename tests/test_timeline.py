import http.client
import json
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path

from hq.collector import Collector
from hq.config import Config
from hq.server import make_server, timeline_events
from hq.store import Store


class TimelineUnitTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name).resolve() / 'db')
        self.addCleanup(self.store.close)
        with self.store._lock, self.store._writer as conn:
            conn.execute("insert into sessions(id,harness) values ('s','codex')")

    def insert(self, timestamps):
        with self.store._lock, self.store._writer as conn:
            conn.executemany("insert into events(session_id,ts,kind,tool,preview) values ('s',?,'tool_call','edit','secret')", [(t,) for t in timestamps])

    def query(self, start, end):
        with closing(self.store.conn()) as conn:
            return timeline_events(conn, start, end)

    def test_timeline_default_24h(self):
        now = time.time()
        self.insert([now - 86401, now - 10])
        self.assertEqual(len(self.query(None, None)['events']), 1)

    def test_timeline_cap_and_truncated(self):
        self.insert(range(20001))
        result = self.query(0, 30000)
        self.assertEqual(len(result['events']), 20000)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['events'][0]['ts'], 0)

    def test_timeline_no_previews(self):
        self.insert([1])
        self.assertEqual(set(self.query(0, 2)['events'][0]), {'session_id', 'ts', 'kind', 'tool', 'harness'})

    def test_timeline_range_limit(self):
        for start, end in [(0, 604801), (2, 1), ('nan', 2)]:
            with self.assertRaises(ValueError):
                self.query(start, end)


class TimelineHttpTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        store = Store(root / 'db')
        self.addCleanup(store.close)
        cfg = Config({}, [], {}, {}, 'isometric', 'coding')
        server, self.token = make_server(store, Collector(store, cfg, []), cfg, root, port=0)
        self.server = server
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join()))

    def request(self, path, headers):
        with closing(http.client.HTTPConnection('127.0.0.1', self.server.server_port)) as conn:
            conn.request('GET', path, headers=headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read())

    def test_timeline_requires_token(self):
        self.assertEqual(self.request('/api/timeline', {})[0], 403)
        self.assertEqual(self.request('/api/timeline', {'X-HQ-Token': self.token})[0], 200)
        self.assertEqual(self.request('/api/timeline?from=0&to=604801', {'X-HQ-Token': self.token})[0], 400)
