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
        self.assertTrue(parse_line('claude', '{"type":"error","message":"429 rate limit"}').rate_limited)

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
        self.assertTrue(parse_line('claude', line).rate_limited)

    def test_codex_turn_failed_with_429(self):
        line = '{"type":"turn.failed","error":{"message":"stream error: 429 Too Many Requests"}}'
        self.assertTrue(parse_line('codex', line).rate_limited)
