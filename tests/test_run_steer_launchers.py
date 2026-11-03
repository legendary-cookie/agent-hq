import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from hq.run.config import Project, RunConfig
from hq.run.launchers import REGISTRY
from hq.run.request import RunRequest, parse_request
from hq.run.stream import parse_line


class SteerLauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.req = RunRequest('claude', 'model', 'project', 'hello', 'ask', 'block', None, 30)

    def launcher(self, name):
        return type(REGISTRY[name])(self.root / 'worktrees')

    def test_claude_ask_flags_exact(self):
        launch = self.launcher('claude').build(self.req, None, self.root, 'run_123', {}, [])
        self.assertEqual(launch.argv, ['claude', '-p', '--input-format', 'stream-json', '--output-format', 'stream-json', '--verbose', '--permission-mode', 'default', '--permission-prompts', 'host', '--permission-prompt-tool', 'stdio', '--model', 'model'])

    def test_claude_first_input_is_stream_json_user(self):
        launcher = self.launcher('claude')
        expected = {'type': 'user', 'message': {'role': 'user', 'content': 'hello'}}
        self.assertEqual(json.loads(launcher.first_input('hello')), expected)
        self.assertEqual(json.loads(launcher.user_message('hello')), expected)
        self.assertEqual(json.loads(launcher.build(self.req, None, self.root, 'run_123', {}, []).stdin_text), expected)
        self.assertEqual(self.launcher('agy').steer_mode, 'resume')  # live unverified until the Task 6 probe
        # Antigravity keeps the checked M5a launch: prompt attached to -p, no stream-json input.
        agy = self.launcher('agy').build(replace(self.req, tool='agy', level='edit'), None, self.root, 'run_123', {}, [])
        self.assertNotIn('--input-format', agy.argv)
        self.assertEqual(agy.argv[-1], '-p=hello')

    def test_control_reply_allow_and_deny_exact_json(self):
        launcher = self.launcher('claude')
        self.assertEqual(launcher.control_reply('r', 'allow', None, {'command': 'x'}), '{"type":"control_response","response":{"subtype":"success","request_id":"r","response":{"behavior":"allow","updatedInput":{"command":"x"}}}}\n')
        self.assertEqual(launcher.control_reply('r', 'deny', 'no', {}), '{"type":"control_response","response":{"subtype":"success","request_id":"r","response":{"behavior":"deny","message":"no"}}}\n')

    def test_permission_event_parsed(self):
        line = json.dumps({'type': 'control_request', 'request_id': 'r', 'request': {'subtype': 'can_use_tool', 'tool_name': 'Bash', 'input': {'command': 'ls'}, 'tool_use_id': 'u', 'description': 'test'}})
        event = parse_line('claude', line)
        self.assertEqual(event.kind, 'permission')
        self.assertEqual(event.request, {'request_id': 'r', 'tool_name': 'Bash', 'input': {'command': 'ls'}, 'tool_use_id': 'u'})

    def test_control_request_inside_text_is_ignored(self):
        fake = {'type': 'control_request', 'request_id': 'r', 'request': {'subtype': 'can_use_tool'}}
        for message in [{'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': json.dumps(fake)}]}}, {'type': 'tool_result', 'content': fake}]:
            with self.subTest(message=message):
                self.assertNotEqual(parse_line('claude', json.dumps(message)).kind, 'permission')

    def test_unknown_control_subtype(self):
        event = parse_line('claude', '{"type":"control_request","request_id":"r","request":{"subtype":"other"}}')
        self.assertEqual(event.kind, 'control_unknown')
        self.assertEqual(event.request['request_id'], 'r')

    def test_codex_resume_argv_keeps_sandbox(self):
        launcher = self.launcher('codex')
        req = replace(self.req, tool='codex', level='read_only')
        launch = launcher.build_resume(req, None, self.root, 'run_123', 'session', 'follow up', {}, [])
        self.assertEqual(launcher.steer_mode, 'resume')
        self.assertEqual(launch.argv[:3], ['wsl', '-d', 'Debian'])
        # Real codex 0.164: `exec resume` has no -s; the sandbox is set through -c, before the id.
        self.assertEqual(launch.argv[-8:], ['codex', 'exec', 'resume', '--json', '-c', 'sandbox_mode="read-only"', 'session', '-'])
        edit = launcher.build_resume(replace(req, level='edit'), None, self.root, 'run_123', 'session', 'x', {}, [])
        self.assertIn('sandbox_mode="workspace-write"', edit.argv)
        auto = launcher.build_resume(replace(req, level='full_auto'), None, self.root / 'worktrees' / 'run_123', 'run_123', 'session', 'x', {}, [])
        self.assertIn('sandbox_workspace_write.network_access=true', auto.argv)
        self.assertNotIn('-s', edit.argv[edit.argv.index('codex'):])
        self.assertEqual(launch.stdin_text, 'follow up')

    def test_opencode_resume_argv(self):
        launcher = self.launcher('opencode')
        req = replace(self.req, tool='opencode', level='edit')
        launch = launcher.build_resume(req, None, self.root, 'run_123', 'session', 'follow up', {}, [])
        self.assertEqual(launcher.steer_mode, 'resume')
        self.assertEqual(launch.argv, ['opencode', 'run', '--format', 'json', '-s', 'session', '-m', 'model', '--', 'follow up'])

    def test_turn_end_events(self):
        for tool, line in [('claude', '{"type":"result"}'), ('agy', '{"event":"result","result":{"status":"SUCCESS"}}'), ('agy', '{"type":"result"}'), ('codex', '{"type":"turn.completed"}')]:
            with self.subTest(tool=tool, line=line):
                self.assertEqual(parse_line(tool, line).kind, 'turn_end')

    def test_ask_level_only_for_can_ask_tools(self):
        for name, launcher in REGISTRY.items():
            with self.subTest(tool=name):
                self.assertEqual('ask' in launcher.levels(), launcher.can_ask)
        tools = {name: launcher.levels() for name, launcher in REGISTRY.items()}
        data = {'tool': 'claude', 'project': 'project', 'prompt': 'hi', 'level': 'ask'}
        self.assertEqual(parse_request(data, RunConfig(), [Project('project', str(self.root))], tools).level, 'ask')
        data['tool'] = 'agy'
        with self.assertRaises(ValueError):
            parse_request(data, RunConfig(), [Project('project', str(self.root))], tools)


class SteerFixTests(unittest.TestCase):
    def test_agy_resume_uses_conversation_and_checked_prompt_flag(self):
        from hq.run.launchers import REGISTRY
        launcher = REGISTRY['agy']
        self.assertEqual(launcher.steer_mode, 'resume')

    def test_claude_deny_without_note_has_message(self):
        import json
        from hq.run.launchers import REGISTRY
        reply = json.loads(REGISTRY['claude'].control_reply('r1', 'deny', None, {}))
        self.assertTrue(reply['response']['response']['message'])
