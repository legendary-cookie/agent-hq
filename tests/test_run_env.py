import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hq.run.env import child_env, resolve_exe


class EnvTests(unittest.TestCase):
    def test_only_allowed_names_kept(self):
        self.assertEqual(child_env({'PATH': 'p', 'FOO': 'x', 'OPENAI_API_KEY': 'x', 'GH_TOKEN': 'x'}, {}, []), {'PATH': 'p'})

    def test_pass_env_named_key_kept(self):
        self.assertEqual(child_env({'OPENROUTER_API_KEY': 'a', 'GH_TOKEN': 'b'}, {}, ['OPENROUTER_API_KEY']), {'OPENROUTER_API_KEY': 'a'})

    def test_extra_wins_and_is_kept(self):
        self.assertEqual(child_env({'PATH': 'a'}, {'PATH': 'b', 'CODEX_HOME': 'c'}, []), {'PATH': 'b', 'CODEX_HOME': 'c'})

    def test_secret_like_extra_refused(self):
        with self.assertRaises(ValueError): child_env({}, {'X_SECRET': '1'}, [])

    def executable(self, directory):
        path = directory / ('fake_hq_tool.cmd' if os.name == 'nt' else 'fake_hq_tool')
        path.write_text('fake')
        path.chmod(0o755)
        return path

    def test_exe_in_cwd_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); exe = self.executable(root); old = Path.cwd()
            try:
                os.chdir(root)
                with patch.dict(os.environ, {'PATH': str(root)}): self.assertIsNone(resolve_exe(exe.name, []))
            finally:
                os.chdir(old)

    def test_exe_in_project_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); bin_dir = root / 'bin'; bin_dir.mkdir(); exe = self.executable(bin_dir)
            with patch.dict(os.environ, {'PATH': str(bin_dir)}): self.assertIsNone(resolve_exe(exe.name, [root]))

    def test_normal_exe_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); exe = self.executable(root)
            with patch.dict(os.environ, {'PATH': str(root)}): self.assertEqual(resolve_exe(exe.name, []), exe)


class WindowsStartupNamesTests(unittest.TestCase):
    def test_tool_startup_names_kept_secrets_dropped(self):
        base = {'APPDATA': 'a', 'LOCALAPPDATA': 'b', 'PATHEXT': '.EXE;.CMD', 'COMSPEC': 'c',
                'XDG_CONFIG_HOME': 'x', 'NPM_TOKEN': 'n', 'AWS_SECRET_ACCESS_KEY': 's'}
        env = child_env(base, {}, [])
        for name in ('APPDATA', 'LOCALAPPDATA', 'PATHEXT', 'COMSPEC', 'XDG_CONFIG_HOME'):
            self.assertIn(name, env)
        self.assertNotIn('NPM_TOKEN', env)
        self.assertNotIn('AWS_SECRET_ACCESS_KEY', env)
