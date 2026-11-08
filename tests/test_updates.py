import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from hq.control.audit import ControlLog
from hq.control.pve import ControlError
from hq.control.updates import Updates, clean_settings, latest_versions, newer, next_check, parse

AUDIT_LINE = 'debian=12.13 list_days=120 pending=40 security=12 reboot=yes node=20.19.0 claude=2.1.281 claude_prefix=/usr zylos=0.9.3 hermes=0.13.0 updates_held=yes \n'


class FakePve:
    """Answers the commands the update engine runs, and records them."""
    def __init__(self, after_claude='2.1.311', fail_on=None, thin=(50, 100)):
        self.agents = {'ct310-intern': {'vmid': 310, 'login': 'zylos'}}
        self.calls, self.after_claude, self.fail_on, self.thin = [], after_claude, fail_on, thin
        self.updated = False

    def _maybe_fail(self, text):
        if self.fail_on and self.fail_on in text:
            raise ControlError(f'failed: {self.fail_on}')

    def agent(self, host):
        if host not in self.agents:
            raise ControlError('HQ can only control the agents it watches')
        return self.agents[host]

    def run(self, words, timeout=60):
        text = ' '.join(map(str, words))
        self.calls.append(text)
        self._maybe_fail(text)
        if words[:2] == ['pct', 'config']:
            return 'arch: amd64\nrootfs: local-lvm:vm-310-disk-0,size=16G\n'
        if 'storage/local-lvm/status' in text:
            return json.dumps({'used': self.thin[0], 'total': self.thin[1]})
        if text.endswith('/snapshot --output-format json'):
            return json.dumps([{'name': 'current'}, {'name': 'hq-claude-20261106-1200', 'snaptime': 1, 'description': 'Before HQ claude update\n'}])
        return ''

    def in_agent(self, host, script, as_root=False, timeout=60):
        self.calls.append(('in', script[:60]))
        self._maybe_fail(script)
        if 'npm install' in script or 'hermes update' in script:
            self.updated = True
            return ''
        if 'out debian' in script:
            return AUDIT_LINE.replace('claude=2.1.281', f'claude={self.after_claude}').replace('hermes=0.13.0', 'hermes=0.22.7') if self.updated else AUDIT_LINE
        return ''

    def containers(self):
        return {'ct310-intern': {'status': 'running'}}

    def forget(self):
        pass

    def session_info(self, host):
        return {'session': 'claude-main', 'sessions': ['claude-main'], 'restartable': True}

    def restart_claude(self, host):
        self.calls.append('restart_claude')


class UpdateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.log = ControlLog(self.dir / 'log.jsonl')

    def engine(self, pve):
        return Updates(pve, self.dir / 'updates.json', self.log)

    def wait(self, engine, job_id):
        for _ in range(200):
            if engine.jobs[job_id].get('ended_at') is not None:
                return engine.jobs[job_id]
            time.sleep(.02)
        self.fail('update did not finish')

    def test_parse_audit_line(self):
        found = parse(AUDIT_LINE)
        self.assertEqual((found['claude'], found['zylos'], found['security'], found['reboot'], found['updates_held'], found['list_days']), ('2.1.281', '0.9.3', 12, True, True, 120))
        self.assertIsNone(parse('claude=; rm -rf /')['claude'])

    def test_versions_compare(self):
        self.assertTrue(newer('2.1.311', '2.1.281'))
        self.assertFalse(newer('2.1.281', '2.1.311'))
        self.assertFalse(newer(None, '1.0.0'))

    def test_latest_versions_survive_a_failed_lookup(self):
        def fetch(url):
            if 'claude-code' in url:
                return {'stable': '2.1.311', 'latest': '2.1.318'}
            raise OSError('offline')
        self.assertEqual(latest_versions(fetch), {'claude_stable': '2.1.311', 'claude_latest': '2.1.318'})

    def test_check_records_every_running_agent_and_compares(self):
        engine = self.engine(FakePve())
        engine.check()
        engine.state['latest'] = {'claude_stable': '2.1.311', 'zylos_latest': '0.9.5'}
        summary = engine.summary()
        self.assertEqual(summary['totals']['claude_behind'], 1)
        self.assertEqual(summary['totals']['zylos_behind'], 1)
        self.assertEqual(summary['totals']['security'], 12)
        self.assertTrue((self.dir / 'updates.json').is_file())

    def test_claude_update_snapshots_first_then_installs_and_restarts(self):
        pve = FakePve()
        engine = self.engine(pve)
        job = self.wait(engine, engine.start('ct310-intern', 'claude', '2.1.311'))
        self.assertEqual(job['state'], 'done', job)
        snap = next(i for i, c in enumerate(pve.calls) if isinstance(c, str) and c.startswith('pct snapshot 310 hq-claude-'))
        install = next(i for i, c in enumerate(pve.calls) if isinstance(c, tuple) and 'npm install' in c[1])
        self.assertLess(snap, install)
        self.assertIn('restart_claude', pve.calls)
        self.assertTrue(job['snapshot'].startswith('hq-claude-'))
        self.assertEqual([e['action'] for e in self.log.recent()], ['update_done', 'update_started'])

    def test_a_retry_in_the_same_second_gets_its_own_snapshot_name(self):
        pve = FakePve()
        engine = Updates(pve, self.dir / 'updates.json', self.log, clock=lambda: 1793959370.0)
        first = engine._snapshot_name('ct310-intern', 'security')
        self.assertRegex(first, r'^hq-security-\d{8}-\d{6}$')
        taken = [first, first + '-2']
        original = pve.run
        pve.run = lambda words, timeout=60: json.dumps([{'name': n} for n in taken]) if ' '.join(map(str, words)).endswith('/snapshot --output-format json') else original(words, timeout)
        self.assertEqual(engine._snapshot_name('ct310-intern', 'security'), first + '-3')

    def test_no_change_if_the_snapshot_fails(self):
        pve = FakePve(fail_on='pct snapshot')
        engine = self.engine(pve)
        job = self.wait(engine, engine.start('ct310-intern', 'claude', '2.1.311'))
        self.assertEqual(job['state'], 'failed')
        self.assertFalse(pve.updated)
        self.assertIn('Nothing was changed', job['steps'][-1]['text'])

    def test_full_storage_stops_before_the_snapshot(self):
        pve = FakePve(thin=(95, 100))
        engine = self.engine(pve)
        job = self.wait(engine, engine.start('ct310-intern', 'claude', '2.1.311'))
        self.assertIn('95% full', job['error'])
        self.assertFalse(any(isinstance(c, str) and c.startswith('pct snapshot') for c in pve.calls))

    def test_a_wrong_version_after_install_fails_and_points_to_the_snapshot(self):
        engine = self.engine(FakePve(after_claude='2.1.281'))
        job = self.wait(engine, engine.start('ct310-intern', 'claude', '2.1.311'))
        self.assertEqual(job['state'], 'failed')
        self.assertIn('Roll back to hq-claude-', job['steps'][-1]['text'])

    def test_bad_requests_are_refused(self):
        engine = self.engine(FakePve())
        for args in (('ct310-intern', 'kernel', None), ('ct310-intern', 'claude', '2.1; rm -rf /'), ('ct999-x', 'security', None)):
            with self.assertRaises((ValueError, ControlError)):
                engine.start(*args)
        for name in ('', 'a b', '../x', '1start'):
            with self.assertRaises(ValueError):
                engine.rollback('ct310-intern', name)

    def test_one_update_at_a_time_per_agent(self):
        pve = FakePve(fail_on=None)
        engine = self.engine(pve)
        engine.jobs['x'] = {'id': 'x', 'host': 'ct310-intern', 'state': 'running', 'started_at': 0}
        with self.assertRaisesRegex(ControlError, 'already running'):
            engine.start('ct310-intern', 'security')

    def test_no_rollback_or_delete_while_an_update_runs(self):
        engine = self.engine(FakePve())
        engine.jobs['x'] = {'id': 'x', 'host': 'ct310-intern', 'state': 'running', 'started_at': 0}
        with self.assertRaisesRegex(ControlError, 'update is running'):
            engine.rollback('ct310-intern', 'hq-claude-20261106-1200')
        with self.assertRaisesRegex(ControlError, 'update is running'):
            engine.delete_snapshot('ct310-intern', 'hq-claude-20261106-1200')
        with self.assertRaisesRegex(ControlError, 'in use'):
            engine.delete_snapshot('ct310-intern', 'hq-clone-source')

    def test_an_unexpected_error_still_ends_the_job(self):
        class Odd(FakePve):
            def session_info(self, host):
                raise AttributeError('odd reply')
        engine = self.engine(Odd())
        job = self.wait(engine, engine.start('ct310-intern', 'claude', '2.1.311'))
        self.assertEqual(job['state'], 'failed')
        self.assertIn('odd reply', job['error'])

    def test_the_watchdog_survives_a_failed_check(self):
        engine = self.engine(FakePve())
        stop = threading.Event()
        calls = []
        def boom():
            calls.append(1)
            stop.set()
            raise OSError('disk full')
        engine.check = boom
        engine.watch(stop)
        self.assertEqual(calls, [1])
        self.assertEqual(self.log.recent()[0]['action'], 'update_check_failed')

    def test_snapshots_hide_current_and_rollback_starts_it_again(self):
        pve = FakePve()
        engine = self.engine(pve)
        self.assertEqual([s['name'] for s in engine.snapshots('ct310-intern')], ['hq-claude-20261106-1200'])
        engine.rollback('ct310-intern', 'hq-claude-20261106-1200')
        self.assertIn('pct rollback 310 hq-claude-20261106-1200 --start 1', pve.calls)

    def test_parse_reads_hermes_codex_and_only_known_install_places(self):
        found = parse('claude=2.1.311 claude_prefix=/root/.hermes/node hermes=0.13.0 codex=0.50.0 codex_prefix=/tmp/x')
        self.assertEqual((found['hermes'], found['codex'], found['claude_prefix'], found['codex_prefix']), ('0.13.0', '0.50.0', '/root/.hermes/node', None))

    def test_parse_reads_native_and_per_user_installs_but_not_odd_paths(self):
        found = parse('claude=2.1.316 claude_home=/home/agentuser zylos=0.9.3 zylos_prefix=/home/agentuser/.nvm/versions/node/v22.22.1')
        self.assertEqual((found['claude_home'], found['zylos_prefix']), ('/home/agentuser', '/home/agentuser/.nvm/versions/node/v22.22.1'))
        odd = parse('claude_home=/home/x;rm zylos_prefix=/home/a/../root/.local codex_prefix=/home/w/.local')
        self.assertEqual((odd['claude_home'], odd['zylos_prefix'], odd['codex_prefix']), (None, None, '/home/w/.local'))

    def test_per_user_and_native_installs_run_as_their_user(self):
        class Recording(FakePve):
            def in_agent(self, host, script, as_root=False, timeout=60):
                self.calls.append(('full', script))
                return super().in_agent(host, script, as_root, timeout)
        pve = Recording(fail_on='nothing-fails')
        engine = self.engine(pve)
        reading = {**parse('claude=2.1.281 claude_home=/home/agenttwo zylos=0.9.3 zylos_prefix=/home/agentone/.local'), 'ok': True}
        engine.state['agents'] = {'ct310-intern': dict(reading)}
        self.wait(engine, engine.start('ct310-intern', 'zylos', snapshot=False))
        engine.state['agents'] = {'ct310-intern': dict(reading)}  # the fake's check after the update reads differently
        self.wait(engine, engine.start('ct310-intern', 'claude', '2.1.311', snapshot=False))
        scripts = [c[1] for c in pve.calls if isinstance(c, tuple) and c[0] == 'full']
        self.assertTrue(any(s.startswith("su - agentone -s /bin/bash -c 'PATH=/home/agentone/.local/bin:$PATH zylos upgrade") for s in scripts), scripts)
        self.assertTrue(any(s.startswith("su - agenttwo -s /bin/bash -c '/home/agenttwo/.local/bin/claude install 2.1.311") for s in scripts), scripts)

    def test_claude_is_never_downgraded(self):
        engine = self.engine(FakePve())
        engine.state['agents'] = {'ct310-intern': {**parse('claude=2.1.317 claude_home=/root'), 'ok': True}}
        with self.assertRaisesRegex(ControlError, 'already 2.1.317'):
            engine.start('ct310-intern', 'claude', '2.1.311')

    def test_an_update_for_a_tool_the_agent_lacks_is_refused(self):
        engine = self.engine(FakePve())
        engine.state['agents'] = {'ct310-intern': {**parse(AUDIT_LINE), 'ok': True}}
        with self.assertRaisesRegex(ControlError, 'Codex isn’t installed'):
            engine.start('ct310-intern', 'codex', '0.50.0')

    def test_hermes_update_runs_hermes_update_and_refreshes_the_row(self):
        pve = FakePve()
        engine = self.engine(pve)
        job = self.wait(engine, engine.start('ct310-intern', 'hermes'))
        self.assertEqual(job['state'], 'done', job)
        self.assertTrue(pve.updated)
        self.assertIn('Hermes is now 0.22.7', [s['text'] for s in job['steps']])
        self.assertEqual(engine.state['agents']['ct310-intern']['hermes'], '0.22.7')

    def test_scripts_put_usr_local_bin_on_the_path_first(self):
        # pct exec leaves /usr/local/bin out of PATH; hermes update then can't find uv and fails (AgentOps, 2026-11-06).
        from hq.control.updates import CHECK, HERMES_UPDATE
        for script in (CHECK, HERMES_UPDATE):
            body = script.strip().splitlines()
            first = next(i for i, line in enumerate(body) if not line.startswith('#'))
            self.assertEqual(body[first], 'export PATH=/usr/local/sbin:/usr/local/bin:$PATH')
        self.assertIn('hermes --version', HERMES_UPDATE)
        self.assertIn('did not finish', HERMES_UPDATE)  # hermes update exits 0 when its dependency step fails
        # Debian 12's Python 3.11.2 is too old for newer Hermes: it is moved onto uv's 3.12, and put back if that fails.
        self.assertIn('sys.version_info < (3, 11, 4)', HERMES_UPDATE)
        self.assertIn('rm -rf venv; mv .venv venv', HERMES_UPDATE)
        # Also needs build tools, pnpm, and git access for the agent's user (Customer Insights, 2026-11-06).
        for need in ('build-essential cmake', 'pnpm@10', 'safe.directory /usr/local/lib/hermes-agent', 'dependencies failed',
                     'trap \'chown -R "$owner" "$home"\' EXIT', 'refusing to update', 'hq-hermes-update-first.log'):
            self.assertIn(need, HERMES_UPDATE)
        self.assertIn('describe --tags', CHECK)

    def test_snapshots_can_be_switched_off(self):
        pve = FakePve()
        engine = self.engine(pve)
        job = self.wait(engine, engine.start('ct310-intern', 'claude', '2.1.311', snapshot=False))
        self.assertEqual(job['state'], 'done', job)
        self.assertIsNone(job['snapshot'])
        self.assertFalse(any(isinstance(c, str) and c.startswith('pct snapshot') for c in pve.calls))

    def test_snapshot_is_deleted_once_the_checks_pass(self):
        pve = FakePve()
        engine = self.engine(pve)
        engine.set_settings({'cleanup': 'after_check'})
        job = self.wait(engine, engine.start('ct310-intern', 'claude', '2.1.311'))
        self.assertEqual(job['state'], 'done', job)
        self.assertIn(f'pct delsnapshot 310 {job["snapshot"]}', pve.calls)

    def test_snapshot_is_deleted_after_the_wait_if_the_agent_is_still_fine(self):
        now = [1793959370.0]
        pve = FakePve()
        engine = Updates(pve, self.dir / 'updates.json', self.log, clock=lambda: now[0])
        engine.set_settings({'cleanup': 'after_hours', 'cleanup_hours': 24})
        job = self.wait(engine, engine.start('ct310-intern', 'claude', '2.1.311'))
        engine.run_cleanups()
        self.assertNotIn(f'pct delsnapshot 310 {job["snapshot"]}', pve.calls)
        now[0] += 25 * 3600
        engine.run_cleanups()
        self.assertIn(f'pct delsnapshot 310 {job["snapshot"]}', pve.calls)
        self.assertEqual(engine.state['cleanups'], [])

    def test_a_failed_agent_keeps_its_snapshot(self):
        now = [1793959370.0]
        pve = FakePve()
        engine = Updates(pve, self.dir / 'updates.json', self.log, clock=lambda: now[0])
        engine.state['cleanups'] = [{'host': 'ct310-intern', 'name': 'hq-claude-1', 'due': 0}]
        pve.containers = lambda: {'ct310-intern': {'status': 'stopped'}}
        engine.run_cleanups()
        self.assertFalse(any(isinstance(c, str) and c.startswith('pct delsnapshot') for c in pve.calls))
        self.assertEqual(self.log.recent()[0]['action'], 'snapshot_kept')

    def test_settings_are_checked_and_saved(self):
        engine = self.engine(FakePve())
        engine.set_settings({'snapshot': False, 'schedule': {'mode': 'weekly', 'day': 6, 'time': '04:30'}})
        again = Updates(FakePve(), self.dir / 'updates.json', self.log)
        self.assertEqual((again.settings['snapshot'], again.settings['schedule']['mode'], again.settings['schedule']['day']), (False, 'weekly', 6))
        for bad in ({'cleanup': 'never'}, {'cleanup_hours': 0}, {'schedule': {'mode': 'hourly'}}, {'schedule': {'time': '25:00'}}, {'schedule': {'hours': 0}}):
            with self.assertRaises(ValueError):
                clean_settings(bad)

    def test_schedule_times(self):
        from datetime import datetime
        last = datetime(2026, 11, 6, 2, 0).timestamp()
        now = datetime(2026, 11, 6, 2, 30).timestamp()
        self.assertEqual(next_check({'mode': 'every', 'hours': 6}, last, now), last + 6 * 3600)
        self.assertEqual(next_check({'mode': 'daily', 'time': '03:00'}, last, now), datetime(2026, 11, 6, 3, 0).timestamp())
        late = datetime(2026, 11, 6, 3, 5).timestamp()
        self.assertEqual(next_check({'mode': 'daily', 'time': '03:00'}, last, late), datetime(2026, 11, 6, 3, 0).timestamp())  # due now
        # 2026-11-06 is a Friday; the next Monday 03:00 is the 9th
        self.assertEqual(next_check({'mode': 'weekly', 'time': '03:00', 'day': 0}, now, now), datetime(2026, 11, 9, 3, 0).timestamp())
        self.assertEqual(next_check({'mode': 'every', 'hours': 12}, None, now), now)


if __name__ == '__main__':
    unittest.main()
