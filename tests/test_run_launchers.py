import tempfile
import unittest
from dataclasses import replace
from pathlib import Path, PureWindowsPath
from unittest.mock import patch

from hq.config import Profile
from hq.run.request import RunRequest
from hq.run.launchers import REGISTRY, available
from hq.run.wsl import to_wsl_path, wsl_stop


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # resolve(): Windows TEMP may be an 8.3 short path (JANEDO~1); the resolver returns long paths.
        self.root = Path(self.tmp.name).resolve()
        self.worktrees = self.root / 'worktrees'
        self.workdir = self.worktrees / 'run_123'
        self.req = RunRequest('claude', 'model', 'project', 'hello', 'edit', 'block', None, 30)

    def build(self, tool, level='edit', workdir=None, profile=None, prompt='hello'):
        launcher = type(REGISTRY[tool])(self.worktrees)
        req = replace(self.req, tool=tool, level=level, prompt=prompt)
        return launcher.build(req, profile, workdir or self.workdir, 'run_123', {'PATH': 'bin', 'SECRET': 'no'}, [])

    def test_claude_argv_read_only(self):
        self.assertEqual(self.build('claude', 'read_only').argv, ['claude', '-p', '--input-format', 'stream-json', '--output-format', 'stream-json', '--verbose', '--permission-mode', 'plan', '--model', 'model'])

    def test_claude_argv_edit(self):
        self.assertIn('acceptEdits', self.build('claude').argv)

    def test_claude_full_auto_requires_worktree(self):
        with self.assertRaises(ValueError): self.build('claude', 'full_auto', self.root)
        self.assertIn('--dangerously-skip-permissions', self.build('claude', 'full_auto').argv)

    def test_codex_argv_and_wslenv(self):
        profile = Profile('p', 'codex', '/profiles/codex', 'a')
        launch = self.build('codex', 'full_auto', profile=profile)
        self.assertEqual(launch.argv[:4], ['wsl', '-d', 'Debian', '--cd'])
        self.assertEqual(launch.argv[5:7], ['--exec', 'bash'])
        self.assertEqual(launch.argv[-10:], ['codex', 'exec', '--json', '-m', 'model', '-s', 'workspace-write', '-c', 'sandbox_workspace_write.network_access=true', '-'])
        self.assertEqual(launch.argv[-1], '-')
        self.assertIn('sandbox_workspace_write.network_access=true', launch.argv)
        self.assertEqual(launch.env['CODEX_HOME'], '/profiles/codex')
        self.assertEqual(launch.env['WSLENV'], 'CODEX_HOME/u')
        self.assertEqual(launch.wsl_pidfile, '/tmp/hq-runs/run_123.pid')

    def test_codex_prompt_on_stdin(self):
        self.assertEqual(self.build('codex').stdin_text, 'hello')

    def test_wsl_path_with_space(self):
        self.assertEqual(to_wsl_path(PureWindowsPath(r'C:\Users\Jane Doe\ao')), '/mnt/c/Users/Jane Doe/ao')

    def test_agy_levels(self):
        self.assertEqual(REGISTRY['agy'].levels(), {'edit', 'full_auto'})
        launch = self.build('agy')
        self.assertIn('--input-format', launch.argv)
        self.assertIsNone(launch.stdin_text)

    def test_opencode_levels_no_full_auto(self):
        self.assertEqual(REGISTRY['opencode'].levels(), {'ask', 'edit'})
        self.assertEqual(self.build('opencode').argv, ['opencode', 'acp', '--cwd', str(self.workdir)])

    def test_kimi_yolo_only_in_worktree(self):
        with self.assertRaises(ValueError): self.build('kimi', 'full_auto', self.root)
        self.assertIn('-y', self.build('kimi', 'full_auto').argv)

    def test_gemini_full_auto_uses_sandbox(self):
        self.assertEqual(self.build('gemini', 'full_auto').argv, ['gemini', '--skip-trust', '-m', 'model', '--sandbox', '--yolo', '--prompt=hello'])

    def test_dsh_levels(self):
        self.assertEqual(REGISTRY['dsh'].levels(), {'edit'})
        self.assertEqual(self.build('dsh').argv, ['dsh', 'headless', '--json', '-'])

    def test_hermes_disabled_because_in_is_ignored(self):
        from hq.run.launchers import available
        self.assertEqual(REGISTRY['hermes'].levels(), set())
        self.assertIn('--in is ignored', REGISTRY['hermes'].disabled_reason)
        self.assertNotIn('hermes', available([]))
        with self.assertRaises(ValueError):
            self.build('hermes')

    def test_only_real_checked_tools_are_tested(self):
        self.assertEqual({n for n, l in REGISTRY.items() if l.tested}, {'claude', 'codex', 'opencode', 'agy'})

    def test_prompt_is_inert(self):
        prompt = '--dangerously-skip-permissions; rm -rf / $(x)\n-y'
        for tool, launcher in REGISTRY.items():
            if not launcher.levels():
                continue  # disabled launcher (Hermes)
            with self.subTest(tool=tool):
                level = 'edit' if 'edit' in launcher.levels() else 'read_only'
                launch = self.build(tool, level, prompt=prompt)
                occurrences = [arg for arg in launch.argv if prompt in arg]
                if tool in {'kimi', 'gemini'}:
                    flag = '-p=' if tool == 'agy' else '--prompt='
                    self.assertEqual(occurrences, [flag + prompt])
                    self.assertIsNone(launch.stdin_text)
                elif tool in {'opencode', 'agy'}:
                    self.assertEqual(occurrences, [])
                    self.assertIsNone(launch.stdin_text)
                elif tool == 'claude':
                    import json as _json
                    self.assertEqual(occurrences, [])
                    self.assertEqual(_json.loads(launch.stdin_text)['message']['content'], prompt)
                else:
                    self.assertEqual(occurrences, [])
                    self.assertEqual(launch.stdin_text, prompt)
                self.assertNotIn('SECRET', launch.env)

    def test_every_launcher_starts_with_bare_tool_name(self):
        for tool, launcher in REGISTRY.items():
            if not launcher.levels():
                continue  # disabled launcher (Hermes)
            with self.subTest(tool=tool):
                level = 'edit' if 'edit' in launcher.levels() else 'read_only'
                self.assertEqual(self.build(tool, level).argv[0], 'wsl' if tool == 'codex' else tool)

    def test_dsh_prompt_on_stdin(self):
        self.assertEqual(self.build('dsh').stdin_text, 'hello')
        self.assertFalse(REGISTRY['dsh'].links_by_stream)

    def test_shim_exe_and_final_argv(self):
        from hq.run.winexe import resolve_command, final_argv
        shim = self.root / 'opencode.CMD'
        target = self.root / 'node_modules' / 'opencode-ai' / 'bin' / 'opencode.exe'
        target.parent.mkdir(parents=True)
        target.touch()
        shim.write_text('@ECHO OFF\n"%dp0%\\node_modules\\opencode-ai\\bin\\opencode.exe"   %*\n')
        self.assertEqual(resolve_command(shim), [str(target)])
        prompt = 'hello & world\n-x'
        launch = self.build('opencode', prompt=prompt)
        self.assertEqual(final_argv(launch, shim), [str(target), *launch.argv[1:]])
        self.assertEqual(final_argv(launch, shim)[-1], str(self.workdir))

    def test_node_shim(self):
        from hq.run.winexe import resolve_command
        shim = self.root / 'gemini.cmd'
        target = self.root / 'node_modules' / '@google' / 'gemini-cli' / 'dist' / 'index.js'
        target.parent.mkdir(parents=True)
        target.touch()
        node = self.root / 'node.exe'
        node.touch()
        shim.write_text('"%_prog%"  "%dp0%\\node_modules\\@google\\gemini-cli\\dist\\index.js" %*\n')
        self.assertEqual(resolve_command(shim), [str(node), str(target)])
        node.unlink()
        with patch('hq.run.winexe.shutil.which', return_value=str(self.root / 'external-node')):
            self.assertEqual(resolve_command(shim), [str(self.root / 'external-node'), str(target)])

    def test_real_npm_shim_last_line(self):
        # Shape of the last line of a real npm 10 shim (gemini.CMD / dsh.CMD on Windows).
        from hq.run.winexe import resolve_command
        shim = self.root / 'dsh.CMD'
        target = self.root / 'node_modules' / '@deepseek-ai' / 'dsh' / 'lib' / 'bin.js'
        target.parent.mkdir(parents=True)
        target.touch()
        node = self.root / 'node.exe'
        node.touch()
        shim.write_text('@ECHO off\nGOTO start\n:start\nSETLOCAL\n)\n\n'
                        'endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & '
                        '"%_prog%"  "%dp0%\\node_modules\\@deepseek-ai\\dsh\\lib\\bin.js" %*\n')
        self.assertEqual(resolve_command(shim), [str(node), str(target)])

    def test_shim_with_trailing_command_refused(self):
        from hq.run.winexe import resolve_command
        shim = self.root / 'x.cmd'
        target = self.root / 'node_modules' / 'x' / 'x.exe'
        target.parent.mkdir(parents=True)
        target.touch()
        shim.write_text('"%dp0%\\node_modules\\x\\x.exe" %* & calc.exe\n')
        with self.assertRaises(ValueError):
            resolve_command(shim)

    def test_shim_rejects_escape(self):
        from hq.run.winexe import resolve_command
        shim_dir = self.root / 'shims'
        shim_dir.mkdir()
        (self.root / 'outside.exe').touch()
        shim = shim_dir / 'bad.bat'
        shim.write_text('"%dp0%\\..\\outside.exe" %*\n')
        with self.assertRaises(ValueError):
            resolve_command(shim)

    def test_non_shim_and_wsl_unchanged(self):
        from hq.run.winexe import resolve_command, final_argv
        exe = self.root / 'tool.exe'
        self.assertEqual(resolve_command(exe), [str(exe)])
        launch = self.build('codex')
        self.assertEqual(final_argv(launch, exe), launch.argv)

    def test_batch_args_reject_metacharacters(self):
        from hq.run.winexe import check_batch_args
        check_batch_args(['tool.cmd', r'abc-._:/=\@+123'])
        for char in ['&', '|', '%', '"', ' ', '\n']:
            with self.subTest(char=char), self.assertRaises(ValueError):
                check_batch_args(['tool.cmd', 'a' + char + 'b'])

    def test_profile_var_set(self):
        launch = self.build('claude', profile=Profile('p', 'claude', str(self.root / 'profile'), 'a'))
        self.assertEqual(launch.env['CLAUDE_CONFIG_DIR'], str(self.root / 'profile'))

    def test_available_skips_missing_exe(self):
        with patch('hq.run.launchers.resolve_exe', side_effect=lambda name, dirs: Path('bin') if name == 'wsl' else None):
            self.assertEqual(set(available([])), {'codex'})

    def test_wsl_stop_signals_process_group(self):
        from subprocess import CompletedProcess
        with patch('hq.run.wsl.subprocess.run', side_effect=[CompletedProcess([], 0, '123\n'), CompletedProcess([], 0), CompletedProcess([], 0)]) as run, patch('hq.run.wsl.time.sleep') as sleep:
            wsl_stop('/tmp/hq-runs/run_123.pid')
        self.assertEqual(run.call_args_list[0].args[0], ['wsl', '-d', 'Debian', '--exec', 'cat', '/tmp/hq-runs/run_123.pid'])
        self.assertEqual(run.call_args_list[1].args[0], ['wsl', '-d', 'Debian', '--exec', '/bin/kill', '-TERM', '--', '-123'])
        self.assertEqual(run.call_args_list[2].args[0][-3:], ['-KILL', '--', '-123'])
        sleep.assert_called_once_with(3)

    def test_wsl_stop_rejects_invalid_pid(self):
        from subprocess import CompletedProcess
        with patch('hq.run.wsl.subprocess.run', return_value=CompletedProcess([], 0, '-1')) as run:
            wsl_stop('/tmp/hq-runs/run_123.pid')
        self.assertEqual(run.call_count, 1)

    def test_unoffered_levels_rejected(self):
        for tool, launcher in REGISTRY.items():
            for level in {'read_only', 'edit', 'full_auto'} - launcher.levels():
                with self.subTest(tool=tool, level=level), self.assertRaises(ValueError):
                    self.build(tool, level)

    def test_forbidden_profile_and_worktree_escape(self):
        with self.assertRaises(ValueError): self.build('claude', profile=Profile('p', 'claude', str(self.root / 'secrets'), 'a'))
        with self.assertRaises(ValueError): self.build('agy', 'full_auto', self.worktrees / '..' / 'outside')
