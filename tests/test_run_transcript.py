import json
import unittest

from hq.run.transcript import text_of


def line(obj):
    return json.dumps(obj)


class TranscriptTests(unittest.TestCase):
    # Real stream shapes (2026-11-03/04); text invented. Found in the real goal check: the planner's
    # fenced JSON sat in an earlier Claude assistant message, escaped inside stream JSON.
    def test_claude_assistant_text_blocks_and_result(self):
        lines = [line({'type': 'system', 'subtype': 'init', 'session_id': 's'}),
                 line({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'Plan:\n```json\n{"steps": []}\n```'},
                                                                     {'type': 'tool_use', 'name': 'Read', 'input': {}}]}}),
                 line({'type': 'result', 'result': 'The JSON block is above.'})]
        text = text_of('claude', lines)
        self.assertIn('```json\n{"steps": []}\n```', text)
        self.assertIn('The JSON block is above.', text)

    def test_codex_agent_messages(self):
        lines = [line({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '```json\n{"a": 1}\n```'}})]
        self.assertIn('{"a": 1}', text_of('codex', lines))

    def test_opencode_text_parts(self):
        lines = [line({'type': 'text', 'part': {'text': 'hello'}})]
        self.assertEqual(text_of('opencode', lines).strip(), 'hello')

    def test_agy_result_response(self):
        lines = [line({'event': 'result', 'result': {'status': 'SUCCESS', 'response': 'BETA\n'}})]
        self.assertIn('BETA', text_of('agy', lines))

    def test_system_hook_and_stderr_lines_ignored(self):
        lines = [line({'type': 'system', 'subtype': 'hook_response', 'output': '```json\n{"evil": 1}\n```'}),
                 '[stderr] ```json\n{"evil": 2}\n```', '[agent-hq] turn 2', 'not json']
        self.assertNotIn('evil', text_of('claude', lines))
