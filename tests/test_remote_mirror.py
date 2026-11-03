import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hq.remote.config import Host
from hq.remote.mirror import apply_answer, cursors_for, prune_hosts


class MirrorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'box'
        self.host = Host('box', 'box', 'linux', None, 60, ('codex',))

    def entry(self, path='.codex/sessions/a', data=b'abc', kind='append', offset=0):
        return {'path': path, 'kind': kind, 'offset': offset, 'data_b64': base64.b64encode(data).decode(), 'size': offset + len(data), 'mtime': 123.0}

    def test_append_and_cursor_roundtrip(self):
        result = apply_answer({'files': [self.entry()], 'more': False}, self.host, self.root)
        self.assertEqual((result.written, result.bytes), (1, 3))
        self.assertEqual(cursors_for(self.root, self.host), {'.codex/sessions/a': [3, 123.0]})

    def test_hostile_paths_refused(self):
        paths = ['.codex/sessions/../../x', '/absolute', 'C:/x', r'\\server\share', r'.codex\sessions\x', '.codex/sessions/auth.json', '.claude/projects/a', '.codex/sessions/a\x00b']
        entries = [self.entry(path) for path in paths] + [{**self.entry(), 'data_b64': '!!!'}]
        result = apply_answer({'files': entries}, self.host, self.root)
        self.assertEqual(len(result.errors), len(entries))
        self.assertFalse((self.root / '.codex/sessions/a').exists())

    def test_offset_mismatch_resets(self):
        apply_answer({'files': [self.entry()]}, self.host, self.root)
        result = apply_answer({'files': [self.entry(data=b'x', offset=1)]}, self.host, self.root)
        self.assertEqual(result.resets, 1)
        self.assertEqual(cursors_for(self.root, self.host), {})
        self.assertEqual((self.root / '.codex/sessions/a').read_bytes(), b'abc')

    def test_replace_is_atomic(self):
        apply_answer({'files': [self.entry()]}, self.host, self.root)
        with patch('hq.remote.mirror.os.replace', side_effect=OSError('crash')):
            apply_answer({'files': [self.entry(data=b'new', kind='replace')]}, self.host, self.root)
        self.assertEqual((self.root / '.codex/sessions/a').read_bytes(), b'abc')

    def test_prune_removed_hosts(self):
        self.root.mkdir()
        other = self.root.parent / 'keep'
        other.mkdir()
        prune_hosts(self.root.parent, {'keep'})
        self.assertFalse(self.root.exists())
        self.assertTrue(other.exists())
