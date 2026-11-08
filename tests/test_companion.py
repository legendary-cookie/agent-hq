import unittest
from hq.server import companion_url


class CompanionUrlTests(unittest.TestCase):
    def test_only_web_urls_without_embedded_credentials(self):
        for value in ('', 'javascript:alert(1)', '//example.com', 'https://user:password@example.com', 'https://['):
            self.assertIsNone(companion_url(value))
        for value in ('http://127.0.0.1:8700', 'https://chat.example.com'):
            self.assertEqual(companion_url(value), value)
