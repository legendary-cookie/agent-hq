import json
import tempfile
import threading
import unittest
from pathlib import Path

from hq.control.audit import ControlLog
from hq.control.pve import ControlError
from hq.watch import AlertSettings, Watchman, limited

TOKEN = '123456789:' + 'A' * 35


class FakePve:
    def __init__(self, containers=None, sessions=None):
        self.containers_now = containers or {}
        self.sessions = sessions or {}
        self.restarted = []

    def configured(self):
        return True

    def containers(self):
        return self.containers_now

    def session_info(self, host):
        return self.sessions.get(host, {'session': 'claude-main', 'restartable': True})

    def restart_claude(self, host):
        self.restarted.append(host)


class WatchTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.now = [100000.0]
        self.sent = []
        self.log = ControlLog(self.dir / 'log.jsonl')
        self.settings = AlertSettings(self.dir / 'alerts.json')
        self.settings.update({'enabled': True, 'bot_token': TOKEN, 'chat_id': '123456789'})
        self.agents = []

    def watch(self, pve=None):
        return Watchman(self.settings, pve or FakePve(), self.log, lambda: {'agents': self.agents},
                        brand=lambda: 'HQ', clock=lambda: self.now[0], sender=lambda token, chat, text: self.sent.append(text))

    def agent(self, host, **patch):
        return {'host': host, 'label': host.upper(), 'status': 'idle', 'last_seen': self.now[0] - 60, 'activity': None, **patch}

    def test_token_is_never_shown_and_is_checked(self):
        public = self.settings.public()
        self.assertTrue(public['has_token'] and public['ready'])
        self.assertNotIn(TOKEN, json.dumps(public))
        for bad in ({'bot_token': 'nope'}, {'chat_id': 'abc'}, {'kinds': {'nope': True}}, {'disk_pct': 120}):
            with self.assertRaises(ValueError):
                self.settings.update(bad)

    def test_an_unreachable_agent_is_sent_once_then_after_the_cooldown_and_when_back(self):
        watch = self.watch()
        self.agents = [self.agent('a', status='attention')]
        watch.tick()
        watch.tick()
        self.assertEqual(len(self.sent), 1)
        self.assertIn('A isn’t reporting', self.sent[0])
        self.now[0] += 61 * 60
        watch.tick()
        self.assertEqual(len(self.sent), 2)
        self.agents = [self.agent('a')]
        watch.tick()
        self.assertEqual(self.sent[-1], 'HQ: A is back')
        self.assertEqual(watch.summary()['warnings'], [])

    def test_limit_and_errors(self):
        self.agents = [self.agent('a', limit_at=self.now[0] - 100), self.agent('b', api_error_at=self.now[0] - 60)]
        watch = self.watch()
        watch.tick()
        kinds = sorted(w['kind'] for w in watch.summary()['warnings'])
        self.assertEqual(kinds, ['errors', 'limit'])

    def test_a_saved_login_kind_from_the_retired_pool_is_dropped(self):
        self.settings.path.write_text(json.dumps({**json.loads(self.settings.path.read_text(encoding='utf-8')), 'kinds': {'login': False, 'disk': False}}), encoding='utf-8')
        kinds = self.settings.load()['kinds']
        self.assertNotIn('login', kinds)
        self.assertFalse(kinds['disk'])
        self.settings.update({'kinds': kinds})  # sending the shown kinds back still works

    def test_switched_off_kinds_and_alerts_are_only_warnings(self):
        self.settings.update({'kinds': {'limit': False}})
        self.agents = [self.agent('a', limit_at=self.now[0] - 100)]
        watch = self.watch()
        watch.tick()
        self.assertEqual(self.sent, [])
        self.assertEqual(len(watch.summary()['warnings']), 1)
        self.settings.update({'enabled': False, 'kinds': {'limit': True}})
        self.agents = [self.agent('b', status='attention')]
        watch.tick()
        self.assertEqual(self.sent, [])

    def test_disk_memory_and_stopped_containers(self):
        pve = FakePve({'a': {'status': 'running', 'disk': 95, 'maxdisk': 100, 'mem': 10, 'maxmem': 100},
                       'b': {'status': 'stopped'}})
        watch = self.watch(pve)
        watch.tick()
        texts = sorted(w['text'] for w in watch.summary()['warnings'])
        self.assertEqual(texts, ['a: disk 95% full', 'b: its container is stopped'])

    def test_claude_is_restarted_after_two_checks_and_at_most_three_times(self):
        pve = FakePve({'a': {'status': 'running'}}, {'a': {'session': None, 'restartable': True}})
        watch = self.watch(pve)
        watch.tick()
        self.assertEqual(pve.restarted, [])  # might just be starting
        for _ in range(8):
            self.now[0] += 301
            watch.tick()
        self.assertEqual(pve.restarted, ['a', 'a', 'a'])
        self.assertTrue(any('keeps stopping' in w['text'] for w in watch.summary()['warnings']))
        self.assertEqual(sum(1 for e in self.log.recent(100) if e['action'] == 'auto_restart_claude'), 3)

    def test_no_restart_when_switched_off_or_not_runtime_started(self):
        pve = FakePve({'a': {'status': 'running'}, 'b': {'status': 'running'}},
                      {'a': {'session': None, 'restartable': True}, 'b': {'session': None, 'restartable': False}})
        self.settings.update({'auto_restart': False})
        watch = self.watch(pve)
        for _ in range(3):
            watch.tick()
            self.now[0] += 301
        self.assertEqual(pve.restarted, [])
        self.assertEqual([w['host'] for w in watch.summary()['warnings']], ['a'])

    def test_a_failing_telegram_is_logged_not_raised(self):
        def broken(token, chat, text):
            raise ValueError('Telegram refused the message: chat not found')
        watch = Watchman(self.settings, FakePve(), self.log, lambda: {'agents': [self.agent('a', status='attention')]}, clock=lambda: self.now[0], sender=broken)
        watch.tick()
        self.assertEqual(self.log.recent()[0]['action'], 'alert_failed')

    def test_the_loop_survives_errors(self):
        def boom():
            raise RuntimeError('db locked')
        watch = Watchman(self.settings, FakePve(), self.log, boom, clock=lambda: self.now[0])
        stop = threading.Event()
        import hq.watch as module
        old, module.TICK_S = module.TICK_S, 0.01
        try:
            thread = threading.Thread(target=watch.run, args=(stop,))
            thread.start()
            threading.Event().wait(0.1)
            stop.set()
            thread.join(2)
        finally:
            module.TICK_S = old
        self.assertEqual(self.log.recent()[0]['action'], 'watch_failed')

    def test_a_broken_telegram_is_tried_once_per_cooldown(self):
        tries = []
        def broken(token, chat, text):
            tries.append(text)
            raise ValueError('Telegram refused the message: Unauthorized')
        self.agents = [self.agent('a', status='attention')]
        watch = Watchman(self.settings, FakePve(), self.log, lambda: {'agents': self.agents}, clock=lambda: self.now[0], sender=broken)
        for _ in range(5):
            watch.tick()
            self.now[0] += 60
        self.assertEqual(len(tries), 1)
        self.assertEqual(sum(1 for e in self.log.recent(100) if e['action'] == 'alert_failed'), 1)

    def test_a_flapping_agent_is_not_sent_every_minute(self):
        watch = self.watch()
        for i in range(6):
            self.agents = [self.agent('a', status='attention' if i % 2 == 0 else 'idle')]
            watch.tick()
            self.now[0] += 60
        self.assertEqual(len(self.sent), 1)  # one alert; the short outages never earn an "is back"

    def test_proxmox_unreachable_keeps_what_was_known_and_says_so(self):
        class Flaky(FakePve):
            fail = False
            def containers(self):
                if self.fail:
                    raise ControlError('ssh: connect to host timed out')
                return {'a': {'status': 'running', 'disk': 95, 'maxdisk': 100}}
        pve = Flaky()
        watch = self.watch(pve)
        watch.tick()
        pve.fail = True
        self.now[0] += 301
        watch.tick()
        texts = [w['text'] for w in watch.summary()['warnings']]
        self.assertIn('a: disk 95% full', texts)
        self.assertTrue(any('can’t reach the Proxmox host' in text for text in texts))

    def test_the_keeps_stopping_warning_is_not_held_back_by_the_restart_notice(self):
        pve = FakePve({'a': {'status': 'running'}}, {'a': {'session': None, 'restartable': True}})
        watch = self.watch(pve)
        for _ in range(8):
            watch.tick()
            self.now[0] += 301
        self.assertTrue(any('keeps stopping' in text for text in self.sent))

    def test_a_stopped_container_resets_the_missing_count(self):
        pve = FakePve({'a': {'status': 'running'}}, {'a': {'session': None, 'restartable': True}})
        watch = self.watch(pve)
        watch.tick()                        # first miss
        pve.containers_now = {'a': {'status': 'stopped'}}
        self.now[0] += 301
        watch.tick()                        # rebooting
        pve.containers_now = {'a': {'status': 'running'}}
        self.now[0] += 301
        watch.tick()                        # first check after boot: only one miss
        self.assertEqual(pve.restarted, [])

    def test_hands_off_while_someone_works_on_the_agent(self):
        pve = FakePve({'a': {'status': 'running'}}, {'a': {'session': None, 'restartable': True}})
        watch = Watchman(self.settings, pve, self.log, lambda: {'agents': []}, clock=lambda: self.now[0],
                         sender=lambda *a: None, hands_off=lambda host: True)
        for _ in range(4):
            watch.tick()
            self.now[0] += 301
        self.assertEqual(pve.restarted, [])

    def test_another_tmux_session_is_not_claude(self):
        pve = FakePve({'a': {'status': 'running'}}, {'a': {'session': 'notes', 'sessions': ['notes'], 'restartable': True}})
        watch = self.watch(pve)
        for _ in range(2):
            watch.tick()
            self.now[0] += 301
        self.assertEqual(pve.restarted, ['a'])

    def test_office_entries_without_a_host_are_skipped(self):
        watch = Watchman(self.settings, FakePve(), self.log, lambda: {'agents': [{'label': 'no host'}]}, clock=lambda: self.now[0])
        watch.tick()
        self.assertEqual(watch.summary()['warnings'], [])
    def test_limited_ignores_a_persons_message(self):
        self.assertTrue(limited({'limit_at': 100, 'activity': {'kind': 'message', 'at': 500}, 'last_from_person': 500}))
        self.assertFalse(limited({'limit_at': 100, 'activity': {'kind': 'read', 'at': 500}}))

    def test_session_check_failure_is_quiet(self):
        class Flaky(FakePve):
            def session_info(self, host):
                raise ControlError('timeout')
        watch = self.watch(Flaky({'a': {'status': 'running'}}))
        watch.tick()
        self.assertEqual(watch.summary()['warnings'], [])


if __name__ == '__main__':
    unittest.main()
