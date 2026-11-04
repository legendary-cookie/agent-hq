import json
import tempfile
import unittest
from pathlib import Path

from hq.run import workspace
from hq.run.scheduler import GoalScheduler
from hq.store import Store


class FakeRunner:
    def __init__(self, store, project, root):
        self.store, self.projects, self.worktrees_root = store, {'project': project}, root
        self.started, self.stopped = [], []

    def start(self, req, profile, *, base_ref=None, goal_id=None, step_id=None):
        rid = 'run_' + str(len(self.started))
        path, branch, base, admin = workspace.create_from(self.projects[req.project], rid, self.worktrees_root, base_ref) if base_ref else (self.projects[req.project], None, None, None)
        self.store.insert_run(dict(id=rid, tool=req.tool, project=req.project, workdir=str(path), branch=branch,
            base_commit=base, git_dir=admin, level=req.level, guard_mode=req.guard_mode, status='running',
            time_limit_s=60, goal_id=goal_id, step_id=step_id))
        self.started.append(rid)
        return self.store.get_run(rid)

    def stop(self, rid): self.stopped.append(rid)
    def output(self, rid): return []


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(); self.project = self.root / 'project'; self.project.mkdir()
        self.root.joinpath('worktrees').mkdir()
        workspace._git(workspace._repo(self.project), 'init')
        (self.project / 'file').write_text('base\n', encoding='utf-8')
        workspace._git(workspace._repo(self.project), 'add', 'file')
        workspace._git(workspace._repo(self.project), '-c', 'user.name=test', '-c', 'user.email=test@localhost', 'commit', '-m', 'base')
        self.store = Store(self.root / 'db'); self.addCleanup(self.store.close)
        self.runner = FakeRunner(self.store, self.project, self.root / 'worktrees')
        self.now = 100.0
        self.scheduler = GoalScheduler(self.store, self.runner, lambda req: (req, None), lambda: self.now)
        self.goal = self.scheduler.create({'project': 'project', 'text': 'goal', 'planner_tool': 'codex', 'max_concurrent': 2})

    def plan(self, dependencies=((),), names=None):
        names = names or ['file' + str(i) for i in range(len(dependencies))]
        steps = [dict(id=f's{i+1}', title=name, prompt=name, kind='code', depends_on=list(deps), tool='codex') for i, (name, deps) in enumerate(zip(names, dependencies))]
        self.scheduler.set_plan(self.goal['id'], json.dumps({'steps': steps}))
        return self.scheduler.prepare_start(self.goal['id'])

    def start(self, dependencies=((),), names=None):
        self.scheduler.start(self.goal['id'], self.plan(dependencies, names))

    def finish(self, rid, text='change\n', filename=None, status='done'):
        run = self.store.get_run(rid)
        if filename: (Path(run['workdir']) / filename).write_text(text, encoding='utf-8')
        self.store.update_run(rid, status=status)
        self.scheduler.on_run_finished(rid)

    def test_steps_run_in_dependency_order(self):
        self.start(((), ('s1',)))
        self.assertEqual(len(self.runner.started), 2)
        self.finish(self.runner.started[-1], filename='one')
        self.assertEqual(len(self.runner.started), 3)

    def test_parallel_steps_up_to_limit(self):
        self.start(((), (), ()))
        self.assertEqual(len(self.runner.started), 3)
        self.finish(self.runner.started[1], filename='one')
        self.assertEqual(len(self.runner.started), 4)

    def test_step_results_merged_into_goal_branch(self):
        self.start(); self.finish(self.runner.started[-1], filename='result')
        goal = self.scheduler.get(self.goal['id'])
        self.assertEqual(goal['status'], 'done')
        self.assertEqual((self.root / 'worktrees' / ('goal-' + goal['id']) / 'result').read_text(), 'change\n')

    def test_step_conflict_pauses_goal(self):
        self.start(((), ()))
        self.finish(self.runner.started[1], 'one\n', 'file')
        self.finish(self.runner.started[2], 'two\n', 'file')
        goal = self.scheduler.get(self.goal['id'])
        self.assertEqual(goal['status'], 'needs_you')
        for rid in self.runner.started[1:]:
            branch = self.store.get_run(rid)['branch']
            self.assertEqual(workspace._git(workspace._repo(self.project), 'rev-parse', '--verify', branch).returncode, 0)
        self.assertEqual((self.project / 'file').read_text(), 'base\n')

    def test_checkout_untouched(self):
        self.start(); self.finish(self.runner.started[-1], filename='file')
        self.assertEqual((self.project / 'file').read_text(), 'base\n')

    def test_failure_pauses_with_retry_skip(self):
        self.start(); self.finish(self.runner.started[-1], status='failed')
        self.assertEqual(self.scheduler.get(self.goal['id'])['status'], 'needs_you')
        self.scheduler.retry_step(self.goal['id'], 's1')
        self.finish(self.runner.started[-1], status='failed')
        self.scheduler.skip_step(self.goal['id'], 's1')
        self.assertEqual(self.scheduler.get(self.goal['id'])['status'], 'done')

    def test_total_time_limit_stops_goal(self):
        self.start(); self.now += 91 * 60
        self.scheduler.check_timeouts()
        self.assertEqual(self.scheduler.get(self.goal['id'])['status'], 'stopped')
        self.assertIn(self.runner.started[-1], self.runner.stopped)

    def test_start_requires_nonce_bound_to_plan(self):
        nonce = self.plan(); self.plan(names=['different'])
        with self.assertRaises(ValueError): self.scheduler.start(self.goal['id'], nonce)
