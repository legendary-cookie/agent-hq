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


class FakeLiveResume(FakeLive):
    def build_resume(self, req, profile, workdir, run_id, session_id, text, env_base, pass_names):
        self.resumed = ClaudeLauncher(self.worktrees_root).build_resume(
            req, profile, workdir, run_id, session_id, text, env_base, pass_names)
        return Launch([sys.executable, str(SCRIPT), 'live'], dict(env_base), workdir, self.first_input(text))


class FakeResume(FakeLive):
    steer_mode = 'resume'
    def build(self, req, profile, workdir, run_id, env_base, pass_names):
        return Launch([sys.executable, str(SCRIPT), 'resume'], dict(env_base), workdir)
    def build_resume(self, req, profile, workdir, run_id, session_id, text, env_base, pass_names):
        self.resumed = CodexLauncher(self.worktrees_root).build_resume(
            req, profile, workdir, run_id, session_id, text, env_base, pass_names).argv
        return self.build(req, profile, workdir, run_id, env_base, pass_names)


class SteerRunnerTests(unittest.TestCase):
    def test_grandchild_holding_stderr_does_not_stick_run(self):
        rid = self.start('stderr_grandchild')
        end = time.monotonic() + 10
        while time.monotonic() < end and self.store.get_run(rid)['status'] != 'done':
            time.sleep(.02)
        self.assertEqual(self.store.get_run(rid)['status'], 'done')

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

    def test_claude_build_resume_argv(self):
        launcher = ClaudeLauncher(self.root / 'worktrees')
        workdir = self.root / 'worktrees' / 'run'
        for level in ('read_only', 'ask', 'edit', 'full_auto'):
            with self.subTest(level=level):
                req = RunRequest('claude', 'model', 'p', 'initial', level, 'warn', None, 1)
                original = launcher.build(req, None, workdir, 'run', {}, [])
                resumed = launcher.build_resume(req, None, workdir, 'run', 'raw-id', 'followup', {}, [])
                self.assertEqual(resumed.argv, original.argv[:2] + ['--resume=raw-id'] + original.argv[2:])
                self.assertEqual(resumed.stdin_text, launcher.first_input('followup'))

    def test_live_run_followup_after_idle_close_resumes(self):
        self.runner.launchers['claude'] = FakeLiveResume()
        rid = self.start('live'); self.status(rid, 'idle')
        self.runner._runs[rid]['raw_session_id'] = 'raw-id'
        self.status(rid, 'done'); self.runner.send(rid, 'followup')
        self.output_has(rid, 'followup'); self.status(rid, 'idle')
        self.assertIn('--resume=raw-id', self.runner.launchers['claude'].resumed.argv)
        self.assertEqual(self.store.get_run(rid)['turns'], 2)

    def finished_row(self, project='p', rid='old'):
        self.store.insert_run(dict(id=rid, tool='claude', project=project, workdir=str(self.root),
            level='read_only', guard_mode='warn', status='done', prompt_preview='original', started_at=1,
            time_limit_s=60, session_id='claude:raw-id'))

    def test_followup_after_restart_rebuilds_from_row_and_keeps_level(self):
        self.finished_row()
        self.runner.shutdown()
        self.runner = Runner(self.store, RunConfig(max_concurrent=2), {'claude': FakeLiveResume()},
                             self.root / 'worktrees', lambda *args: None, lambda event: None,
                             approvals=self.book, idle_close_s=.4, projects=[Project('p', self.root)])
        self.addCleanup(self.runner.shutdown)
        self.runner.send('old', 'followup'); self.output_has('old', 'followup')
        launch = self.runner.launchers['claude'].resumed
        self.assertEqual(launch.argv[launch.argv.index('--permission-mode') + 1], 'plan')
        self.assertIn('--resume=raw-id', launch.argv)
        self.assertEqual(self.store.get_run('old')['turns'], 2)

    def restarted(self, launcher, minute_s=60.0):
        self.runner.shutdown()
        self.runner = Runner(self.store, RunConfig(max_concurrent=2), {'claude': launcher},
                             self.root / 'worktrees', lambda *args: None, lambda event: None,
                             approvals=self.book, idle_close_s=.4, projects=[Project('p', self.root)],
                             minute_s=minute_s)
        self.addCleanup(self.runner.shutdown)

    def test_resume_tool_followup_after_restart(self):
        # Found in the real check: Codex, opencode and Antigravity runs from before a restart
        # could never be continued ("The run cannot be resumed").
        self.finished_row(rid='run_old')  # launchers require real run ids
        self.restarted(FakeResume())
        self.runner.send('run_old', 'followup')
        self.wait_for(lambda: self.store.get_run('run_old')['turns'] == 2)
        self.wait_for(lambda: hasattr(self.runner.launchers['claude'], 'resumed'))
        self.assertIn('raw-id', self.runner.launchers['claude'].resumed)

    def test_followup_on_timed_out_run_resumes(self):
        self.finished_row()
        with self.store._lock, self.store._writer:
            self.store._writer.execute("update runs set status='timed_out' where id='old'")
        self.restarted(FakeLiveResume())
        self.runner.send('old', 'followup'); self.output_has('old', 'followup')

    def test_followup_after_failed_turn_resumes(self):
        # Found in the real check: a failed resume turn left the run impossible to continue.
        self.finished_row(rid='run_old')
        with self.store._lock, self.store._writer:
            self.store._writer.execute("update runs set status='failed' where id='run_old'")
        self.restarted(FakeResume())
        self.runner.send('run_old', 'followup')
        self.wait_for(lambda: self.store.get_run('run_old')['turns'] == 2)

    def test_time_limit_pauses_while_idle(self):
        # Found in the real check: an Ask me run timed out while it sat idle waiting for a follow-up.
        self.runner.minute_s = .3  # 1 minute limit = 0.3 s
        self.runner.idle_close_s = 5  # stay idle well past the time limit
        rid = self.start('live'); self.status(rid, 'idle')
        time.sleep(.6)  # idle for twice the limit
        self.assertEqual(self.store.get_run(rid)['status'], 'idle')

    def test_goal_run_ends_after_its_turn_instead_of_idling(self):
        # Found in the real goal check: a live planner went idle, so the goal waited in
        # 'planning' until the 30-minute idle close.
        self.runner.idle_close_s = 60  # only the one-shot rule can end it within the wait
        req = RunRequest('claude', None, 'p', 'live', 'read_only', 'warn', None, 1)
        rid = self.runner.start(req, None, goal_id='g1')['id']
        self.status(rid, 'done')

    def test_followup_after_restart_refused_when_project_gone(self):
        self.finished_row('gone')
        with self.assertRaisesRegex(ValueError, 'Project'):
            self.runner.send('old', 'followup')

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
        self.assertIn('sandbox_mode="read-only"', argv)

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
