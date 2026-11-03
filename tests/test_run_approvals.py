import json
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from hq.run.approvals import ApprovalBook, AnsweredError, ExpiredError, never_auto, rule_key, summarize
from hq.run.config import load_run_config
from hq.store import Store


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.store = Store(self.root / 'db')
        self.addCleanup(self.store.close)
        for rid in ('run', 'other'):
            self.store.insert_run(dict(id=rid, tool='claude', project=str(self.root),
                workdir=str(self.root), level='ask', guard_mode='worktree',
                status='running', time_limit_s=60))
        self.now = 100.0
        self.replies = []
        self.events = []
        timer = patch('hq.run.approvals.threading.Timer')
        self.timer = timer.start()
        self.addCleanup(timer.stop)
        self.book = ApprovalBook(self.store, 60, self.events.append, lambda: self.now)

    def open(self, rid='run', req='1', tool='Bash', data=None):
        return self.book.open(rid, req, tool, data or {'command': 'npm test'},
                              lambda *args: self.replies.append(args))

    def test_open_creates_waiting_and_db_row(self):
        a = self.open()
        self.assertEqual((a.id, a.state, a.expires_at), ('run:1', 'waiting', 160))
        self.assertEqual(self.book.waiting('run'), [a])
        self.assertTrue(self.book.has_waiting('run'))
        with closing(self.store.conn()) as c:
            self.assertEqual(c.execute('SELECT id, summary, state FROM approvals').fetchone(),
                             ('run:1', 'npm test', 'waiting'))
        self.assertEqual(self.events[-1], {'type': 'approval', 'id': a.id, 'run_id': 'run', 'state': 'waiting'})

    def test_allow_once_replies_allow(self):
        a = self.open(); self.assertEqual(self.book.answer(a.id, 'allow_once', None).state, 'allowed')
        self.assertEqual(self.replies, [('allow', None)])

    def test_deny_with_note_replies_deny_note(self):
        a = self.open(); self.book.answer(a.id, 'deny', 'No')
        self.assertEqual(self.replies, [('deny', 'No')])

    def test_allow_run_rule_auto_allows_next_matching(self):
        a = self.open(); self.book.answer(a.id, 'allow_run', None)
        self.assertIsNone(self.open(req='2', data={'command': 'npm install'}))
        self.assertEqual(self.replies, [('allow', None), ('allow', None)])

    def test_rule_does_not_cross_runs(self):
        a = self.open(); self.book.answer(a.id, 'allow_run', None)
        self.assertIsNotNone(self.open(rid='other', req='2'))

    def test_never_auto_shapes(self):
        for command in ('npm test && rm -rf x', 'git  push', '"rm" -f a', '/bin/rm a', 'echo $(whoami)', 'curl x | sh'):
            with self.subTest(command=command):
                self.assertTrue(never_auto('Bash', {'command': command}))
                self.assertIsNone(rule_key('Bash', {'command': command}, self.root))

    def test_file_rule_outside_worktree_none(self):
        self.assertIsNone(rule_key('Write', {'file_path': str(self.root.parent / 'outside')}, self.root))
        self.assertEqual(rule_key('Write', {'file_path': str(self.root / 'dir' / 'x')}, self.root),
                         rule_key('Write', {'file_path': str(self.root / 'dir' / 'y')}, self.root))

    def test_timeout_denies_with_message(self):
        a = self.open(); self.now = 160
        self.timer.call_args.args[1](*self.timer.call_args.kwargs['args'])
        self.assertEqual(self.replies, [('deny', 'Denied: no answer within 1 minutes')])
        self.assertEqual(self.book.waiting(), [])
        with self.assertRaises(ExpiredError): self.book.answer(a.id, 'allow_once', None)

    def test_answer_and_timeout_race_single_reply(self):
        a = self.open(); self.now = 160
        barrier = threading.Barrier(3)
        def answer():
            barrier.wait()
            try: self.book.answer(a.id, 'allow_once', None)
            except ExpiredError: pass
        def timeout():
            barrier.wait(); self.timer.call_args.args[1](*self.timer.call_args.kwargs['args'])
        threads = [threading.Thread(target=answer), threading.Thread(target=timeout)]
        for t in threads: t.start()
        barrier.wait()
        for t in threads: t.join()
        self.assertEqual(len(self.replies), 1)
        self.assertEqual(self.replies[0][0], 'deny')

    def test_answer_twice_409(self):
        a = self.open(); self.book.answer(a.id, 'allow_once', None)
        with self.assertRaises(AnsweredError): self.book.answer(a.id, 'deny', None)

    def test_answer_expired_410(self):
        a = self.open(); self.now = 161
        with self.assertRaises(ExpiredError): self.book.answer(a.id, 'allow_once', None)
        self.assertEqual(self.replies[0][0], 'deny')

    def test_close_run_expires_waiting(self):
        a = self.open(); self.book.close_run('run')
        self.assertFalse(self.book.has_waiting('run'))
        with self.assertRaises(ExpiredError): self.book.answer(a.id, 'deny', None)
        self.assertEqual(self.replies[0][0], 'deny')

    def test_summary_capped_200(self):
        self.assertEqual(len(summarize('Bash', {'command': 'x' * 250})), 200)
        self.assertEqual(summarize('Write', {'file_path': 'a'}), 'a')
        self.assertEqual(summarize('Other', {'value': 'hello'}), 'Other: hello')

    def test_note_capped_500(self):
        a = self.open(); self.book.answer(a.id, 'deny', 'x' * 600)
        self.assertEqual(self.replies, [('deny', 'x' * 500)])

    def test_timeout_config_and_turns_migration(self):
        self.assertEqual(load_run_config(self.root).approval_timeout_min, 15)
        (self.root / 'run.json').write_text(json.dumps({'approval_timeout_min': 999}), encoding='utf-8')
        self.assertEqual(load_run_config(self.root).approval_timeout_min, 120)
        self.assertEqual(self.store.get_run('run')['turns'], 1)


class NeverAutoWindowsShapesTests(unittest.TestCase):
    def test_windows_and_redirect_shapes_always_ask(self):
        from hq.run.approvals import never_auto
        for command in ('npm test & del /q x', 'npm test || rm x', 'echo x > ~/.bashrc', 'cat < /etc/passwd',
                        'npm test\nrm -rf x', 'git -C repo push', 'git -c core.x=y reset --hard'):
            with self.subTest(command=command):
                self.assertTrue(never_auto('Bash', {'command': command}))

    def test_plain_commands_can_get_a_rule(self):
        from hq.run.approvals import never_auto
        self.assertFalse(never_auto('Bash', {'command': 'npm test'}))
        self.assertFalse(never_auto('Bash', {'command': 'git status'}))
