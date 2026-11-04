import json
import unittest

from hq.run.plan import PlanError, parse_plan, plan_hash


def output(steps):
    return '```json\n' + json.dumps({'steps': steps}) + '\n```'


def step(**changes):
    return dict({'id': 's1', 'title': 'Title', 'prompt': 'Do it', 'kind': 'code',
                 'depends_on': [], 'tool': None}, **changes)


class PlanTests(unittest.TestCase):
    def parse(self, text, limit=6, level='edit'):
        return parse_plan(text, limit, level, {'codex', 'claude'})

    def test_parse_valid_plan(self):
        plan = self.parse(output([step(), step(id='s2', kind='review', depends_on=['s1'], tool='codex')]))
        self.assertEqual(plan.steps[1].depends_on, ('s1',))
        self.assertEqual(plan.steps[1].level, 'read_only')

    def test_hostile_plans(self):
        cases = [output([step(tool='evil')]), output([step(depends_on=['s2']), step(id='s2', depends_on=['s1'])]),
                 output([step(id=f's{i}') for i in range(11)]), output([step(prompt='x' * 1_000_000)]),
                 'not JSON', output([step(id='bad')]), output([step(depends_on=['s2'])]),
                 output([step(title='x' * 121)]), output([step(kind='unknown')])]
        for value in cases:
            with self.subTest(value=value[:80]), self.assertRaises(PlanError):
                self.parse(value)
        for level in ('full_auto', 'unknown'):
            with self.assertRaises(PlanError): self.parse(output([step()]), level=level)

    def test_levels_capped_and_never_full_auto(self):
        for maximum, expected in [('read_only', 'read_only'), ('ask', 'ask'), ('edit', 'edit')]:
            plan = self.parse(output([step(level='full_auto')]), level=maximum)
            self.assertEqual(plan.steps[0].level, expected)

    def test_last_json_block_used(self):
        plan = self.parse(output([step()]) + '\n' + output([step(id='s2')]))
        self.assertEqual(plan.steps[0].id, 's2')

    def test_plan_hash_stable(self):
        self.assertEqual(plan_hash(self.parse(output([step()]))), plan_hash(self.parse(output([step()]))))
