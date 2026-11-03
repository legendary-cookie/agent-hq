import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from hq.run.approvals import ApprovalBook
from hq.run.config import Project, RunConfig
from hq.run.launchers.base import Launch
from hq.run.launchers.claude import ClaudeLauncher
from hq.run.launchers.codex import CodexLauncher
from hq.run.request import RunRequest
from hq.run.runner import Runner, NotIdle
from hq.store import Store

SCRIPT = Path(__file__).parent / 'fixtures' / 'run' / 'fake_agent.py'


class FakeLive(ClaudeLauncher):
    name = harness = 'claude'
    exe = sys.executable

    def build(self, req, profile, workdir, run_id, env_base, pass_names):
        return Launch([sys.executable, str(SCRIPT), *req.prompt.split()], dict(env_base), workdir, self.first_input(req.prompt))


class FakeResume(FakeLive):
    steer_mode = 'resume'
    def build(self, req, profile, workdir, run_id, env_base, pass_names):
        return Launch([sys.executable, str(SCRIPT), 'resume'], dict(env_base), workdir)
    def build_resume(self, req, profile, workdir, run_id, session_id, text, env_base, pass_names):
        self.resumed = CodexLauncher(self.worktrees_root).build_resume(
            req, profile, workdir, run_id, session_id, text, env_base, pass_names).argv
        return self.build(req, profile, workdir, run_id, env_base, pass_names)


class SteerRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.store = Store(self.root / 'db'); self.addCleanup(self.store.close)
        self.book = ApprovalBook(self.store, .3, lambda event: None)
        self.runner = Runner(self.store, RunConfig(max_concurrent=2), {'claude': FakeLive()},
                             self.root / 'worktrees', lambda *args: None, lambda event: None,
                             approvals=self.book, idle_close_s=.4, projects=[Project('p', self.root)])
        self.addCleanup(self.runner.shutdown)

    def start(self, prompt):
        req = RunRequest('claude', None, 'p', prompt, 'read_only', 'warn', None, 1)
        return self.runner.start(req, None)['id']

    def wait_for(self, predicate):
        end = time.monotonic() + 5
        while time.monotonic() < end:
            if predicate(): return
            time.sleep(.01)
        self.fail('condition not reached')

    def status(self, rid, status):
        self.wait_for(lambda: self.store.get_run(rid)['status'] == status)

    def output_has(self, rid, text):
        self.wait_for(lambda: any(text in line for line in self.runner.output(rid)))

    def ask(self):
        rid = self.start('ask Bash echo')
        self.wait_for(lambda: bool(self.book.waiting(rid)))
        return rid, self.book.waiting(rid)[0].id

    def test_ask_allow_reaches_agent(self):
        rid, aid = self.ask(); self.book.answer(aid, 'allow_once', None)
        self.output_has(rid, '"behavior": "allow"')

    def test_ask_deny_reaches_agent(self):
        rid, aid = self.ask(); self.book.answer(aid, 'deny', 'no')
        self.output_has(rid, '"behavior": "deny"')

    def test_ask_timeout_denies(self):
        rid, _ = self.ask(); self.output_has(rid, '"behavior": "deny"')

    def test_status_needs_you_then_running(self):
        rid, aid = self.ask(); self.status(rid, 'needs_you')
        self.book.answer(aid, 'allow_once', None); self.status(rid, 'running')

    def test_live_turn_end_sets_idle(self):
        rid = self.start('live'); self.status(rid, 'idle')

    def test_send_live_followup(self):
        rid = self.start('live'); self.status(rid, 'idle'); self.runner.send(rid, 'hello')
        self.output_has(rid, 'hello'); self.status(rid, 'idle')
        self.assertEqual(self.store.get_run(rid)['turns'], 2)

    def test_followup_while_working_refused(self):
        rid, _ = self.ask()
        with self.assertRaises(NotIdle): self.runner.send(rid, 'no')

    def test_resume_keeps_frozen_level(self):
        self.runner.launchers['claude'] = FakeResume()
        rid = self.start('resume'); self.status(rid, 'done')
        with self.store._lock, self.store._writer:
            self.store._writer.execute('UPDATE runs SET level=? WHERE id=?', ('full_auto', rid))
        self.runner.send(rid, 'again'); self.status(rid, 'done')
        argv = self.runner.launchers['claude'].resumed
        self.assertIn('raw-id', argv)
        self.assertEqual(argv[argv.index('-s') + 1], 'read-only')

    def test_stop_while_waiting_denies_then_stops(self):
        rid, aid = self.ask(); self.runner.stop(rid); self.status(rid, 'stopped')
        self.assertEqual(self.book._items[aid][0].state, 'denied')

    def test_exit_while_waiting_expires(self):
        rid = self.start('ask Bash echo exit'); self.status(rid, 'done')
        self.assertEqual(self.book._items[rid + ':r1'][0].state, 'expired')

    def test_unknown_control_denied(self):
        rid = self.start('unknown'); self.output_has(rid, '"behavior": "deny"')
        self.assertEqual(self.runner.warnings(rid), 1)

    def test_idle_close_ends_live_run(self):
        rid = self.start('live'); self.status(rid, 'idle'); self.status(rid, 'done')
