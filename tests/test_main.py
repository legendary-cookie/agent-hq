import subprocess, sys, tempfile, unittest
from pathlib import Path

class MainTests(unittest.TestCase):
    def test_once_with_fixture_config_exits_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=subprocess.run([sys.executable,'-m','hq','--once','--config','tests/fixtures/config','--db',str(Path(tmp)/'db')],capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            for name in ('claude_code','codex','opencode','antigravity','gemini_cli','provider_usage'): self.assertIn(name,p.stdout)

    def test_token_reused_across_launches(self):
        from hq.__main__ import load_token
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'token'
            token = 'a' * 32
            path.write_text(token, encoding='utf-8')
            self.assertEqual(load_token(path), token)
            self.assertEqual(load_token(path), token)

    def test_short_token_file_replaced(self):
        from hq.__main__ import load_token
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'token'
            path.write_text('short', encoding='utf-8')
            token = load_token(path)
            self.assertGreaterEqual(len(token), 32)
            self.assertEqual(path.read_text(encoding='utf-8'), token)
