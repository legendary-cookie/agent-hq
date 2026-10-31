import unittest
from hq.status import session_status

class StatusTests(unittest.TestCase):
    def test_status_rules(self):
        for last, kind, ended, expected in [(990,'assistant',None,'working'),(990,'turn_end',None,'waiting'),(300,'assistant',None,'idle'),(1000-86400-1,'assistant',None,'done'),(999,'assistant',995,'done'),(999,'error',None,'error'),(700,'tool_call',None,'working'),(300,'tool_call',None,'idle')]:
            with self.subTest(kind=kind, last=last): self.assertEqual(session_status(last,kind,ended,1000), expected)


class LastKindsTests(unittest.TestCase):
    def test_last_kind_per_session_with_ties_broken_by_id(self):
        import sqlite3
        from hq.status import last_kinds
        c = sqlite3.connect(':memory:')
        c.execute('create table events (id integer primary key, session_id text, ts real, kind text)')
        c.executemany('insert into events (session_id, ts, kind) values (?,?,?)',
                      [('a', 1, 'user'), ('a', 5, 'tool_call'), ('a', 5, 'turn_end'), ('b', 2, 'error')])
        self.assertEqual(last_kinds(c), {'a': 'turn_end', 'b': 'error'})
