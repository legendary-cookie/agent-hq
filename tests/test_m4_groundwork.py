import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path

from hq import zstd_frames
from hq.collector import Collector
from hq.config import Config, save_config
from hq.providers import normalize_provider
from hq.records import Batch, SessionRec
from hq.server import make_server
from hq.store import Store


@unittest.skipUnless(zstd_frames.available, 'compression.zstd unavailable')
class FrameTests(unittest.TestCase):
    def test_iter_frames_concatenated(self):
        from compression import zstd
        data = zstd.compress(b'a\n') + zstd.compress(b'b\n')
        self.assertEqual(b''.join(zstd_frames.iter_frames(data)), b'a\nb\n')

    def test_torn_tail_frame_skipped(self):
        from compression import zstd
        tail = zstd.compress(b'c\n')
        data = zstd.compress(b'a\n') + zstd.compress(b'b\n') + tail[:len(tail) // 2]
        self.assertEqual(b''.join(zstd_frames.iter_frames(data)), b'a\nb\n')

    def test_broken_middle_frame_raises(self):
        from compression import zstd
        data = zstd.compress(b'a') + b'\x28\xb5\x2f\xfd\xff' + zstd.compress(b'b')
        with self.assertRaises(ValueError):
            list(zstd_frames.iter_frames(data))


class GroundworkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_normalize_provider(self):
        self.assertEqual(normalize_provider('opencode', 'deepseek-v4.1-flash-free'), 'deepseek')
        self.assertEqual(normalize_provider(None, 'kimi-k2'), 'moonshot')
        self.assertEqual(normalize_provider('openai', 'gpt-6'), 'openai')

    def test_relabel_under_zylos(self):
        store = Store(self.root / 'db')
        self.addCleanup(store.close)
        session = SessionRec('s', 'claude_code', 'opencode', 'deepseek-v4', '~/zylos/workspace', None, 0, 1, None, None, 'source')
        store.apply(Batch([session], [], [], []), lambda *args: 'a', [(Path('~/zylos').expanduser(), 'zylos')])
        with closing(store.conn()) as conn:
            self.assertEqual(conn.execute('select harness, provider from sessions').fetchone(), ('zylos', 'deepseek'))

    def test_imported_column_and_balances_table_exist_after_migration(self):
        path = self.root / 'db'
        with closing(sqlite3.connect(path)) as conn:
            conn.execute('create table sessions (id TEXT PRIMARY KEY, harness TEXT, provider TEXT, model TEXT, account_id TEXT, project TEXT, title TEXT, started_at REAL, last_event_at REAL, ended_at REAL, status TEXT, parent_id TEXT, source_ref TEXT)')
            conn.commit()
        store = Store(path)
        self.addCleanup(store.close)
        with closing(store.conn()) as conn:
            self.assertIn('imported', [row[1] for row in conn.execute('pragma table_info(sessions)')])
            self.assertEqual([row[1] for row in conn.execute('pragma table_info(balances)')], ['account_id', 'currency', 'total', 'granted', 'topped_up', 'recorded_at'])

    def test_health_reports_tested(self):
        class Adapter:
            name = 'example'
            tested = 'upstream'
        store = Store(self.root / 'db')
        self.addCleanup(store.close)
        cfg = Config({}, [], {}, {}, 'isometric', 'coding')
        collector = Collector(store, cfg, [Adapter()])
        self.assertEqual(collector.health()['example']['tested'], 'upstream')
        self.assertEqual(collector.health()['importer']['state'], 'disabled')
        collector.run_once()
        self.assertEqual(collector.health()['example']['tested'], 'upstream')

    def test_settings_zylos_previews_validated(self):
        store = Store(self.root / 'db')
        self.addCleanup(store.close)
        cfg = Config({}, [], {}, {'thresholds': [80, 95], 'toast': False}, 'isometric', 'coding')
        save_config(self.root, cfg)
        collector = Collector(store, cfg, [])
        server, token = make_server(store, collector, cfg, self.root, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def cleanup():
            server.shutdown()
            server.server_close()
            thread.join()
        self.addCleanup(cleanup)
        headers = {'X-HQ-Token': token, 'Origin': f'http://127.0.0.1:{server.server_port}'}
        def request(method, data=None):
            with closing(http.client.HTTPConnection('127.0.0.1', server.server_port)) as conn:
                conn.request(method, '/api/settings', json.dumps(data) if data is not None else None, headers)
                response = conn.getresponse()
                return response.status, json.loads(response.read())
        data = request('GET')[1]
        data['zylos_previews'] = 'yes'
        self.assertEqual(request('PUT', data)[0], 400)
        data['zylos_previews'] = True
        self.assertEqual(request('PUT', data)[0], 200)
        self.assertTrue(request('GET')[1]['zylos_previews'])


@unittest.skipUnless(zstd_frames.available, 'compression.zstd needs Python 3.14+')
class FrameBoundaryTests(unittest.TestCase):
    def test_magic_bytes_inside_payload_do_not_split_frames(self):
        from compression import zstd
        import os
        # Incompressible data is stored raw, so the magic bytes stay literal inside the frame.
        payload = os.urandom(4000) + b'\x28\xb5\x2f\xfd' + os.urandom(4000)
        second = b'next frame\n'
        data = zstd.compress(payload) + zstd.compress(second)
        self.assertEqual(list(zstd_frames.iter_frames(data)), [payload, second])
