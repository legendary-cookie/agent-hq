import json
import unittest

from hq.control.pve import ControlError
from hq.router import INNER, KEY_INNER, SCRIPT, Router, check_key_request, parse_why, summarize

NOW = 1794000000.0
RAW = {
    'vmid': '122', 'status': 'running', 'version': '0.5.112', 'health': 200, 'headroom_health': 200,
    'services': {'9router': 'active', 'headroom': 'active'},
    'connections': [
        {'id': 'c3', 'provider': 'claude', 'auth': 'oauth', 'name': None, 'email': 'three@example.test', 'priority': 1, 'active': True, 'testStatus': 'active', 'expiresAt': '2026-11-06T22:53:35Z', 'backoffLevel': 0},
        {'id': 'c2', 'provider': 'claude', 'auth': 'oauth', 'name': None, 'email': 'two@example.test', 'priority': 2, 'active': True, 'testStatus': 'active', 'rateLimitedUntil': str(int((NOW + 600) * 1000)), 'backoffLevel': 1},
        {'id': 'c1', 'provider': 'claude', 'auth': 'oauth', 'name': None, 'email': 'one@example.test', 'priority': 3, 'active': True, 'testStatus': 'expired', 'lastError': 'refresh failed'},
    ],
    'settings': {'fallbackStrategy': 'fill-first', 'autoPing': {'enabled': False, 'schedules': {'c3': True}}},
    'rotation': {'ran': '2026-11-06T15:13:07Z', 'results': [{'provider': 'claude', 'order': [
        {'priority': 1, 'why': 'weekly (7d) 8% left, resets 2026-11-08T08:00:00.172Z'},
        {'priority': 2, 'why': 'weekly (7d) 34% left, resets 2026-11-09T11:59:59Z'},
        {'priority': 3, 'why': 'weekly (7d) 83% left, resets 2026-11-12T10:59:59Z'}]}]},
    'api_keys': [{'id': 'k1', 'name': 'ct201-assistant', 'active': True, 'created': '', 'restricted': True, 'allow': ['default']},
                 {'id': 'k0', 'name': 'Default Key', 'active': True, 'created': '', 'restricted': False, 'allow': []}],
    'usage': [[int(NOW - 3600), 'claude', 'claude-opus-5-5', 'c3', 'k1', 1000, 200, 0.5, 'ok'],
              [int(NOW - 7200), 'claude', 'claude-sonnet-5-5', 'c3', 'k1', 500, 100, 0.1, 'error'],
              [int(NOW - 40 * 86400), 'claude', 'old', 'c3', None, 9, 9, 9, 'ok']],
    'combos': [{'name': 'default', 'models': '["cc/claude-opus-5-5"]'}], 'models': {'claude': []},
}


class RouterTests(unittest.TestCase):
    def test_reads_the_limit_and_reset_from_the_rotation_note(self):
        found = parse_why('weekly (7d) 58% left, resets 2026-11-08T08:00:00.172Z')
        self.assertEqual((found['window'], found['left_pct']), ('weekly (7d)', 58.0))
        self.assertGreater(found['resets_at'], 1.7e9)
        self.assertIsNone(parse_why('nothing useful'))

    def test_summary_shows_accounts_rotation_and_the_one_in_use(self):
        snap = summarize(RAW, NOW)
        self.assertTrue(snap['up'])
        by = {c['id']: c for c in snap['connections']}
        self.assertEqual(by['c3']['name'], 'three@example.test')
        self.assertEqual(by['c3']['limit']['left_pct'], 8.0)
        self.assertEqual(by['c3']['rotation_position'], 1)
        self.assertTrue(by['c3']['auto_ping'])
        self.assertAlmostEqual(by['c2']['rate_limited_until'], NOW + 600)
        self.assertEqual(by['c3']['requests_24h'], 2)
        # c2 is cooling down, so the next one in rotation that's ready is c3
        self.assertEqual(snap['in_use'], 'c3')

    def test_auto_ping_follows_the_per_account_switch_not_the_top_level_flag(self):
        # As on the real 9router: top-level enabled is false, but each account's own switch is on.
        raw = {**RAW, 'settings': {'autoPing': {'enabled': False, 'connections': {'c1': True, 'c2': True},
                                                'schedules': {'c1': False, 'c2': False, 'c3': False}}}}
        raw['connections'] = [dict(c) for c in RAW['connections']]
        raw['connections'][2]['lastPingAt'] = '2026-11-06T10:00:00Z'
        by = {c['id']: c for c in summarize(raw, NOW)['connections']}
        self.assertEqual((by['c1']['auto_ping'], by['c2']['auto_ping'], by['c3']['auto_ping']), (True, True, False))
        self.assertEqual(by['c1']['auto_ping_how'], 'at reset')
        self.assertGreater(by['c1']['last_ping_at'], 1.7e9)
        self.assertIsNone(by['c2']['last_ping_at'])

    def test_plan_share_is_the_main_figure_and_api_cost_only_counts_as_paid_for_api_accounts(self):
        raw = {**RAW, 'connections': [dict(c) for c in RAW['connections']]}
        snap = summarize(raw, NOW)
        plan = snap['plan']
        self.assertEqual(plan['billing'], 'plan')
        # c3 is the only account with usage this week (92% used); k1 made all of it, so k1 holds all of c3's share
        k1 = plan['keys']['k1']
        self.assertAlmostEqual(k1['plan_pct'], 92 / 3, places=1)
        self.assertEqual(k1['api_paid'], 0.0)
        self.assertEqual(plan['by_vmid'][201]['plan_pct'], k1['plan_pct'])
        self.assertEqual(plan['used_pct'], round((92 + 66 + 17) / 3, 1))
        # An API-key account's spend is real money
        raw['connections'][0]['auth'] = 'apikey'
        plan = summarize(raw, NOW)['plan']
        self.assertEqual(plan['billing'], 'mixed')  # the other two are still subscriptions
        self.assertEqual(plan['keys']['k1']['api_paid'], 0.6)
        self.assertEqual(plan['keys']['k1']['plan_pct'], 0.0)
        for c in raw['connections']:
            c['auth'] = 'apikey'
        self.assertEqual(summarize(raw, NOW)['plan']['billing'], 'api')

    def test_usage_totals_skip_old_rows_and_count_errors(self):
        usage = summarize(RAW, NOW)['usage']
        self.assertEqual(usage['total']['requests'], 2)
        self.assertEqual(usage['total']['errors'], 1)
        self.assertEqual(usage['total']['prompt_tokens'] + usage['total']['completion_tokens'], 1800)
        self.assertEqual(usage['by_model'][0]['name'], 'claude/claude-opus-5-5')
        self.assertEqual(usage['by_account'][0]['name'], 'three@example.test')
        self.assertEqual(sum(usage['daily_tokens']), 1800)

    def test_keys_get_usage_and_the_agent_from_their_name(self):
        snap = summarize(RAW, NOW)
        keys = {k['id']: k for k in snap['api_keys']}
        self.assertEqual((keys['k1']['vmid'], keys['k1']['requests_30d'], keys['k1']['tokens_30d'], keys['k1']['errors_30d']), (201, 2, 1800, 1))
        self.assertIsNone(keys['k0']['vmid'])
        self.assertNotIn('last4', keys['k1'])
        self.assertEqual(snap['usage']['by_key'][0]['name'], 'ct201-assistant')

    def test_assignments_are_checked_saved_and_cleared(self):
        import tempfile
        from pathlib import Path
        from hq.router import KeyAssignments
        with tempfile.TemporaryDirectory() as tmp:
            store = KeyAssignments(Path(tmp) / 'router-keys.json')
            store.set('k1', ['ct201-assistant', 'ct202-manager'], ['n8n'], 'shared by two')
            self.assertEqual(store.all()['k1']['agents'], ['ct201-assistant', 'ct202-manager'])
            for bad in ((None, [], [], ''), ('k1', ['a b'], [], ''), ('k1', [], ['x' * 61], ''), ('k1', [], [], 'n' * 201)):
                with self.assertRaises(ValueError):
                    store.set(*bad)
            store.set('k1')
            self.assertEqual(store.all(), {})

    def test_down_and_missing(self):
        self.assertFalse(summarize({**RAW, 'health': 'connection refused'}, NOW)['up'])
        self.assertEqual(summarize({'missing': True}, NOW), {'found': False})

    def test_the_container_script_never_reads_tokens_out(self):
        # Only whitelisted fields leave the container: no token, refresh token or API key value.
        self.assertNotIn("'accessToken'", INNER)
        self.assertIn("safe = ('testStatus'", INNER)
        self.assertIn('mode=ro', INNER)
        self.assertNotIn('select id, key,', INNER)  # the key value itself isn't read in the snapshot

    def test_refresh_runs_the_script_on_the_host_with_the_inner_part_on_stdin(self):
        calls = []

        class Pve:
            def run(self, words, timeout=60, stdin=None):
                calls.append((words, stdin))
                return json.dumps(RAW) + '\n'

            def configured(self):
                return True
        router = Router(Pve(), clock=lambda: NOW)
        snap = router.refresh()
        self.assertEqual(calls[0][0][:2], ['python3', '-c'])
        self.assertEqual(calls[0][0][2], SCRIPT)
        self.assertEqual(calls[0][1], INNER)
        self.assertEqual(router.snapshot(), (snap, None))


class KeyActionTests(unittest.TestCase):
    def snap(self):
        return summarize(RAW, NOW)

    def test_requests_are_checked_against_the_keys_9router_has(self):
        snap = self.snap()
        self.assertEqual(check_key_request('pause', snap, 'k1'), {'action': 'pause', 'id': 'k1'})
        self.assertEqual(check_key_request('create', snap, name=' n8n-main ', access={'restricted': True, 'allow': ['default']}),
                         {'action': 'create', 'name': 'n8n-main', 'access': {'restricted': True, 'allow': ['default']}})
        for bad in (('pause', 'nope'), ('drop', 'k1'), ('delete', "k1'; rm")):
            with self.assertRaises(ValueError):
                check_key_request(bad[0], snap, bad[1])
        for name in ('ct201-assistant', 'has space', '', None, 'x' * 61):  # taken, or not a plain name
            with self.assertRaises(ValueError):
                check_key_request('create', snap, name=name)
        with self.assertRaises(ValueError):
            check_key_request('create', snap, name='ok', access={'restricted': 'yes'})

    def test_actions_go_through_9routers_own_api_and_return_the_value_only_when_asked(self):
        sent = []

        class Pve:
            def __init__(self, answer):
                self.answer = answer

            def run(self, words, timeout=60, stdin=None):
                sent.append(stdin)
                return json.dumps({**self.answer, 'vmid': '122', 'status': 'running'}) + '\n'

            def configured(self):
                return True
        router = Router(Pve({'key': 'sk-secret'}), clock=lambda: NOW)
        router._snapshot = self.snap()
        router._refresh_quietly = lambda: None
        self.assertEqual(router.key_action('reveal', 'k1'), {'key': 'sk-secret'})
        self.assertIn('"action": "reveal"', sent[-1])
        self.assertIn('x-9r-cli-token', sent[-1])
        self.assertNotIn('__REQ__', sent[-1])
        router.pve = Pve({'error': 'Key not found'})
        with self.assertRaises(ControlError):
            router.key_action('delete', 'k1')
        self.assertIn("'/api/keys/' + kid", KEY_INNER)


class RouterAlertTests(unittest.TestCase):
    def test_alerts_for_failing_cooling_and_nearly_used_accounts(self):
        from hq.watch import Watchman
        snap = summarize(RAW, NOW)
        watch = Watchman.__new__(Watchman)
        watch.router, watch.clock = (lambda: snap), (lambda: NOW)
        found = {key: text for kind, key, text in watch._router()}
        self.assertIn('expired', found['router:c1'])
        self.assertIn('cooling down', found['router:c2'])
        self.assertIn('8% left', found['router:c3'])
        down = summarize({**RAW, 'services': {'9router': 'failed'}}, NOW)
        watch.router = lambda: down
        self.assertEqual([k for _, k, _ in watch._router()], ['9router'])


if __name__ == '__main__':
    unittest.main()
