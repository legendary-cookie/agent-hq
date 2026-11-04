"""Coordinate guarded runs on an isolated goal branch."""
import json
import secrets
import threading
from dataclasses import asdict
from pathlib import Path

from hq.run import workspace
from hq.run.plan import parse_plan, plan_hash, planner_prompt
from hq.run.request import RunRequest


class GoalScheduler:
    def __init__(self, store, runner, service_guard, clock):
        self.store, self.runner, self.guard, self.clock = store, runner, service_guard, clock
        self._lock = threading.RLock()
        self._goals, self._nonces, self._timers = {}, {}, {}
        if hasattr(runner, 'on_event'):
            previous = runner.on_event
            def event(value):
                previous(value)
                if value.get('type') == 'run' and value.get('status') in ('done', 'failed', 'stopped', 'timed_out', 'interrupted', 'rate_limited'):
                    thread = threading.Thread(target=self.on_run_finished, args=(value['id'],), daemon=True)
                    thread.start()
            runner.on_event = event

    def _save(self, goal):
        fields = ('id', 'project', 'text_preview', 'max_level', 'status', 'branch', 'base_commit',
                  'plan_json', 'step_limit', 'time_limit_s', 'created_at', 'ended_at')
        with self.store._lock, self.store._writer:
            self.store._writer.execute('INSERT OR REPLACE INTO goals (' + ','.join(fields) + ') VALUES (' + ','.join('?' for _ in fields) + ')', tuple(goal.get(k) for k in fields))

    def get(self, goal_id):
        return dict(self._goals[goal_id])

    def _guarded_start(self, req, **options):
        resolved, profile = self.guard(req)
        if (resolved.level != req.level or resolved.level == 'full_auto' or resolved.project != req.project
                or resolved.prompt != req.prompt or resolved.tool not in self._tools()):
            raise ValueError('Guard returned an invalid run')
        launchers = getattr(self.runner, 'launchers', {})
        if launchers and resolved.level not in launchers[resolved.tool].levels():
            raise ValueError('Level is not offered for this tool')
        return self.runner.start(resolved, profile, **options)

    def create(self, goal_req):
        limit = goal_req.get('step_limit', 6)
        minutes = goal_req.get('time_limit_min', 90)
        concurrent = goal_req.get('max_concurrent', 1)
        level = goal_req.get('max_level', 'edit')
        text, project = goal_req.get('text'), goal_req.get('project')
        if (type(limit) is not int or not 1 <= limit <= 10 or type(minutes) is not int or not 1 <= minutes <= 240
                or type(concurrent) is not int or concurrent < 1 or level not in ('read_only', 'ask', 'edit')
                or not isinstance(text, str) or not text.strip() or len(text) > 100000 or project not in self.runner.projects):
            raise ValueError('Invalid goal')
        gid = secrets.token_urlsafe(9)
        goal = dict(id=gid, project=project, text_preview=text[:200], max_level=level, status='planning', branch=None,
                    base_commit=None, plan_json=None, step_limit=limit, time_limit_s=minutes * 60,
                    created_at=self.clock(), ended_at=None, max_concurrent=concurrent, steps={}, planner_id=None)
        with self._lock:
            self._goals[gid] = goal
            self._save(goal)
            req = RunRequest(goal_req.get('planner_tool', 'claude'), None, project, planner_prompt(text, limit), 'read_only', 'block', None, minutes)
            try:
                run = self._guarded_start(req, goal_id=gid)
                goal['planner_id'] = run['id']
            except Exception:
                goal['status'] = 'failed'; goal['ended_at'] = self.clock(); self._save(goal)
                raise
        return self.get(gid)

    def _tools(self):
        launchers = getattr(self.runner, 'launchers', None)
        if launchers is None: return {'codex', 'claude', 'opencode', 'agy'}
        return {name for name, launcher in launchers.items() if getattr(launcher, 'tested', False)}

    def set_plan(self, goal_id, plan_json):
        with self._lock:
            goal = self._goals[goal_id]
            if goal['status'] not in ('planning', 'review'): raise ValueError('Goal is not in review')
            text = plan_json if isinstance(plan_json, str) else json.dumps(plan_json)
            plan = parse_plan('```json\n' + text + '\n```', goal['step_limit'], goal['max_level'], self._tools())
            goal['plan_json'] = json.dumps(asdict(plan), sort_keys=True, separators=(',', ':'))
            goal['steps'] = {step.id: {'status': 'pending', 'run_id': None} for step in plan.steps}
            goal['status'] = 'review'; self._save(goal)
            return self.get(goal_id)

    def _plan(self, goal):
        return parse_plan('```json\n' + goal['plan_json'] + '\n```', goal['step_limit'], goal['max_level'], self._tools())

    def prepare_start(self, goal_id):
        with self._lock:
            goal = self._goals[goal_id]
            if goal['status'] != 'review': raise ValueError('Goal is not in review')
            nonce = secrets.token_urlsafe(24)
            self._nonces[nonce] = (goal_id, plan_hash(self._plan(goal)), self.clock() + 300)
            return nonce

    def start(self, goal_id, nonce):
        with self._lock:
            goal = self._goals[goal_id]
            token = self._nonces.pop(nonce, None)
            if goal['status'] != 'review' or token != (goal_id, plan_hash(self._plan(goal)), token[2] if token else None) or token[2] < self.clock():
                raise ValueError('Confirm the current plan')
            workdir, branch, base, admin = workspace.create_goal(self.runner.projects[goal['project']], goal_id, self.runner.worktrees_root)
            goal.update(status='running', branch=branch, base_commit=base, workdir=str(workdir), git_dir=admin, started_at=self.clock())
            self._save(goal)
            timer = threading.Timer(goal['time_limit_s'], self.check_timeouts)
            timer.daemon = True; self._timers[goal_id] = timer; timer.start()
            self._schedule(goal)
            return self.get(goal_id)

    def _schedule(self, goal):
        if goal['status'] != 'running': return
        if self.clock() >= goal['started_at'] + goal['time_limit_s']:
            self.stop(goal['id']); return
        plan = self._plan(goal)
        steps = goal['steps']
        for step in plan.steps:
            if sum(item['status'] == 'running' for item in steps.values()) >= goal['max_concurrent']: break
            if steps[step.id]['status'] != 'pending' or any(steps[d]['status'] not in ('done', 'skipped') for d in step.depends_on): continue
            req = RunRequest(step.tool or 'codex', None, goal['project'], step.prompt, step.level,
                             'block' if step.tool else 'auto', None, max(1, int((goal['started_at'] + goal['time_limit_s'] - self.clock()) // 60)))
            steps[step.id]['status'] = 'starting'
            try:
                run = self._guarded_start(req, base_ref=goal['branch'], goal_id=goal['id'], step_id=step.id)
                steps[step.id].update(status='running', run_id=run['id'])
                finished = self.store.get_run(run['id'])
                if finished['status'] in ('done', 'failed', 'stopped', 'timed_out', 'interrupted', 'rate_limited'):
                    self.on_run_finished(run['id'])
            except Exception:
                steps[step.id]['status'] = 'failed'; goal['status'] = 'needs_you'; self._save(goal); return
        if all(item['status'] in ('done', 'skipped') for item in steps.values()):
            goal['status'] = 'done'; goal['ended_at'] = self.clock(); self._save(goal)
            timer = self._timers.pop(goal['id'], None)
            if timer: timer.cancel()

    def on_run_finished(self, run_id):
        with self._lock:
            run = self.store.get_run(run_id)
            if not run or not run.get('goal_id') or run['goal_id'] not in self._goals: return
            goal = self._goals[run['goal_id']]
            if not run.get('step_id'):
                if goal['status'] != 'planning': return
                try:
                    if run['status'] != 'done': raise ValueError('Planner failed')
                    plan = parse_plan('\n'.join(self.runner.output(run_id)), goal['step_limit'], goal['max_level'], self._tools())
                    self.set_plan(goal['id'], json.dumps(asdict(plan)))
                except (ValueError, TypeError):
                    goal['status'] = 'failed'; goal['ended_at'] = self.clock(); self._save(goal)
                return
            if goal['status'] not in ('running', 'needs_you'): return
            step = goal['steps'].get(run['step_id'])
            if not step or step['run_id'] != run_id or step['status'] != 'running': return
            if run['status'] == 'done':
                try:
                    project = self.runner.projects[goal['project']]
                    workspace.snapshot(Path(run['workdir']), run_id, project=project, git_dir=run['git_dir'])
                    result = workspace.merge_into_branch(project, goal['branch'], run['branch'], Path(goal['workdir']), goal['git_dir'])
                    step['status'] = 'done' if result == 'merged' else 'failed'
                except Exception:
                    step['status'] = 'failed'
            else: step['status'] = 'failed'
            if step['status'] == 'failed': goal['status'] = 'needs_you'; self._save(goal)
            self._schedule(goal)

    def retry_step(self, goal_id, step_id):
        with self._lock:
            goal = self._goals[goal_id]; step = goal['steps'][step_id]
            if goal['status'] != 'needs_you' or step['status'] != 'failed': raise ValueError('Step cannot be retried')
            step.update(status='pending', run_id=None)
            goal['status'] = 'needs_you' if any(item['status'] == 'failed' for item in goal['steps'].values()) else 'running'
            self._save(goal); self._schedule(goal)

    def skip_step(self, goal_id, step_id):
        with self._lock:
            goal = self._goals[goal_id]; step = goal['steps'][step_id]
            if goal['status'] != 'needs_you' or step['status'] != 'failed': raise ValueError('Step cannot be skipped')
            step['status'] = 'skipped'
            goal['status'] = 'needs_you' if any(item['status'] == 'failed' for item in goal['steps'].values()) else 'running'
            self._save(goal); self._schedule(goal)

    def stop(self, goal_id):
        with self._lock:
            goal = self._goals[goal_id]
            if goal['status'] in ('done', 'stopped', 'failed'): return
            goal['status'] = 'stopped'; goal['ended_at'] = self.clock(); self._save(goal)
            timer = self._timers.pop(goal_id, None)
            if timer: timer.cancel()
            for item in goal['steps'].values():
                if item['status'] == 'running': self.runner.stop(item['run_id'])
            if goal.get('planner_id') and not goal.get('started_at'): self.runner.stop(goal['planner_id'])

    def check_timeouts(self):
        with self._lock:
            for goal in list(self._goals.values()):
                if goal['status'] in ('running', 'needs_you') and self.clock() >= goal['started_at'] + goal['time_limit_s']:
                    self.stop(goal['id'])
