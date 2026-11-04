import json
import unittest
from pathlib import Path

from hq.run.acp import AcpDriver
from hq.run.launchers.agy import AgyLauncher
from hq.run.launchers.opencode import OpencodeLauncher
from hq.run.request import RunRequest


def decode(lines):
    return [json.loads(line) for line in lines]


class AcpTests(unittest.TestCase):
    def setUp(self):
        self.cwd = Path('.').resolve()
        self.driver = AcpDriver(self.cwd)

    def ready(self, session='sess-1'):
        init = decode(self.driver.start('hello'))[0]
        self.assertEqual(init['method'], 'initialize')
        _, lines = self.driver.on_line(json.dumps({'jsonrpc': '2.0', 'id': init['id'], 'result': {}}))
        new = decode(lines)[0]
        _, lines = self.driver.on_line(json.dumps({'jsonrpc': '2.0', 'id': new['id'], 'result': {'sessionId': session}}))
        return new, decode(lines)

    def permission(self, options, ident=42):
        events, _ = self.driver.on_line(json.dumps({'jsonrpc': '2.0', 'id': ident, 'method': 'session/request_permission', 'params': {'toolCall': {'title': 'a.py', 'kind': 'edit', 'rawInput': {'file_path': 'a.py'}}, 'options': options}}))
        return events[0].request['request_id']

    def test_acp_handshake_then_prompt(self):
        new, lines = self.ready()
        self.assertEqual(new['params'], {'cwd': str(self.cwd), 'mcpServers': []})
        self.assertEqual(lines[0]['params'], {'sessionId': 'sess-1', 'prompt': [{'type': 'text', 'text': 'hello'}]})
        events, _ = self.driver.on_line(json.dumps({'method': 'session/update', 'params': {'update': {'content': {'type': 'text', 'text': 'hi'}}}}))
        self.assertEqual((events[0].kind, events[0].text), ('working', 'hi'))
        events, _ = self.driver.on_line(json.dumps({'id': lines[0]['id'], 'result': {'stopReason': 'end_turn'}}))
        self.assertEqual(events[0].kind, 'turn_end')

    def test_acp_permission_allow_sends_allow_once(self):
        rid = self.permission([{'kind': 'allow_once', 'optionId': 'yes'}])
        reply = decode(self.driver.answer(rid, 'allow', None))[0]
        self.assertEqual((reply['id'], reply['result']['outcome']['optionId']), (42, 'yes'))

    def test_acp_permission_deny_sends_reject_once(self):
        rid = self.permission([{'kind': 'reject_once', 'optionId': 'no'}])
        self.assertEqual(decode(self.driver.answer(rid, 'deny', None))[0]['result']['outcome']['optionId'], 'no')

    def test_acp_never_sends_always_options(self):
        for decision in ('allow', 'deny'):
            rid = self.permission([{'kind': 'allow_always', 'optionId': 'a'}, {'kind': 'reject_always', 'optionId': 'b'}], 50 if decision == 'allow' else 51)
            self.assertEqual(decode(self.driver.answer(rid, decision, None))[0]['result']['outcome'], {'outcome': 'cancelled'})

    def test_acp_answers_match_request_ids(self):
        first = self.permission([{'kind': 'allow_once', 'optionId': 'first'}], 10)
        second = self.permission([{'kind': 'allow_once', 'optionId': 'second'}], 11)
        for key, ident, option in ((second, 11, 'second'), (first, 10, 'first')):
            reply = decode(self.driver.answer(key, 'allow', None))[0]
            self.assertEqual((reply['id'], reply['result']['outcome']['optionId']), (ident, option))
        self.assertEqual(self.driver.answer(first, 'allow', None), [])

    def test_acp_unknown_request_gets_error(self):
        _, lines = self.driver.on_line(json.dumps({'id': 7, 'method': 'fs/readTextFile', 'params': {}}))
        self.assertEqual(decode(lines)[0]['error'], {'code': -32601, 'message': 'not supported'})

    def test_acp_follow_up_same_session(self):
        self.ready()
        self.assertEqual(decode(self.driver.follow_up('again'))[0]['params']['sessionId'], 'sess-1')

    def test_acp_resume_uses_session_load(self):
        self.driver = AcpDriver(self.cwd, 'saved')
        new, _ = self.ready('saved')
        self.assertEqual(new['method'], 'session/load')
        self.assertEqual(new['params']['sessionId'], 'saved')

    def test_acp_bad_session_id_ignored(self):
        _, lines = self.ready('../bad')
        self.assertIsNone(self.driver.session_id)
        self.assertEqual(lines, [])

    def test_opencode_config_content_env(self):
        launcher = OpencodeLauncher(self.cwd / 'worktrees')
        for level, permission in [('ask', 'ask'), ('edit', 'allow')]:
            req = RunRequest('opencode', None, 'p', 'hello', level, 'block', None, 30)
            launch = launcher.build(req, None, self.cwd, 'run', {}, [])
            self.assertEqual(launch.argv, ['opencode', 'acp', '--cwd', str(self.cwd)])
            # Found in the real goal check: opencode rejects "model": null ("Expected string | undefined").
            self.assertEqual(json.loads(launch.env['OPENCODE_CONFIG_CONTENT']), {'permission': {'edit': permission, 'bash': permission}})
        req = RunRequest('opencode', 'opencode/glm-5.3-flash-free', 'p', 'hello', 'edit', 'block', None, 30)
        config = json.loads(launcher.build(req, None, self.cwd, 'run', {}, []).env['OPENCODE_CONFIG_CONTENT'])
        self.assertEqual(config['model'], 'opencode/glm-5.3-flash-free')

    def test_agy_live_first_input_and_follow_up_shape(self):
        launcher = AgyLauncher()
        for line in (launcher.first_input('hello'), launcher.user_message('hello')):
            self.assertEqual(json.loads(line), {'event': 'user', 'message': {'role': 'user', 'content': 'hello'}})

class AcpRunnerTests(unittest.TestCase):
    def test_fake_acp_runner_approval_and_follow_up(self):
        import sys
        import tempfile
        import time
        from unittest.mock import patch
        from hq.run.approvals import ApprovalBook
        from hq.run.config import Project, RunConfig
        from hq.run.launchers.base import Launch
        from hq.run.runner import Runner
        from hq.store import Store

        fixture = Path(__file__).parent / 'fixtures' / 'run' / 'fake_acp.py'
        class FakeAcp(OpencodeLauncher):
            exe = sys.executable
            def build(self, req, profile, workdir, run_id, env_base, pass_names):
                return Launch([sys.executable, str(fixture)], dict(env_base), workdir)

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        store = Store(root / 'db')
        self.addCleanup(store.close)
        book = ApprovalBook(store, 1, lambda event: None)
        runner = Runner(store, RunConfig(), {'opencode': FakeAcp()}, root / 'worktrees',
                        lambda *args: None, lambda event: None, approvals=book,
                        projects=[Project('p', str(root))], idle_close_s=30)
        self.addCleanup(runner.shutdown)
        req = RunRequest('opencode', None, 'p', 'ask', 'ask', 'block', None, 1)
        with patch('hq.run.runner.workspace.create', return_value=(root, None, None, None)):
            rid = runner.start(req, None)['id']
            def wait(predicate):
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if predicate(): return
                    time.sleep(.01)
                self.fail('condition not reached: ' + str(runner.output(rid)))
            wait(lambda: bool(book.waiting(rid)))
            book.answer(book.waiting(rid)[0].id, 'allow_once', None)
            wait(lambda: store.get_run(rid)['status'] == 'idle')
            self.assertTrue(any('"optionId": "yes"' in line for line in runner.output(rid)))
            self.assertEqual(store.get_run(rid)['session_id'], 'opencode:fake-session')
            runner.send(rid, 'again')
            wait(lambda: store.get_run(rid)['status'] == 'idle')
            self.assertTrue(any('again' in line for line in runner.output(rid)))


class AcpRealShapeTests(unittest.TestCase):
    # Real opencode 1.18 permission shapes (2026-11-04): edit requests carry the path as filePath /
    # locations, and commands come as kind "execute". Ids and paths invented.
    def driver(self):
        from hq.run.acp import AcpDriver
        return AcpDriver(Path('.').resolve())

    def request(self, d, call):
        events, _ = d.on_line(json.dumps({'jsonrpc': '2.0', 'id': 7, 'method': 'session/request_permission',
                                          'params': {'toolCall': call, 'options': []}}))
        return events[0].request

    def test_edit_request_shows_the_file(self):
        from hq.run.approvals import summarize
        req = self.request(self.driver(), {'title': 'C:/w/oc.txt', 'kind': 'edit', 'locations': [{'path': 'C:/w/oc.txt'}],
                                           'rawInput': {'filePath': 'C:/w/oc.txt', 'content': 'hi'}})
        self.assertEqual(req['tool_name'], 'Write')
        self.assertEqual(req['input']['file_path'], 'C:/w/oc.txt')
        self.assertEqual(summarize(req['tool_name'], req['input']), 'C:/w/oc.txt')

    def test_execute_request_is_a_command_under_never_auto(self):
        from hq.run.approvals import never_auto
        req = self.request(self.driver(), {'title': 'rm calc.py', 'kind': 'execute', 'rawInput': {'command': 'rm calc.py'}})
        self.assertEqual(req['tool_name'], 'Bash')
        self.assertEqual(req['input']['command'], 'rm calc.py')
        self.assertTrue(never_auto(req['tool_name'], req['input']))
