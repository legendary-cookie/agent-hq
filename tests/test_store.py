import tempfile
from contextlib import closing
import unittest
from pathlib import Path
from hq.records import Batch, SessionRec, EventRec
from hq.store import Store

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'hq.db')
        self.addCleanup(self.store.close)
        self.session = SessionRec('s','codex','openai',None,None,None,0,9,None,None,'source')
        self.account = lambda h, p, m: 'account'

    def event(self, ext, ts, kind, session='s'):
        return EventRec(session, ext, ts, kind, None, None, None, None, None, None, False, None)

    def test_apply_inserts_and_is_idempotent(self):
        batch = Batch([self.session], [self.event('1',0,'user'),self.event('2',9,'turn_end')], [], [])
        self.assertTrue(self.store.apply(batch, self.account))
        self.assertFalse(self.store.apply(batch, self.account))
        with closing(self.store.conn()) as c:
            self.assertEqual(c.execute('select count(*) from sessions').fetchone()[0], 1)
            self.assertEqual(c.execute('select count(*) from events').fetchone()[0], 2)
        other = SessionRec('s2','codex','openai',None,None,None,0,0,None,None,'source')
        self.assertTrue(self.store.apply(Batch([other], [self.event('1', 0, 'user', 's2')], [], []), self.account))
        with closing(self.store.conn()) as c: self.assertEqual(c.execute('select count(*) from events').fetchone()[0], 3)

    def test_session_upsert_extends_last_event(self):
        from dataclasses import replace
        for ts in [9, 20, 10]: self.store.apply(Batch([replace(self.session,last_event_at=ts)], [], [], []), self.account)
        with closing(self.store.conn()) as c: self.assertEqual(c.execute('select last_event_at from sessions').fetchone()[0], 20)

    def test_account_assigned_on_insert(self):
        calls = []
        self.store.apply(Batch([self.session], [], [], []), lambda *args: calls.append(args) or 'chosen')
        self.assertEqual(calls, [('codex','openai',None)])
        with closing(self.store.conn()) as c: self.assertEqual(c.execute('select account_id from sessions').fetchone()[0], 'chosen')

    def test_cursor_roundtrip(self):
        self.assertIsNone(self.store.get_cursor('a','b'))
        self.store.set_cursor('a','b',123,4.5)
        self.assertEqual(self.store.get_cursor('a','b'), (123,4.5))

    def test_derive_tasks(self):
        events = [self.event(str(i),ts,kind) for i,(ts,kind) in enumerate([(0,'user'),(5,'tool_call'),(9,'turn_end'),(20,'user'),(25,'error'),(30,'user')])]
        self.store.apply(Batch([self.session],events,[],[]),self.account)
        self.store.derive_tasks(['s'])
        with closing(self.store.conn()) as c:
            self.assertEqual(c.execute('select started_at, ended_at, outcome from tasks order by started_at').fetchall(), [(0,9,'ok'),(20,25,'error'),(30,None,'open')])

    def test_error_mid_turn_ends_at_turn_end(self):
        events = [self.event(str(i),ts,kind) for i,(ts,kind) in enumerate([(0,'user'),(3,'error'),(5,'tool_call'),(9,'turn_end')])]
        self.store.apply(Batch([self.session],events,[],[]),self.account)
        self.store.derive_tasks(['s'])
        with closing(self.store.conn()) as c:
            self.assertEqual(c.execute('select started_at, ended_at, outcome from tasks').fetchall(), [(0,9,'error')])

    def test_writer_usable_from_other_thread(self):
        import threading
        errors = []
        def work():
            try: self.store.apply(Batch([self.session],[],[],[]),self.account)
            except Exception as e: errors.append(e)
        t = threading.Thread(target=work); t.start(); t.join()
        self.assertEqual(errors, [])
