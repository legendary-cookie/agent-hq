import json
import sys
import tempfile
import threading
from unittest.mock import patch
import time
import unittest
from pathlib import Path

from hq.run.config import Project, RunConfig
from hq.run.launchers.base import Launch
from hq.run.request import RunRequest
from hq.run.runner import Runner, Busy
from hq.run.stream import StreamEvent
from hq.store import Store

SCRIPT = Path(__file__).parent / 'fixtures' / 'run' / 'fake_agent.py'


class FakeLauncher:
    name = harness = 'fake'
    exe = sys.executable

    def build(self, req, profile, workdir, run_id, env_base, pass_names):
        return Launch([sys.executable, str(SCRIPT), *req.prompt.split()], dict(env_base), workdir)

    def parse(self, line):
        try:
            data = json.loads(line)
        except ValueError:
            return None
        kind = {'session': 'session', 'error': 'error'}.get(data.get('type'), 'working')
        return StreamEvent(kind, data.get('session_id'), data.get('message'))


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()  # Windows TEMP may be an 8.3 short path
        self.store = Store(self.root / 'db'); self.addCleanup(self.store.close)
        self.logs = []; self.limits = []; self.events = []
        self.runner = Runner(self.store, RunConfig(max_concurrent=1), {'fake': FakeLauncher()},
                             self.root / 'worktrees', lambda *x: self.logs.append(x), self.events.append,
                             pu_limit=lambda *x: self.limits.append(x), minute_s=.5,
                             projects=[Project('project', self.root)])
        self.addCleanup(self.runner.shutdown)

    def start(self, prompt='ok'):
        return self.runner.start(RunRequest('fake', 'model', 'project', prompt, 'read_only', 'warn', None, 1), None)

    def wait(self, rid, statuses=('done', 'failed', 'stopped', 'timed_out', 'interrupted')):
        end = time.monotonic() + 10
        while time.monotonic() < end:
            row = self.store.get_run(rid)
            if row['status'] in statuses and row['ended_at'] is not None: return row
            time.sleep(.01)
        self.fail('run did not finish')

    def test_ok_run_done_with_session_and_output(self):
        row = self.wait(self.start()['id']); self.assertEqual(row['status'], 'done')
        self.assertEqual(row['session_id'], 'fake:s-1'); self.assertEqual(len(self.runner.output(row['id'])), 4)

    def test_long_line_is_bounded_and_run_completes(self):
        rid = self.start('longline')['id']
        self.assertEqual(self.wait(rid)['status'], 'done')
        lines = self.runner.output(rid)
        self.assertEqual(len(lines), 1)
        self.assertLessEqual(len(lines[0]), 65536)
        self.assertTrue(lines[0].endswith(' …[truncated]'))

    def test_finished_run_states_are_pruned(self):
        for _ in range(205):
            rid = self.start()['id']
            self.wait(rid)
        with self.runner._lock:
            self.assertLessEqual(len(self.runner._runs), 200)

    def test_fail_run_failed_exit_code(self):
        row = self.wait(self.start('fail')['id']); self.assertEqual((row['status'], row['exit_code']), ('failed', 3))

    def test_missing_executable_fails_with_reason_in_output(self):
        self.runner.executables['fake'] = None
        rid = self.start('ok')['id']
        self.assertEqual(self.wait(rid)['status'], 'failed')
        self.assertTrue(any('run failed: FileNotFoundError' in line for line in self.runner.output(rid)))

    def test_stop_kills_within_5s(self):
        rid = self.start('sleep 30')['id']; self.runner.stop(rid)
        self.assertEqual(self.wait(rid)['status'], 'stopped')

    def test_stop_during_start(self):
        from hq.run import proc
        entered, release = threading.Event(), threading.Event()
        real_spawn = proc.spawn
        children = []
        def delayed(launch, should_stop):
            entered.set()
            self.assertTrue(release.wait(10))
            child = real_spawn(launch, should_stop)
            children.append(child)
            return child
        with patch('hq.run.runner.proc.spawn', side_effect=delayed):
            rid = self.start('sleep 30')['id']
            self.assertTrue(entered.wait(10))
            self.runner.stop(rid)
            release.set()
            self.assertEqual(self.wait(rid)['status'], 'stopped')
            self.assertFalse(children[0].alive())

    def test_time_limit_sets_timed_out(self):
        self.assertEqual(self.wait(self.start('sleep 30')['id'])['status'], 'timed_out')

    def test_concurrency_busy(self):
        rid = self.start('sleep 30')['id']
        with self.assertRaises(Busy): self.start()
        self.runner.stop(rid)

    def test_ratelimit_status_and_pu_log_outcome(self):
        rid = self.start('ratelimit')['id']; row = self.wait(rid, ('rate_limited',))
        self.assertEqual(row['status'], 'rate_limited'); self.assertEqual(self.logs[-1][3], 'rate_limited')
        self.assertEqual(len(self.limits), 1)

    def test_garbage_lines_kept_and_counted(self):
        rid = self.start('garbage')['id']; self.wait(rid)
        self.assertEqual(self.runner.output(rid), ['not json'])
        self.assertEqual(self.runner.warnings(rid), 1)

    def test_ring_keeps_last_500(self):
        rid = self.start('lines 510')['id']; self.wait(rid)
        self.assertEqual(len(self.runner.output(rid)), 500)

    def test_recover_marks_interrupted(self):
        self.store.insert_run(dict(id='old', tool='fake', project='p', workdir='p', level='edit', guard_mode='warn', status='starting', time_limit_s=1))
        self.assertEqual(self.runner.recover(), 1)

    def test_prompt_preview_200_chars(self):
        row = self.start('garbage ' + 'x' * 300); self.assertEqual(len(row['prompt_preview']), 200)

    def _grandchild(self):
        rid = self.start('grandchild')['id']
        end = time.monotonic() + 10
        while time.monotonic() < end:
            lines = self.runner.output(rid)
            if lines:
                return rid, json.loads(lines[0])['pid']
            time.sleep(.01)
        self.fail('grandchild did not start')

    def _assert_dead(self, pid):
        import os
        def alive():
            if os.name == 'nt':
                import ctypes
                from ctypes import wintypes
                kernel = ctypes.windll.kernel32
                kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                kernel.OpenProcess.restype = wintypes.HANDLE
                kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
                kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                handle = kernel.OpenProcess(0x1000, False, pid)
                if not handle: return False
                code = wintypes.DWORD()
                try: return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value == 259)
                finally: kernel.CloseHandle(handle)
            try:
                os.kill(pid, 0)
                # A zombie has exited, even if the container's init has not reaped it.
                stat = Path(f'/proc/{pid}/stat')
                if stat.exists() and stat.read_text().split(') ')[1].startswith('Z'):
                    return False
                return True
            except (ProcessLookupError, FileNotFoundError): return False
        end = time.monotonic() + 10
        while time.monotonic() < end:
            if not alive(): return
            time.sleep(.01)
        self.fail(f'process {pid} still alive')

    def test_grandchild_killed_on_stop(self):
        rid, pid = self._grandchild()
        child = self.runner._runs[rid]['child']
        self.runner.stop(rid); self.wait(rid)
        self.assertFalse(child.alive()); self._assert_dead(pid)

    def test_shutdown_kills_grandchild(self):
        rid, pid = self._grandchild()
        child = self.runner._runs[rid]['child']
        self.runner.shutdown(); self.assertEqual(self.wait(rid)['status'], 'interrupted')
        self.assertFalse(child.alive()); self._assert_dead(pid)

    def test_ratelimit_calls_pu_limit_and_blocks_profile(self):
        from hq.config import Profile
        from hq.run.pool import pick, profile_states
        profile = Profile('profile', 'fake', str(self.root / 'profile'), 'account')
        req = RunRequest('fake', 'model', 'project', 'ratelimit', 'read_only', 'warn', 'profile', 1)
        rid = self.runner.start(req, profile)['id']
        self.wait(rid, ('rate_limited',))
        self.assertEqual(self.limits, [('fake', 'model', None)])
        self.assertIsNone(pick(profile_states(self.store, [profile], time.time()), time.time()))
