import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from hq.control import create as create_module
from hq.control.audit import ControlLog
from hq.control.create import Creator, free_ips, used_ips
from hq.control.pve import ControlError

CONFIGS = 'net0: name=eth0,bridge=vmbr0,gw=192.0.2.1,ip=192.0.2.60/24,type=veth\nnet0: name=eth0,ip=192.0.2.61/24\n'
GOOD = {'name': 'nadia', 'label': 'Nadia', 'owner': 'Alex Smith', 'vmid': 314, 'ip': '192.0.2.79', 'source': 'ct309-template',
        'bot_token': '1234567890:' + 'A' * 35, 'owner_chat_id': '123456789', 'owner_username': '@nadia_y', 'group': 'Staff', 'role': 'Sales support'}


class FakePve:
    def __init__(self, fail_on=None, existing=()):
        self.agents = {'ct309-template': {'vmid': 309, 'login': 'zylos'}}
        self.calls, self.stdin, self.fail_on, self.existing = [], [], fail_on, set(existing)

    def agent(self, host):
        if host not in self.agents:
            raise ControlError('HQ can only control the agents it watches')
        return self.agents[host]

    def run(self, words, timeout=60, stdin=None):
        text = ' '.join(map(str, words))
        self.calls.append(text)
        if stdin is not None:
            self.stdin.append(stdin)
        if self.fail_on and self.fail_on in text:
            raise ControlError(f'failed at {self.fail_on}')
        if words[:2] == ['pct', 'status']:
            if words[2] in self.existing:
                return 'status: running'
            raise ControlError('does not exist')
        if 'nextid' in text:
            return '314'
        if words[:2] == ['sh', '-c']:
            return CONFIGS
        if words[:2] == ['pct', 'config']:
            return 'net0: name=eth0,bridge=vmbr0,firewall=1,gw=192.0.2.1,hwaddr=AA:BB,ip=192.0.2.74/24,type=veth\n'
        if 'WIPED' in text:
            return 'WIPED zylos\n'
        if 'ssh_host_ed25519_key.pub' in text:
            return 'ssh-ed25519 AAAAKEY root@x\n'
        return ''

    def forget(self):
        pass


class CreateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = ControlLog(Path(tmp.name) / 'log.jsonl')
        self.registered = []
        sleep = mock.patch.object(create_module, 'pause', lambda s: None)
        sleep.start()
        self.addCleanup(sleep.stop)

    def creator(self, pve):
        return Creator(pve, self.log, self.registered.append)

    def wait(self, creator, job_id):
        for _ in range(300):
            if creator.jobs[job_id].get('ended_at') is not None:
                return creator.jobs[job_id]
            time.sleep(.01)
        self.fail('creation did not finish')

    def test_ip_helpers(self):
        self.assertEqual(used_ips([CONFIGS]), {'192.0.2.60', '192.0.2.61'})
        self.assertEqual(free_ips([CONFIGS])[:2], ['192.0.2.62', '192.0.2.63'])

    def test_validation_refuses_bad_input(self):
        creator = self.creator(FakePve(existing={315}))
        cases = [{'name': 'Bad Name'}, {'name': 'x'}, {'vmid': 500}, {'vmid': 315}, {'ip': '10.0.0.5'}, {'ip': '192.0.2.60'},
                 {'bot_token': 'nope'}, {'owner_chat_id': 'abc'}, {'source': 'ct999-x'}, {'label': ''}]
        for change in cases:
            with self.assertRaises((ValueError, ControlError), msg=change):
                creator.validate({**GOOD, **change})
        self.assertEqual(creator.validate(GOOD)['owner_username'], 'nadia_y')

    def test_create_follows_the_safe_order(self):
        pve = FakePve()
        creator = self.creator(pve)
        job = self.wait(creator, creator.start(GOOD))
        self.assertEqual(job['state'], 'done', job)
        def index(prefix):
            return next(i for i, c in enumerate(pve.calls) if c.startswith(prefix))
        unplugged = next(i for i, c in enumerate(pve.calls) if 'link_down=1' in c)
        started = index('pct start 314')
        wiped = next(i for i, c in enumerate(pve.calls) if 'WIPED' in c)
        plugged = next(i for i, c in enumerate(pve.calls) if c.startswith('pct set 314 --net0') and 'link_down' not in c)
        self.assertLess(index('pct snapshot 309'), index('pct clone 309 314'))
        self.assertLess(index('pct clone'), index('pct delsnapshot 309'))
        self.assertTrue(unplugged < started < wiped < plugged)
        self.assertIn('ip=192.0.2.79/24', pve.calls[plugged])
        self.assertNotIn('hwaddr=AA:BB', pve.calls[plugged])  # A new MAC address, not the source's.
        # The bot token only ever travels on stdin, never in a command line.
        self.assertFalse(any(GOOD['bot_token'] in c for c in pve.calls))
        self.assertEqual(json.loads(pve.stdin[0])['bot_token'], GOOD['bot_token'])
        self.assertEqual(self.registered[0]['name'], 'ct314-nadia')
        self.assertEqual(self.registered[0]['ssh'], 'zylos@192.0.2.79')
        self.assertEqual(self.registered[0]['host_key'], 'ssh-ed25519 AAAAKEY')
        self.assertIn('--hostname nadia-ai-agent', pve.calls[index('pct clone')])

    def test_a_failed_clone_still_removes_the_temporary_snapshot(self):
        pve = FakePve(fail_on='pct clone')
        creator = self.creator(pve)
        job = self.wait(creator, creator.start(GOOD))
        self.assertEqual(job['state'], 'failed')
        self.assertTrue(any(c.startswith('pct delsnapshot 309') for c in pve.calls))
        self.assertIn('Nothing was created', job['steps'][-1]['text'])
        self.assertEqual(self.registered, [])

    def test_a_failed_wipe_leaves_the_copy_stopped_and_unplugged(self):
        pve = FakePve(fail_on='WIPED')
        creator = self.creator(pve)
        job = self.wait(creator, creator.start(GOOD))
        self.assertEqual(job['state'], 'failed')
        self.assertIn('pct stop 314', pve.calls)
        self.assertFalse(any(c.startswith('pct set 314 --net0') and 'link_down' not in c for c in pve.calls))
        self.assertIn('left stopped with its network unplugged', job['steps'][-1]['text'])

    def test_wipe_uses_the_login_hq_knows_and_stops_if_the_login_survived(self):
        pve = FakePve()
        creator = self.creator(pve)
        self.wait(creator, creator.start(GOOD))
        wipe = next(c for c in pve.calls if 'WIPED' in c)
        self.assertTrue(wipe.endswith(' wipe zylos'))
        self.assertNotIn('stat -c %U', create_module.WIPE + create_module.IDENTITY)
        self.assertIn('still there after the wipe', create_module.WIPE)
        self.assertEqual(json.loads(pve.stdin[0])['login'], 'zylos')

    def test_an_unfinished_wipe_keeps_the_copy_offline(self):
        class NoWipe(FakePve):
            def run(self, words, timeout=60, stdin=None):
                out = super().run(words, timeout, stdin)
                return 'still there after the wipe: x\n' if 'WIPED' in ' '.join(map(str, words)) else out
        pve = NoWipe()
        creator = self.creator(pve)
        job = self.wait(creator, creator.start(GOOD))
        self.assertEqual(job['state'], 'failed')
        self.assertFalse(any(c.startswith('pct set 314 --net0') and 'link_down' not in c for c in pve.calls))

    def test_a_failure_once_online_says_so_and_leaves_it_running(self):
        def broken(info):
            raise AttributeError('hosts.json is malformed')
        pve = FakePve()
        creator = Creator(pve, self.log, broken)
        job = self.wait(creator, creator.start(GOOD))
        self.assertEqual(job['state'], 'failed')
        self.assertIn('is online', job['steps'][-1]['text'])
        self.assertNotIn('pct stop 314', pve.calls)
        self.wait(creator, creator.start(GOOD))  # not blocked by the failed job

    def test_one_creation_at_a_time(self):
        creator = self.creator(FakePve())
        creator.jobs['x'] = {'state': 'running', 'started_at': 0}
        with self.assertRaisesRegex(ControlError, 'another agent'):
            creator.start(GOOD)

    def test_wipe_keeps_company_knowledge(self):
        self.assertIn('reference/decisions.md', create_module.WIPE)
        self.assertNotIn('reference/company.md', create_module.WIPE)
        self.assertIn('systemctl disable zylos-hxa-connect', create_module.WIPE)
        self.assertIn('ssh-keygen -A', create_module.WIPE)


if __name__ == '__main__':
    unittest.main()
