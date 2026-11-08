import getpass
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hq.control import settings as settings_module
from hq.control.audit import ControlLog
from hq.control.pve import ControlError
from hq.control.settings import Settings


class FakePve:
    def __init__(self):
        self.agents = {'ct310-intern': {'vmid': 310, 'login': 'zylos'}}
        self.calls, self.stdin = [], []

    def agent(self, host):
        if host not in self.agents:
            raise ControlError('HQ can only control the agents it watches')
        return self.agents[host]

    def run(self, words, timeout=60, stdin=None):
        self.calls.append(' '.join(map(str, words)))
        if stdin is not None:
            self.stdin.append(json.loads(stdin))
        if words[:2] == ['pct', 'config']:
            return 'cores: 2\nmemory: 2048\nswap: 512\nonboot: 1\n'
        if words[-2:-1] == ['-c'] and 'list-unit-files' in str(words[-1]):
            return json.dumps({'login': 'zylos', 'claude': {'model': '', 'auto_updates': False}, 'services': [], 'timezone': 'Asia/Singapore', 'identity': '# Me'})
        return json.dumps({'done': ['ok']})

    def forget(self):
        pass


class SettingsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = ControlLog(Path(tmp.name) / 'log.jsonl')
        self.pve = FakePve()
        self.settings = Settings(self.pve, self.log)

    def test_scripts_are_valid_python(self):
        compile(settings_module.READ, 'read', 'exec')
        compile(settings_module.WRITE, 'write', 'exec')

    def test_read_combines_container_and_inside(self):
        found = self.settings.read('ct310-intern')
        self.assertEqual(found['container'], {'cores': 2, 'memory': 2048, 'swap': 512, 'onboot': True})
        self.assertEqual((found['timezone'], found['claude']['auto_updates']), ('Asia/Singapore', False))

    def test_container_size_uses_pct_set(self):
        self.settings.apply('ct310-intern', 'container', {'cores': 4, 'memory': 4096, 'swap': 1024, 'onboot': False})
        self.assertIn('pct set 310 --cores 4 --memory 4096 --swap 1024 --onboot 0', self.pve.calls)
        for bad in ({'cores': 99, 'memory': 4096, 'swap': 0, 'onboot': True}, {'cores': 2, 'memory': 100, 'swap': 0, 'onboot': True}, {'cores': '2; rm', 'memory': 4096, 'swap': 0, 'onboot': True}):
            with self.assertRaises(ValueError):
                self.settings.apply('ct310-intern', 'container', bad)

    def test_values_travel_on_stdin_only(self):
        self.settings.apply('ct310-intern', 'identity', {'text': "# I am Nadia's agent; $(rm -rf /)"})
        self.assertEqual(self.pve.stdin[-1], {'section': 'identity', 'text': "# I am Nadia's agent; $(rm -rf /)", 'login': 'zylos'})
        self.assertFalse(any('rm -rf' in c for c in self.pve.calls))

    def test_telegram_validation(self):
        good = {'enabled': True, 'owner_chat_id': '123456789', 'owner_username': '@nadia_y', 'dm_policy': 'allowlist', 'group_policy': 'allowlist', 'dm_allow': ['123456789'], 'groups': ['-1003891780982']}
        self.settings.apply('ct310-intern', 'telegram', good)
        self.assertEqual(self.pve.stdin[-1]['owner_username'], 'nadia_y')
        for change in ({'dm_policy': 'everyone'}, {'dm_allow': ['abc']}, {'owner_chat_id': '12'}, {'groups': ['x']}, {'enabled': 'yes'}):
            with self.assertRaises(ValueError, msg=change):
                self.settings.apply('ct310-intern', 'telegram', {**good, **change})

    def test_services_only_zylos_units_and_known_actions(self):
        self.settings.apply('ct310-intern', 'service', {'name': 'zylos-hxa-connect', 'action': 'disable'})
        self.assertEqual(self.pve.stdin[-1]['verbs'], ['stop', 'disable'])
        for bad in ({'name': 'ssh', 'action': 'stop'}, {'name': 'zylos-x;reboot', 'action': 'stop'}, {'name': 'zylos-telegram', 'action': 'mask'}):
            with self.assertRaises(ValueError):
                self.settings.apply('ct310-intern', 'service', bad)

    def test_full_model_ids_are_kept(self):
        self.settings.apply('ct310-intern', 'claude', {'model': 'claude-opus-5-5', 'auto_updates': False})
        self.assertEqual(self.pve.stdin[-1]['model'], 'claude-opus-5-5')
        with self.assertRaises(ValueError):
            self.settings.apply('ct310-intern', 'claude', {'model': 'claude-x; rm -rf /', 'auto_updates': False})

    def test_other_checks(self):
        with self.assertRaises(ValueError):
            self.settings.apply('ct310-intern', 'claude', {'model': 'gpt-5', 'auto_updates': True})
        with self.assertRaises(ValueError):
            self.settings.apply('ct310-intern', 'timezone', {'timezone': 'Asia/Singapore; reboot'})
        with self.assertRaises(ValueError):
            self.settings.apply('ct310-intern', 'nonsense', {})
        with self.assertRaises(ControlError):
            self.settings.apply('ct999-x', 'timezone', {'timezone': 'UTC'})

    def test_the_agents_real_login_is_passed_in_not_guessed(self):
        self.pve.agents['ct203-manager'] = {'vmid': 203, 'login': 'agentuser'}
        self.settings.read('ct203-manager')
        self.assertEqual(self.pve.stdin[-1], {'login': 'agentuser'})
        self.settings.apply('ct203-manager', 'timezone', {'timezone': 'UTC'})
        self.assertEqual(self.pve.stdin[-1]['login'], 'agentuser')
        self.assertNotIn("'stat', '-c'", settings_module.READ + settings_module.WRITE)

    def test_changes_are_logged(self):
        self.settings.apply('ct310-intern', 'service', {'name': 'zylos-telegram', 'action': 'restart'})
        entry = self.log.recent()[0]
        self.assertEqual((entry['action'], entry['section'], entry['service_action']), ('settings_changed', 'service', 'restart'))


if __name__ == '__main__':
    unittest.main()


@unittest.skipUnless(os.name == 'posix', 'the scripts run inside Linux containers')
class InsideScriptTests(unittest.TestCase):
    """Run the real write script against a temporary copy of an agent's folders."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = self.root / 'home'
        (self.home / '.claude').mkdir(parents=True)
        (self.root / 'opt/zylos/memory').mkdir(parents=True)
        self.secret = self.root / 'secret'
        self.secret.write_text('root only')

    def run_write(self, change):
        script = (settings_module.WRITE
                  .replace("os.path.expanduser('~' + login)", repr(str(self.home)))
                  .replace('/opt/zylos/', str(self.root / 'opt/zylos') + '/')
                  .replace("pwd.getpwnam(login)", "pwd.getpwuid(os.getuid())"))
        stdin = json.dumps({**change, 'login': getpass.getuser()})
        return subprocess.run([sys.executable, '-c', script], input=stdin, capture_output=True, text=True)

    def test_saves_claude_settings_and_keeps_a_backup(self):
        (self.home / '.claude/settings.json').write_text('{"model": "opus"}')
        result = self.run_write({'section': 'claude', 'model': 'sonnet', 'auto_updates': True})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.home / '.claude/settings.json').read_text())['model'], 'sonnet')
        self.assertEqual(len(list((self.home / '.claude').glob('settings.json.bak-hq-*'))), 1)

    def test_a_planted_link_is_refused_not_followed(self):
        identity = self.root / 'opt/zylos/memory/identity.md'
        identity.symlink_to(self.secret)
        result = self.run_write({'section': 'identity', 'text': 'overwritten'})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('refused', result.stderr)
        self.assertEqual(self.secret.read_text(), 'root only')

    def test_a_linked_folder_is_refused(self):
        os.rmdir(self.home / '.claude')
        (self.root / 'elsewhere').mkdir()
        (self.home / '.claude').symlink_to(self.root / 'elsewhere')
        (self.root / 'elsewhere/settings.json').write_text('{}')
        result = self.run_write({'section': 'claude', 'model': 'sonnet', 'auto_updates': True})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / 'elsewhere/settings.json').read_text(), '{}')