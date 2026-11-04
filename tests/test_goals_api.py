import unittest
from unittest.mock import MagicMock

from hq.run.service import RunService, RunError
import tests.test_run_api as run_api


class GoalServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = RunService.__new__(RunService)
        self.service._lock = __import__('threading').RLock()
        self.service._enabled = lambda: None
        self.service._goal_scheduler = MagicMock()
        self.service._goal_scheduler.get.return_value = {'id': 'g', 'status': 'review'}

    def test_start_nonce_bound_to_plan(self):
        self.service._goal_scheduler.start.side_effect = ValueError('Confirm the current plan')
        with self.assertRaises(RunError): self.service.goal_start('g', {'nonce': 'old'})

    def test_plan_edit_revalidated(self):
        self.service._goal_scheduler.set_plan.side_effect = ValueError('invalid plan')
        with self.assertRaises(RunError): self.service.goal_plan('g', {'steps': []})

    def test_merge_viewed_commit_only(self):
        self.service._lock = __import__('threading').RLock()
        self.service._viewed = {}
        self.service._goal_scheduler.get.return_value = {'id': 'g', 'status': 'done', 'branch': 'branch'}
        with self.assertRaises(RunError): self.service.goal_merge('g', {'commit': 'unseen'})


class GoalHttpTests(unittest.TestCase):
    setUp = run_api.RunHttpTests.setUp
    close_server = run_api.RunHttpTests.close_server
    req = run_api.RunHttpTests.req
    def test_goal_routes_need_run_key(self):
        self.run_key = 'wrong'
        for path, method in [('/api/goals', 'GET'), ('/api/goals/g', 'GET'), ('/api/goals/prepare', 'POST'), ('/api/goals/g/plan', 'PUT')]:
            self.assertEqual(self.req(path, method, {})[0], 403)
