import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from hq.tail import read_new_lines
from hq.adapters.base import preview, ro_connect

class TailTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'log'

    def test_tail_reads_complete_lines(self):
        self.path.write_bytes(b'one\ntwo\n')
        lines, cursor = read_new_lines(self.path, None)
        self.assertEqual(lines, ['one','two'])
        self.assertEqual(cursor[0], 8)
        self.assertEqual(read_new_lines(self.path, cursor)[0], [])

    def test_tail_partial_line(self):
        self.path.write_bytes(b'{"a":1}\n{"b":')
        lines, cursor = read_new_lines(self.path, None)
        self.assertEqual(lines, ['{"a":1}'])
        self.assertEqual(cursor[0], len(b'{"a":1}\n'))
        with self.path.open('ab') as f: f.write(b'2}\n')
        self.assertEqual(read_new_lines(self.path,cursor)[0], ['{"b":2}'])

    def test_tail_truncated_restarts(self):
        self.path.write_bytes(b'long original line\n')
        _, cursor = read_new_lines(self.path,None)
        self.path.write_bytes(b'new\n')
        lines, cursor = read_new_lines(self.path,cursor)
        self.assertEqual(lines,['new'])
        self.assertEqual(cursor[0],4)

    def test_preview_truncates_to_200(self):
        self.assertEqual(preview('  a\n  b  '), 'a b')
        self.assertEqual(preview('x'*250), 'x'*200)
        self.assertIsNone(preview(None))

    def test_ro_connect_cannot_write(self):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute('create table t (x)')
            conn.commit()
        with closing(ro_connect(self.path)) as conn:
            with self.assertRaises(sqlite3.OperationalError): conn.execute('insert into t values (1)')
