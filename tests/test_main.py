import subprocess, sys, tempfile, unittest
from pathlib import Path

class MainTests(unittest.TestCase):
    def test_once_with_fixture_config_exits_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=subprocess.run([sys.executable,'-m','hq','--once','--config','tests/fixtures/config','--db',str(Path(tmp)/'db')],capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            for name in ('claude_code','codex','opencode','antigravity','gemini_cli','provider_usage'): self.assertIn(name,p.stdout)
