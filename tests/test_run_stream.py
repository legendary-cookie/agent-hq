import unittest
from hq.run.stream import parse_line


class StreamTests(unittest.TestCase):
    def test_claude_init_gives_session(self):
        self.assertEqual(parse_line('claude', '{"type":"system","subtype":"init","session_id":"s"}').session_id, 's')

    def test_codex_thread_started_gives_session(self):
        self.assertEqual(parse_line('codex', '{"type":"thread.started","thread_id":"s"}').session_id, 's')

    def test_kimi_session_event(self):
        self.assertEqual(parse_line('kimi', '{"type":"session","session_id":"s"}').session_id, 's')

    def test_non_json_is_none(self):
        self.assertIsNone(parse_line('claude', 'garbage'))

    def test_rate_limit_detected(self):
        self.assertTrue(parse_line('claude', '{"type":"error","message":"429 rate limit"}').rate_limited)
