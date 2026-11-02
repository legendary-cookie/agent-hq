import json
import os
import tempfile
import time
import subprocess
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from hq.adapters.importer import ImporterAdapter
from hq.collector import Collector
from hq.config import Config, SourceCfg
from hq.status import session_status
from hq.store import Store


class ImporterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'hq.db')
        self.addCleanup(self.store.close)
        self.source = SourceCfg(True, [])
        self.cfg = Config({'importer': self.source}, [], {}, {}, 'isometric', 'coding')
        self.tools = self.root / 'tools'
        self.bin = self.tools / 'node_modules' / '.bin' / ('llm-usage.cmd' if os.name == 'nt' else 'llm-usage')
        self.bin.parent.mkdir(parents=True)
        self.event = {'source': 'goose', 'sessionId': 's1', 'timestamp': '2026-01-02T03:04:05Z', 'provider': 'anthropic', 'model': 'example', 'inputTokens': 10, 'outputTokens': 4, 'reasoningTokens': 2, 'cacheReadTokens': 3, 'cacheWriteTokens': 1, 'totalTokens': 20, 'costMode': 'estimated', 'repoRoot': '/project'}
        self.doctor = {'schemaVersion': 1, 'report': 'doctor', 'data': {'sources': [{'id': 'goose', 'format': 'jsonl', 'status': 'ok', 'state': 'found', 'itemsFound': 1}, {'id': 'codex', 'format': 'jsonl', 'status': 'ok', 'state': 'found', 'itemsFound': 1}]}}
        self.write_fake()
        self.adapter = ImporterAdapter(self.tools)

    def write_fake(self):
        script = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
root = Path(__file__).resolve().parents[2]
(root / 'calls').open('a').write(json.dumps({'args': sys.argv[1:], 'env': dict(os.environ)}) + '\\n')
if sys.argv[1] == 'doctor': print((root / 'doctor.json').read_text())
else: print((root / 'events.jsonl').read_text(), end='')
"""
        executable = self.bin if os.name != 'nt' else self.bin.with_suffix('.py')
        executable.write_text(script)
        executable.chmod(0o755)
        if os.name == 'nt':
            self.bin.write_text('@python "%~dp0llm-usage.py" %*\r\n')
        (self.tools / 'doctor.json').write_text(json.dumps(self.doctor))
        (self.tools / 'events.jsonl').write_text(json.dumps(self.event) + '\n')

    def apply(self, batch):
        self.store.apply(batch, lambda *args: 'unassigned')

    def calls(self):
        return [json.loads(line) for line in (self.tools / 'calls').read_text().splitlines()]

    def test_doctor_found_sources_imported(self):
        batch = self.adapter.poll(self.store, self.source)
        self.assertEqual(batch.sessions[0].id, 'goose:s1')
        self.assertTrue(batch.sessions[0].imported)
        self.assertEqual(batch.events[0].tokens_out, 6)
        self.assertEqual(batch.sessions[0].project, '/project')

    def test_native_sources_excluded(self):
        self.adapter.poll(self.store, self.source)
        self.assertEqual(self.calls()[1]['args'][2], 'goose')

    def test_reimport_is_idempotent(self):
        self.apply(self.adapter.poll(self.store, self.source))
        self.adapter._last_import = 0
        self.apply(self.adapter.poll(self.store, self.source))
        with closing(self.store.conn()) as conn:
            self.assertEqual(conn.execute('select count(*) from events').fetchone()[0], 1)
        self.assertIn('2026-01-02', self.calls()[-1]['args'][-1])

    def test_imported_status_by_recency(self):
        self.assertEqual(session_status(1000, 'error', 1001, 1599, imported=True), 'active')
        self.assertEqual(session_status(1000, 'tool_call', None, 1600, imported=True), 'idle')
        self.assertEqual(session_status(1000, None, None, 87400, imported=True), 'done')

    def test_missing_tool_marks_partial(self):
        self.bin.unlink()
        collector = Collector(self.store, self.cfg, [self.adapter])
        collector.run_once()
        self.assertEqual(collector.health()['importer']['state'], 'partial')
        self.assertTrue(collector.health()['importer']['error'])

    def test_env_scrubbed(self):
        with patch.dict(os.environ, {'FAKE_KEY': 'private', 'fake_token': 'private', 'FAKE_SECRET': 'private', 'FAKE_PASSWORD': 'private'}):
            self.adapter.poll(self.store, self.source)
        for call in self.calls():
            self.assertFalse(any(part in k.upper() for k in call['env'] for part in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD')))

    def test_install_requires_token(self):
        from hq.server import make_server
        import http.client
        try:
            server, token = make_server(self.store, Collector(self.store, self.cfg, []), self.cfg, self.root, port=0)
        except PermissionError:
            self.skipTest('socket binding unavailable')
        self.addCleanup(server.server_close)
        import threading
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port)
        self.addCleanup(conn.close)
        conn.request('POST', '/api/importer/install', '{}', {'Origin': f'http://127.0.0.1:{server.server_port}'})
        response = conn.getresponse()
        self.assertEqual(response.status, 403)
        response.read()


class InstallCommandTests(unittest.TestCase):
    """On Windows npm is npm.cmd: shell=False cannot launch a bare 'npm', so the
    install must resolve the real path, and allow more than the 60 s run timeout."""

    def test_install_resolves_npm_path_and_uses_long_timeout(self):
        from unittest.mock import patch
        import tempfile
        from hq.adapters import importer
        seen = {}

        def fake_run(args, **kwargs):
            seen['args'], seen['timeout'], seen['shell'] = args, kwargs.get('timeout'), kwargs.get('shell')
            return subprocess.CompletedProcess(args, 0, '', '')

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with patch('shutil.which', return_value=r'C:\node\npm.cmd'), patch('subprocess.run', fake_run):
            importer.install(Path(tmp.name) / 'tools')
        self.assertEqual(seen['args'][0], r'C:\node\npm.cmd')
        self.assertEqual(seen['args'][-1], 'llm-usage-metrics@0.12.1')
        self.assertFalse(seen['shell'])
        self.assertGreaterEqual(seen['timeout'], 300)

    def test_install_without_npm_explains(self):
        from unittest.mock import patch
        import tempfile
        from hq.adapters import importer
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with patch('shutil.which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'npm'):
                importer.install(Path(tmp.name) / 'tools')
