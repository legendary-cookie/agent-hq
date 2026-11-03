import ast
import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCANNER = Path(__file__).resolve().parents[1] / 'hq/remote/scanner.py'


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve()

    def put(self, name, data):
        path = self.home / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def scan(self, sources=('codex',), cursors=None, cap=20000000, db_cap=200000000):
        request = {'sources': list(sources), 'cursors': cursors or {}, 'cap_bytes': cap, 'db_cap_bytes': db_cap}
        env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home))
        result = subprocess.run([sys.executable, str(SCANNER)], input=json.dumps(request), text=True, capture_output=True, env=env, check=True)
        return json.loads(result.stdout)

    def test_new_file_sent_append_from_zero(self):
        self.put('.codex/sessions/a', b'abc')
        entry = self.scan()['files'][0]
        self.assertEqual((entry['kind'], entry['offset'], base64.b64decode(entry['data_b64'])), ('append', 0, b'abc'))

    def test_cursor_sends_only_new_bytes(self):
        p = self.put('.codex/sessions/a', b'abc')
        entry = self.scan(cursors={'.codex/sessions/a': [1, p.stat().st_mtime]})['files'][0]
        self.assertEqual((entry['offset'], base64.b64decode(entry['data_b64'])), (1, b'bc'))

    def test_shrunk_file_reset(self):
        self.put('.codex/sessions/a', b'a')
        self.assertEqual(self.scan(cursors={'.codex/sessions/a': [3, 0]})['files'][0]['kind'], 'reset')

    def test_forbidden_names_skipped(self):
        self.put('.codex/sessions/auth.json', b'x')
        self.put('.codex/sessions/secrets/a', b'x')
        self.assertEqual(self.scan()['files'], [])

    def test_symlink_out_of_source_skipped(self):
        outside = self.put('outside/a', b'x')
        link = self.home / '.codex/sessions/link'
        link.parent.mkdir(parents=True)
        try:
            link.symlink_to(outside.parent, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('symlinks unavailable')
        self.assertEqual(self.scan()['files'], [])

    def test_cap_sets_more(self):
        self.put('.codex/sessions/a', b'abcdef')
        result = self.scan(cap=3)
        self.assertTrue(result['more'])
        self.assertLessEqual(len(base64.b64decode(result['files'][0]['data_b64'])), 3)

    def test_db_replace_on_change(self):
        self.put('.local/share/opencode/opencode.db', b'db')
        self.assertEqual(self.scan(('opencode',))['files'][0]['kind'], 'replace')

    def test_db_over_cap_skipped(self):
        self.put('.local/share/opencode/opencode.db', b'db')
        self.assertEqual(self.scan(('opencode',), db_cap=1)['files'], [])

    def test_unrequested_source_not_read(self):
        self.put('.claude/projects/a', b'x')
        self.assertEqual(self.scan()['files'], [])

    def test_runs_on_python38_syntax(self):
        ast.parse(SCANNER.read_text(encoding='utf-8'), feature_version=(3, 8))
