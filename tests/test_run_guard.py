import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from hq.config import Profile
from hq.run.guard import check, model_blocked
from hq.run.pool import ProfileState, pick, profile_states
from hq.run.request import RunRequest
from hq.store import Store


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'hq.db')
        self.addCleanup(self.store.close)
        self.a = Profile('a', 'codex', 'unused', 'account-a')
        self.b = Profile('b', 'codex', 'unused', 'account-b')
        self.req = RunRequest('codex', 'model', 'project', 'prompt', 'edit', 'block', None, 30)
        self.recommend = lambda kind: []

    def snapshot(self, account, window, percent, reset=None, source='measured', ts=90):
        with closing(self.store.conn()) as conn:
            conn.execute('insert into limit_snapshots(account_id, window, used_percent, resets_at, source, recorded_at) values (?,?,?,?,?,?)',
                         (account, window, percent, reset, source, ts))
            conn.commit()

    def model_block(self, provider, scope, reset):
        with closing(self.store.conn()) as conn:
            conn.execute("insert into limit_snapshots(harness, provider, scope, window, used_percent, resets_at, source, recorded_at) values (?,?,?,'block',100,?,'blocked',90)",
                         (provider, provider, scope, reset))
            conn.commit()

    def test_model_block_exact_wildcard_and_expiry(self):
        self.model_block('codex', 'model', 120)
        self.assertEqual(model_blocked(self.store, 'codex', 'model', 100), 120)
        self.assertIsNone(model_blocked(self.store, 'codex', 'other', 100))
        self.model_block('codex', '*', 130)
        self.assertEqual(model_blocked(self.store, 'codex', 'other', 100), 130)
        self.assertIsNone(model_blocked(self.store, 'codex', 'model', 130))

    def test_runner_limit_blocks_then_expires(self):
        self.snapshot('account-a', '5h', 100, 120, 'run')
        self.assertIsNone(pick(profile_states(self.store, [self.a], 100), 100))
        state = profile_states(self.store, [self.a], 120)[0]
        self.assertIsNone(state.blocked_until)
        self.assertIsNone(state.max_used_percent)
        self.assertEqual(pick([state], 120), self.a)

    def test_expired_high_usage_is_ignored(self):
        self.snapshot('account-a', 'week', 95, 100)
        self.assertIsNone(profile_states(self.store, [self.a], 100)[0].max_used_percent)

    def test_unmapped_tool_does_not_match_provider_block(self):
        self.model_block('claude', '*', 120)
        req = replace(self.req, tool='claude-subagent')
        self.assertTrue(check(req, self.store, [], self.recommend, 100).allowed)

    def test_pick_lowest_usage(self):
        self.snapshot('account-a', '5h', 10, ts=80)
        self.snapshot('account-a', '5h', 70)
        self.snapshot('account-a', 'week', 90)
        self.snapshot('account-b', '5h', 40)
        states = profile_states(self.store, [self.a, self.b], 100)
        self.assertEqual(states[0].max_used_percent, 90)
        self.assertEqual(pick(states, 100), self.b)

    def test_pick_skips_blocked_until_future(self):
        states = [ProfileState(self.a, 1, 101, None), ProfileState(self.b, 90, 100, None)]
        self.assertEqual(pick(states, 100), self.b)

    def test_pick_tie_least_recently_used(self):
        states = [ProfileState(self.a, 50, None, 20), ProfileState(self.b, 50, None, 10)]
        self.assertEqual(pick(states, 100), self.b)
        self.assertEqual(pick([states[0], replace(states[1], last_used=None)], 100), self.b)

    def test_all_blocked_not_allowed_with_earliest_reset(self):
        self.snapshot('account-a', '5h', 100, 130, 'blocked')
        self.snapshot('account-b', 'week', 100, 120, 'blocked')
        verdict = check(self.req, self.store, [self.a, self.b], self.recommend, 100)
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.earliest_reset, 120)

    def test_block_mode_refuses_blocked_model(self):
        self.model_block('codex', 'model', 120)
        verdict = check(self.req, self.store, [self.a], self.recommend, 100)
        self.assertFalse(verdict.allowed)
        self.assertTrue(verdict.reasons)

    def test_warn_mode_allows_blocked_with_warning(self):
        self.model_block('codex', 'model', 120)
        verdict = check(replace(self.req, guard_mode='warn'), self.store, [self.a], self.recommend, 100)
        self.assertTrue(verdict.allowed)
        self.assertIn('Requested model is blocked', verdict.warnings)

    def test_warning_at_80_percent(self):
        self.snapshot('account-a', 'week', 80)
        verdict = check(self.req, self.store, [self.a], self.recommend, 100)
        self.assertTrue(verdict.allowed)
        self.assertTrue(verdict.warnings)

    @patch('hq.run.guard._installed', side_effect=lambda tool: tool == 'agy')
    def test_auto_mode_fills_from_recommend(self, installed):
        agy = Profile('agy-profile', 'agy', 'unused', 'account-b')
        rows = [{'provider': 'codex', 'model': 'first'},
                {'provider': 'antigravity', 'model': 'second'}]
        calls = []
        verdict = check(replace(self.req, guard_mode='auto'), self.store, [self.a, agy],
                        lambda kind: calls.append(kind) or rows, 100)
        self.assertTrue(verdict.allowed)
        self.assertEqual(verdict.suggestion['tool'], 'agy')
        self.assertEqual(verdict.suggestion['model'], 'second')
        self.assertEqual(verdict.profile, 'agy-profile')
        self.assertEqual(calls, ['hq-run'])

    @patch('hq.run.guard._installed', return_value=True)
    def test_auto_skips_model_block(self, installed):
        self.model_block('codex', 'first', 120)
        rows = [{'provider': 'codex', 'model': 'first'},
                {'provider': 'codex', 'model': 'second'}]
        verdict = check(replace(self.req, guard_mode='auto'), self.store, [self.a],
                        lambda kind: rows, 100)
        self.assertEqual(verdict.suggestion['model'], 'second')

    @patch('hq.run.guard._installed', return_value=False)
    def test_auto_mode_no_installed_tool_refused(self, installed):
        verdict = check(replace(self.req, guard_mode='auto'), self.store, [self.a],
                        lambda kind: [{'provider': 'codex', 'model': 'model'}], 100)
        self.assertFalse(verdict.allowed)

    def test_recommend_unavailable_is_warning_not_crash(self):
        verdict = check(self.req, self.store, [self.a], lambda kind: None, 100)
        self.assertTrue(verdict.allowed)
        self.assertTrue(verdict.warnings)

    def test_snapshot_expiry_future_and_last_used(self):
        self.snapshot('account-a', '5h', 30, ts=80)
        self.snapshot('account-a', '5h', 100, 100, 'blocked', 90)
        self.snapshot('account-a', 'week', 99, ts=101)
        self.store.insert_run({'id': 'run_a', 'tool': 'codex', 'account': 'account-a',
                               'project': 'p', 'workdir': 'p', 'level': 'edit',
                               'guard_mode': 'block', 'status': 'done',
                               'started_at': 50, 'time_limit_s': 60})
        state = profile_states(self.store, [self.a], 100)[0]
        self.assertEqual((state.max_used_percent, state.blocked_until, state.last_used), (None, None, 50))

    @patch('hq.run.guard._installed', return_value=True)
    def test_auto_skips_blocked_profile_and_recommendation(self, installed):
        self.snapshot('account-a', '5h', 100, 120, 'blocked')
        self.model_block('codex', 'm', 120)
        agy = Profile('agy', 'agy', 'unused', 'account-b')
        rows = [{'provider': 'codex', 'model': 'm'},
                {'provider': 'codex', 'model': 'n'},
                {'provider': 'antigravity', 'model': 'other'}]
        verdict = check(replace(self.req, guard_mode='auto'), self.store, [self.a, agy],
                        lambda kind: rows, 100)
        self.assertTrue(verdict.allowed)
        self.assertEqual((verdict.suggestion['tool'], verdict.profile), ('agy', 'agy'))
