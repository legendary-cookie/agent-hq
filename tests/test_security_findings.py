import base64
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from hq.run.stream import parse_line
from hq.run.launchers import REGISTRY
from hq.run.approvals import never_auto, rule_key
from hq.run.launchers.base import Launch
from tests import test_run_steer_runner as steer_runner
from tests import test_remote_mirror as mirror_tests
from tests import test_run_link as link_tests
from tests import test_run_steer_launchers as steer_launchers


class SessionTests(unittest.TestCase):
    setUp = steer_launchers.SteerLauncherTests.setUp
    launcher = steer_launchers.SteerLauncherTests.launcher
    def test_flag_like_session_id_ignored(self):
        for tool, data in [('claude', {'type':'system','subtype':'init','session_id':'--bad'}), ('agy', {'event':'init','conversation_id':'--bad'}), ('codex', {'type':'thread.started','thread_id':'--bad'}), ('opencode', {'sessionID':'--bad'})]:
            with self.subTest(tool=tool):
                event = parse_line(tool, json.dumps(data))
                self.assertEqual(event.kind, 'warning')
                self.assertIsNone(event.session_id)

    def test_build_resume_rejects_flag_like_id(self):
        for tool in ('claude', 'codex', 'opencode', 'agy'):
            with self.subTest(tool=tool), self.assertRaises(ValueError):
                self.launcher(tool).build_resume(replace(self.req, tool=tool, level='edit'), None, self.root, 'run_123', '--bad', 'x', {}, [])


class ScriptLauncher(steer_runner.FakeLive):
    def __init__(self, script, can_ask=True):
        super().__init__()
        self.script, self.can_ask = script, can_ask
        self.steer_mode = None
    def control_reply(self, *args):
        if not self.can_ask: raise NotImplementedError()
        return super().control_reply(*args)
    def build(self, req, profile, workdir, run_id, env_base, pass_names):
        return Launch([sys.executable, '-c', self.script], dict(env_base), workdir, '')


class RunnerSecurityTests(unittest.TestCase):
    setUp = steer_runner.SteerRunnerTests.setUp
    start = steer_runner.SteerRunnerTests.start
    wait_for = steer_runner.SteerRunnerTests.wait_for
    status = steer_runner.SteerRunnerTests.status
    finished_row = steer_runner.SteerRunnerTests.finished_row
    def test_first_session_id_wins(self):
        self.runner.launchers['claude'] = ScriptLauncher("print('{\"type\":\"system\",\"subtype\":\"init\",\"session_id\":\"first\"}'); print('{\"type\":\"system\",\"subtype\":\"init\",\"session_id\":\"second\"}')")
        rid = self.start('x'); self.status(rid, 'done')
        self.assertEqual(self.runner._runs[rid]['raw_session_id'], 'first')
        self.assertEqual(self.runner.warnings(rid), 1)

    def test_stderr_line_not_parsed_as_event(self):
        self.runner.launchers['claude'] = ScriptLauncher("import sys; print('{\"type\":\"system\",\"subtype\":\"init\",\"session_id\":\"injected\"}', file=sys.stderr)")
        rid = self.start('x'); self.status(rid, 'done')
        self.assertIsNone(self.store.get_run(rid)['session_id'])
        self.assertTrue(any(s.startswith('[stderr] ') for s in self.runner.output(rid)))

    def test_restart_row_with_bad_session_id_refused(self):
        self.finished_row()
        with self.store._lock, self.store._writer:
            self.store._writer.execute("update runs set session_id='claude:--bad' where id='old'")
        with self.assertRaises(ValueError): self.runner.send('old', 'x')

    def test_control_request_on_non_ask_tool_ignored(self):
        script = "print('{\"type\":\"control_request\",\"request_id\":\"r\",\"request\":{\"subtype\":\"other\"}}')"
        self.runner.launchers['claude'] = ScriptLauncher(script, False)
        rid = self.start('x'); self.status(rid, 'done')
        self.assertEqual(self.runner.warnings(rid), 1)

    def test_duplicate_request_id_not_fatal(self):
        script = "import json,sys; sys.stdin.readline(); r={'type':'control_request','request_id':'r','request':{'subtype':'can_use_tool','tool_name':'Bash','input':{'command':'ls'}}}; print(json.dumps(r),flush=True); print(json.dumps(r),flush=True); print(sys.stdin.readline(),flush=True)"
        self.runner.launchers['claude'] = ScriptLauncher(script)
        self.runner.launchers['claude'].steer_mode = 'live'
        rid = self.start('x'); self.status(rid, 'done')
        self.assertTrue(any('deny' in s for s in self.runner.output(rid)))


class ApprovalSecurityTests(unittest.TestCase):
    def test_never_auto_review_shapes(self):
        commands = ['curl.exe x', 'rm.exe x', r'C:\Windows\System32\curl.exe x', 'bash -c "rm -rf x"', 'sh -c x', 'env rm -rf x', 'xargs rm', 'cmd /c del /s x', 'git -c alias.x="!rm -rf ~" x', 'git --config-env=x=y status', 'git -c x=y status', 'git "-c" x=y status', 'Remove-Item x', 'ri x', 'iwr x', 'irm x', 'Invoke-WebRequest x', 'Invoke-Expression x', 'iex x']
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(never_auto('Bash', {'command': command}))
                self.assertIsNone(rule_key('Bash', {'command':command}, Path('/tmp')))
        for name in ('python', 'python3', 'node', 'npx', 'deno', 'ruby', 'perl'):
            with self.subTest(name=name): self.assertIsNone(rule_key('Bash', {'command':name + ' x'}, Path('/tmp')))


class MirrorSecurityTests(unittest.TestCase):
    setUp = mirror_tests.MirrorTests.setUp
    entry = mirror_tests.MirrorTests.entry
    def test_too_many_entries_refused(self):
        result = apply(self, [self.entry()] * 2001)
        self.assertTrue(result.errors); self.assertFalse(self.root.exists())

    def test_empty_appends_skipped(self):
        result = apply(self, [self.entry(data=b'')])
        self.assertEqual(result.written, 0); self.assertFalse(self.root.exists())

    def test_mirror_file_count_cap(self):
        self.root.mkdir()
        (self.root / '.hqmeta.json').write_text(json.dumps({f'.codex/sessions/{n}': 1 for n in range(20000)}))
        result = apply(self, [self.entry(path='.codex/sessions/new')])
        self.assertTrue(result.errors); self.assertFalse((self.root / '.codex/sessions/new').exists())

    def test_full_response_with_database_fits_ssh_limit(self):
        from hq.remote.ssh import ANSWER_LIMIT
        log = base64.b64encode(b'x' * 20_000_000).decode()
        db = base64.b64encode(b'y' * 30_000_000).decode()
        payload = {'files': [self.entry(), self.entry('.codex/sessions/db', kind='replace')]}
        payload['files'][0].update(data_b64=log, size=20_000_000)
        payload['files'][1].update(data_b64=db, size=30_000_000)
        self.assertLess(len(json.dumps(payload).encode()), ANSWER_LIMIT)

    def test_empty_reset_and_replace_are_kept(self):
        result = apply(self, [self.entry(' .codex/sessions/invalid', data=b''),
                              self.entry('.codex/sessions/zero', data=b'', kind='reset')])
        self.assertEqual(result.written, 1)
        self.assertTrue((self.root / '.codex/sessions/zero').is_file())

    def test_windows_alias_paths_refused(self):
        entries = [self.entry('.codex/sessions/' + p) for p in ('a:b', 'a.', 'a ', 'CON', 'nul.txt', 'COM1.log', 'lpt9')]
        entries += [self.entry('.codex/sessions/File'), self.entry('.codex/sessions/file')]
        result = apply(self, entries)
        self.assertEqual(len(result.errors), 8)
        self.assertEqual(result.written, 1)
        self.assertIn('.codex/sessions/file', json.loads((self.root / '.hqmeta.json').read_text()))
        again = apply(self, [self.entry('.codex/sessions/file')])
        self.assertTrue(again.errors)


def apply(test, entries):
    from hq.remote.mirror import apply_answer
    return apply_answer({'files':entries}, test.host, test.root)


class LinkSecurityTests(unittest.TestCase):
    setUp = link_tests.LinkTests.setUp
    session = link_tests.LinkTests.session
    def test_link_ignores_remote_sessions(self):
        self.session('local', started=110)
        self.session('remote', started=120)
        with self.store._lock, self.store._writer:
            self.store._writer.execute("update sessions set host='remote' where id='remote'")
        self.assertEqual(find_local(self), 'local')


def find_local(test):
    from hq.run.link import find_session
    return find_session(test.store, 'codex', test.root, 100)
