import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hq.control import memory as memory_module
from hq.control.audit import ControlLog
from hq.control.memory import Memory
from hq.control.pve import ControlError


class FakePve:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def python_as_agent(self, host, script, data, timeout=60):
        self.calls.append((host, data))
        return json.dumps(self.answer)


class MemoryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = ControlLog(Path(tmp.name) / 'log.jsonl')

    def test_paths_are_checked_before_anything_runs(self):
        pve = FakePve({'files': []})
        memory = Memory(pve, self.log)
        for bad in ('../etc/passwd', '/etc/passwd', 'a/../../b', 'x;rm', '', '.git/config', 'a/.hidden/b.md', 'notes'):
            with self.assertRaises(ValueError, msg=bad):
                memory.read('h', 'memory', bad)
        with self.assertRaises(ValueError):
            memory.files('h', 'home')
        with self.assertRaises(ValueError):
            memory.write('h', 'memory', 'state.bin', 'x')
        with self.assertRaises(ValueError):
            memory.version('h', 'memory', 'state.md', 'HEAD~1')
        self.assertEqual(pve.calls, [])

    def test_errors_from_inside_become_control_errors(self):
        with self.assertRaisesRegex(ControlError, 'no Zylos memory'):
            Memory(FakePve({'error': 'this agent has no Zylos memory folder'}), self.log).files('h', 'memory')

    def test_saves_are_logged_without_the_text(self):
        memory = Memory(FakePve({'saved': True, 'created': False}), self.log)
        memory.write('h', 'skills', 'daily-report/SKILL.md', 'secret plan text')
        entry = self.log.recent()[0]
        self.assertEqual((entry['action'], entry['path'], entry['chars']), ('skill_edited', 'daily-report/SKILL.md', 16))
        self.assertNotIn('secret', json.dumps(entry))


@unittest.skipUnless(os.name == 'posix', 'the script runs inside Linux containers')
class InsideScriptTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.mem = self.home / 'zylos/memory'
        (self.mem / 'reference').mkdir(parents=True)
        (self.mem / 'identity.md').write_text('# Me\n')
        (self.mem / 'reference/company.md').write_text('Company\n')
        (self.home / '.claude/skills/report').mkdir(parents=True)
        (self.home / '.claude/skills/report/SKILL.md').write_text('v1\n')
        git = ['git', '-C', str(self.mem), '-c', 'user.email=t@t', '-c', 'user.name=t']
        if shutil.which('git'):
            subprocess.run(['git', 'init', '-q', str(self.mem)], check=True)
            subprocess.run(git + ['add', '-A'], check=True)
            subprocess.run(git + ['commit', '-qm', 'start'], check=True)

    def run_script(self, request, env=None):
        script = memory_module.SCRIPT.replace('home = pwd.getpwuid(os.getuid()).pw_dir', f'home = {str(self.home)!r}')
        data = {**request, 'endings': list(memory_module.TEXT_ENDINGS), 'max': memory_module.MAX_TEXT}
        out = subprocess.run([sys.executable, '-c', script], input=json.dumps(data), capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout.strip().splitlines()[-1])

    def test_list_read_write_and_history(self):
        files = self.run_script({'op': 'list', 'root': 'memory'})['files']
        self.assertEqual(sorted(f['path'] for f in files), ['identity.md', 'reference/company.md'])
        read = self.run_script({'op': 'read', 'root': 'memory', 'path': 'identity.md'})
        self.assertEqual(read['text'], '# Me\n')
        saved = self.run_script({'op': 'write', 'root': 'memory', 'path': 'identity.md', 'text': '# Me, edited\n', 'expect': read['modified']})
        self.assertTrue(saved['saved'])
        self.assertEqual((self.mem / 'identity.md').read_text(), '# Me, edited\n')
        history = self.run_script({'op': 'read', 'root': 'memory', 'path': 'identity.md'})['history']
        if (self.mem / '.git').is_dir():
            self.assertEqual(history[0]['label'], 'Edited in HQ: identity.md')
            old = self.run_script({'op': 'version', 'root': 'memory', 'path': 'identity.md', 'id': history[1]['id']})
            self.assertEqual(old['text'], '# Me\n')

    def test_skill_saves_keep_the_old_text(self):
        self.run_script({'op': 'write', 'root': 'skills', 'path': 'report/SKILL.md', 'text': 'v2\n'})
        read = self.run_script({'op': 'read', 'root': 'skills', 'path': 'report/SKILL.md'})
        self.assertEqual(read['text'], 'v2\n')
        old = self.run_script({'op': 'version', 'root': 'skills', 'path': 'report/SKILL.md', 'id': read['history'][0]['id']})
        self.assertEqual(old['text'], 'v1\n')

    def test_a_stale_save_is_refused(self):
        answer = self.run_script({'op': 'write', 'root': 'memory', 'path': 'identity.md', 'text': 'x', 'expect': 1.0})
        self.assertIn('changed since you opened it', answer['error'])

    def test_two_quick_skill_saves_keep_both_old_texts(self):
        self.run_script({'op': 'write', 'root': 'skills', 'path': 'report/SKILL.md', 'text': 'v2\n'})
        self.run_script({'op': 'write', 'root': 'skills', 'path': 'report/SKILL.md', 'text': 'v3\n'})
        history = self.run_script({'op': 'read', 'root': 'skills', 'path': 'report/SKILL.md'})['history']
        texts = [self.run_script({'op': 'version', 'root': 'skills', 'path': 'report/SKILL.md', 'id': v['id']})['text'] for v in history]
        self.assertEqual(sorted(texts), ['v1\n', 'v2\n'])

    def test_creating_never_overwrites(self):
        answer = self.run_script({'op': 'write', 'root': 'memory', 'path': 'identity.md', 'text': 'x', 'create': True})
        self.assertIn('already exists', answer['error'])
        self.assertEqual((self.mem / 'identity.md').read_text(), '# Me\n')

    def test_a_git_folder_without_git_installed_still_reads_and_saves(self):
        (self.mem / '.git').mkdir(exist_ok=True)
        no_git = {'PATH': str(self.home / 'empty-bin'), 'HOME': str(self.home)}
        read = self.run_script({'op': 'read', 'root': 'memory', 'path': 'identity.md'}, env=no_git)
        self.assertEqual((read['text'], read['history']), ('# Me\n', []))
        saved = self.run_script({'op': 'write', 'root': 'memory', 'path': 'identity.md', 'text': 'new\n'}, env=no_git)
        self.assertEqual((saved['saved'], saved['committed']), (True, False))

    def test_dot_folders_are_off_limits(self):
        self.assertIn('error', self.run_script({'op': 'read', 'root': 'memory', 'path': '.git/config'}))
    def test_links_and_escapes_are_refused(self):
        (self.mem / 'link.md').symlink_to(self.home / 'outside.md')
        (self.home / 'outside.md').write_text('outside')
        self.assertIn('error', self.run_script({'op': 'read', 'root': 'memory', 'path': 'link.md'}))
        self.assertIn('error', self.run_script({'op': 'write', 'root': 'memory', 'path': 'link.md', 'text': 'x'}))
        self.assertIn('error', self.run_script({'op': 'read', 'root': 'memory', 'path': '../outside.md'}))
        self.assertEqual((self.home / 'outside.md').read_text(), 'outside')


if __name__ == '__main__':
    unittest.main()
