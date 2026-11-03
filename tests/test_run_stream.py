import unittest
from hq.run.stream import parse_line


class StreamTests(unittest.TestCase):
    # Session ids come back in the reader's format, '<harness>:<id>', so a run joins its session.
    def test_claude_init_gives_session(self):
        self.assertEqual(parse_line('claude', '{"type":"system","subtype":"init","session_id":"s"}').session_id,
                         'claude_code:s')

    def test_codex_thread_started_gives_session(self):
        self.assertEqual(parse_line('codex', '{"type":"thread.started","thread_id":"s"}').session_id, 'codex:s')

    def test_kimi_session_event(self):
        self.assertEqual(parse_line('kimi', '{"type":"session","session_id":"s"}').session_id, 'kimi:s')

    def test_agy_session_uses_reader_prefix(self):
        self.assertEqual(parse_line('agy', '{"type":"system","subtype":"init","session_id":"s"}').session_id,
                         'antigravity:s')

    def test_non_json_is_none(self):
        self.assertIsNone(parse_line('claude', 'garbage'))

    def test_rate_limit_detected(self):
        self.assertFalse(parse_line('claude', '{"type":"error","message":"failed to read docs/quota.md"}').rate_limited)

    def test_claude_rejected_rate_limit_event(self):
        line = '{"type":"rate_limit_event","rate_limit_info":{"status":"rejected","resetsAt":1793700000}}'
        self.assertTrue(parse_line('claude', line).rate_limited)

    def test_claude_allowed_rate_limit_event_is_not_a_limit(self):
        # Claude Code prints this on every run; it is informational.
        line = '{"type":"rate_limit_event","rate_limit_info":{"status":"allowed","rateLimitType":"five_hour"}}'
        self.assertFalse(parse_line('claude', line).rate_limited)

    def test_agent_talking_about_rate_limits_is_not_a_limit(self):
        line = ('{"type":"assistant","message":{"content":[{"type":"text",'
                '"text":"This code retries on HTTP 429 rate limit and quota errors."}]}}')
        self.assertFalse(parse_line('claude', line).rate_limited)

    def test_successful_result_mentioning_quota_is_not_a_limit(self):
        line = '{"type":"result","subtype":"success","is_error":false,"result":"Added a quota check."}'
        self.assertFalse(parse_line('claude', line).rate_limited)

    def test_error_result_with_limit_text_is_a_limit(self):
        line = '{"type":"result","subtype":"error_during_execution","is_error":true,"result":"usage limit reached"}'
        self.assertFalse(parse_line('claude', line).rate_limited)

    def test_codex_turn_failed_with_429(self):
        line = '{"type":"turn.failed","error":{"code":429}}'
        self.assertTrue(parse_line('codex', line).rate_limited)


    def test_structured_limits(self):
        cases = [('claude', '{"type":"result","is_error":true,"api_error_status":429}'),
                 ('codex', '{"type":"error","error":{"code":"usage_limit_reached"}}'),
                 ('codex', "{\"type\":\"error\",\"error\":{\"message\":\"You've hit your usage limit today\"}}"),
                 ('agy', '{"event":"result","result":{"status":"RATE_LIMITED"}}'),
                 ('agy', '{"event":"result","result":{"error":{"code":"RESOURCE_EXHAUSTED"}}}'),
                 ('kimi', '{"type":"error","status":429}')]
        for tool, line in cases:
            with self.subTest(tool=tool, line=line):
                self.assertTrue(parse_line(tool, line).rate_limited)

class RealShapeTests(unittest.TestCase):
    # Shapes from real runs on this PC (2026-11-03); ids and values invented.
    def test_agy_init_event_gives_conversation(self):
        line = '{"event":"init","conversation_id":"c-1","init":{"cwd":"x","permission_mode":"request-review"}}'
        self.assertEqual(parse_line('agy', line).session_id, 'antigravity:c-1')

    def test_agy_result_success_is_done(self):
        line = '{"event":"result","result":{"conversation_id":"c-1","status":"SUCCESS","num_turns":1}}'
        self.assertEqual(parse_line('agy', line).kind, 'turn_end')

    def test_agy_result_failure_is_error(self):
        line = '{"event":"result","result":{"conversation_id":"c-1","status":"RATE_LIMITED"}}'
        event = parse_line('agy', line)
        self.assertEqual(event.kind, 'error')
        self.assertTrue(event.rate_limited)

    def test_opencode_step_event_gives_session(self):
        line = '{"type":"step_finish","sessionID":"ses_1","part":{"reason":"stop"}}'
        self.assertEqual(parse_line('opencode', line).session_id, 'opencode:ses_1')
