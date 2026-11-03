import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from hq.__main__ import build_adapters
from hq.adapters.codex import CodexAdapter
from hq.config import SourceCfg, load_config
from hq.remote.collector import RemoteCollector
from hq.remote.config import Host
from hq.store import Store

ROOT = Path(__file__).resolve().parent.parent


class RemoteCollectorTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home = self.root / 'home'
        shutil.copytree(ROOT / 'tests/fixtures/codex', self.home / '.codex/sessions')
        shutil.copytree(ROOT / 'tests/fixtures/claude', self.home / '.claude/projects')
        self.store = Store(self.root / 'hq.db')
        self.addCleanup(self.store.close)
        self.cfg = load_config(ROOT / 'config')
        self.host = Host('box', 'box', 'linux', None, 30, ('codex', 'claude_code'))
        self.col = RemoteCollector(self.store, self.cfg, [self.host], self.root, build_adapters, run_scan=self.scan)

    def scan(self, host, request, scanner_text):
        env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home))
        code = '_HQ_REQUEST = None\n' + scanner_text + '\nmain(' + repr(json.dumps(request)) + ')\n'
        result = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, check=True)
        return json.loads(result.stdout)

    def rows(self, sql):
        with closing(self.store.conn()) as conn:
            return conn.execute(sql).fetchall()

    def test_remote_pass_stores_namespaced_sessions(self):
        self.col.run_once()
        rows = self.rows('select id, host, parent_id from sessions')
        self.assertIn(('box/codex:x', 'box', None), rows)
        self.assertIn(('box/claude_code:session:agent', 'box', 'box/claude_code:session'), rows)

    def test_remote_numbers_equal_local_reader(self):
        local = CodexAdapter().poll(self.store, SourceCfg(True, [ROOT / 'tests/fixtures/codex']))
        self.col.run_once()
        expected = sum(e.tokens_in or 0 for e in local.events)
        self.assertEqual(self.rows("select sum(tokens_in) from events where session_id='box/codex:x'")[0][0], expected)

    def test_remote_ids_never_collide_with_local(self):
        batch = CodexAdapter().poll(self.store, SourceCfg(True, [ROOT / 'tests/fixtures/codex']))
        self.store.apply(batch, lambda *args: 'local')
        self.col.run_once()
        self.assertEqual(set(self.rows("select id, host from sessions where id like '%codex:x'")), {('codex:x', None), ('box/codex:x', 'box')})

    def test_hung_host_does_not_block_others(self):
        entered, release = threading.Event(), threading.Event()
        def scan(host, request, scanner_text):
            if host.name == 'hung':
                entered.set(); release.wait(3)
            return self.scan(host, request, scanner_text)
        hung = Host('hung', 'hung', 'linux', None, 30, ('codex',))
        col = RemoteCollector(self.store, self.cfg, [hung, self.host], self.root, build_adapters, run_scan=scan)
        self.addCleanup(release.set)
        col.start(); self.addCleanup(col.stop)
        self.assertTrue(entered.wait(2))
        for _ in range(100):
            if col.health()['box']['state'] == 'ok': break
            threading.Event().wait(.02)
        self.assertEqual(col.health()['box']['state'], 'ok')
        release.set()

    def test_reset_restarts_reader_cursor(self):
        self.col.run_once()
        path = next((self.home / '.codex/sessions').rglob('*.jsonl'))
        data = path.read_bytes()
        before = path.stat().st_mtime
        path.write_bytes(data)
        os.utime(path, (before - 100, before - 100))
        from unittest.mock import patch as mock_patch
        adapter = next(a for a in self.col._adapters['box'] if a.name == 'codex')
        original = adapter.poll
        seen = []
        def poll(store, source):
            mirror_path = next(source.paths[0].rglob('*.jsonl'))
            seen.append(store.get_cursor('codex', str(mirror_path)))
            return original(store, source)
        with mock_patch.object(adapter, 'poll', side_effect=poll):
            self.col.run_once()
        self.assertTrue(not seen[0] or seen[0][0] == 0)
        mirror = next((self.root / 'remote/box/.codex/sessions').rglob('*.jsonl'))
        self.assertEqual(self.store.get_cursor('codex', str(mirror))[0], len(data))
        self.assertEqual(self.col.health()['box']['state'], 'ok')

    def test_backoff_and_recovery(self):
        def fail(*args): raise RuntimeError('offline')
        self.col.run_scan = fail
        self.col.run_once()
        self.assertEqual(self.col._backoff['box'], 60)
        self.col.run_once()
        self.assertEqual(self.col._backoff['box'], 120)
        self.col.run_scan = self.scan
        self.col.run_once()
        self.assertEqual(self.col._backoff['box'], 30)

    def test_health_reports_errors(self):
        def fail(*args): raise RuntimeError('offline')
        self.col.run_scan = fail
        self.col.run_once()
        self.assertEqual(self.col.health()['box']['error'], 'offline')
        self.assertEqual(self.col.health()['box']['state'], 'partial')

    def test_scanner_note_does_not_stop_the_host(self):
        def scan_with_note(host, request, scanner_text):
            answer = self.scan(host, request, scanner_text)
            answer['errors'] = ['.local/share/opencode/opencode.db: database larger than 40 MB, not mirrored']
            return answer
        self.col.run_scan = scan_with_note
        self.col.run_once()
        self.assertEqual(self.col.health()['box']['state'], 'ok')
        self.assertTrue(self.col.health()['box']['notes'])
        self.assertTrue(self.rows("select count(*) from sessions where host='box'")[0][0])

    def test_one_bad_entry_does_not_stop_the_good_ones(self):
        def scan_with_bad(host, request, scanner_text):
            answer = self.scan(host, request, scanner_text)
            answer['files'].append({'path': '../../evil', 'kind': 'append', 'offset': 0, 'data_b64': '', 'size': 0, 'mtime': 1.0})
            return answer
        self.col.run_scan = scan_with_bad
        self.col.run_once()
        self.assertEqual(self.col.health()['box']['state'], 'partial')
        self.assertTrue(self.rows("select count(*) from sessions where host='box'")[0][0])

    def test_once_includes_remote_pass(self):
        from hq.__main__ import main
        config = self.root / 'config'
        shutil.copytree(ROOT / 'config', config)
        (config / 'hosts.json').write_text(json.dumps({'hosts': [{'name': 'box', 'ssh': 'box', 'os': 'linux', 'sources': ['codex']}]}), encoding='utf-8')
        with patch('hq.remote.collector.ssh.run_scan', self.scan), patch('hq.collector.Collector.run_once'):
            main(['--once', '--config', str(config), '--db', str(self.root / 'once.db')])
        with closing(__import__('sqlite3').connect(self.root / 'once.db')) as conn:
            self.assertIsNotNone(conn.execute("select id from sessions where id='box/codex:x'").fetchone())

class RemoteHttpTests(unittest.TestCase):
    def test_health_and_host_filter(self):
        import http.client
        from hq.collector import Collector
        from hq.server import make_server
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        store = Store(root / 'db'); self.addCleanup(store.close)
        cfg = load_config(ROOT / 'config')
        local = Collector(store, cfg, [])
        host = Host('box', 'box', 'linux', None, 30, ('codex',))
        remote = RemoteCollector(store, cfg, [host], root, build_adapters)
        batch = CodexAdapter().poll(store, SourceCfg(True, [ROOT / 'tests/fixtures/codex']))
        store.apply(batch, lambda *args: 'local')
        from hq.remote.namespace import prefix_batch
        store.apply(prefix_batch(batch, 'box'), lambda *args: 'remote', host='box')
        server, token = make_server(store, local, cfg, root, port=0, remote_collector=remote)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def cleanup():
            server.shutdown(); server.server_close(); thread.join()
        self.addCleanup(cleanup)
        def get(path):
            conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            self.addCleanup(conn.close)
            conn.request('GET', path, headers={'X-HQ-Token': token})
            return json.loads(conn.getresponse().read())
        self.assertIn('box', get('/api/health')['hosts'])
        self.assertEqual([(s['id'], s['host']) for s in get('/api/sessions?host=box')], [('box/codex:x', 'box')])
        self.assertEqual([(s['id'], s['host']) for s in get('/api/sessions?host=local')], [('codex:x', None)])
