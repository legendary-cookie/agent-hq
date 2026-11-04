"""Run API operations and their shared validation."""
import hashlib
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from hq.run import guard, pool, request, workspace
from hq.run.approvals import ApprovalBook, AnsweredError, ExpiredError
from hq.run.config import load_projects, load_run_config
from hq.run.launchers import REGISTRY
from hq.run.nonce import NonceBook
from hq.run.runner import Busy, NotIdle, Runner


@dataclass(frozen=True)
class MessageKey:
    run_id: str
    text_hash: str


class RunError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class RunService:
    def __init__(self, store, cfg, config_dir, data_dir, recommend, *, launchers=None, runner_factory=None, pu=None):
        self.store, self.cfg, self.recommend = store, cfg, recommend
        self.run_cfg = load_run_config(Path(config_dir))
        self.projects = load_projects(Path(config_dir))
        self.launchers = REGISTRY if launchers is None else launchers
        self.nonces = NonceBook()
        self.pu_dir = Path(pu) if pu is not None else None
        self.pu_failures = 0
        self._lock = threading.RLock()
        self._viewed = {}
        self.on_event = lambda event: None
        self.approvals = ApprovalBook(store, self.run_cfg.approval_timeout_min * 60,
                                      lambda event: self.on_event(event))
        self.runner = (runner_factory or Runner)(store, self.run_cfg, self.launchers, Path(data_dir)/'worktrees',
            self.pu_log, on_event=lambda event: self.on_event(event), pu_limit=self.pu_limit, projects=self.projects, approvals=self.approvals, profiles=self._profiles())

    def _pu(self, command, provider, model, *args):
        if self.pu_dir is None: return
        provider = {'agy': 'antigravity', 'claude': 'claude'}.get(provider, provider)
        try:
            subprocess.run([sys.executable, str(self.pu_dir/'pu.py'), command, provider, model or '', *map(str, args)],
                           timeout=20, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            with self._lock: self.pu_failures += 1

    def pu_log(self, provider, model, task_kind, outcome, duration_s, note):
        args = [task_kind, outcome, '--duration', duration_s]
        if note: args += ['--note', note]
        self._pu('log', provider, model, *args)

    def pu_limit(self, provider, model, reset_in_h):
        args = ['--reason', 'hq-run']
        if reset_in_h is not None: args += ['--in', reset_in_h]
        self._pu('limit', provider, model, *args)

    def _profiles(self):
        return [p for account in self.cfg.accounts for p in account.profiles]

    def _tools(self):
        return {name: launcher for name, launcher in self.launchers.items()
                if (getattr(launcher, 'tested', False) or self.run_cfg.show_untested)
                and self.runner.executables.get(name) is not None}

    def _enabled(self):
        if not self.run_cfg.run_mode: raise RunError(403, 'run mode is off')

    def _parse(self, data):
        try: return request.parse_request(data, self.run_cfg, self.projects, {n: l.levels() for n,l in self._tools().items()})
        except (ValueError, TypeError, AttributeError) as exc: raise RunError(400, str(exc)) from exc

    def _verdict(self, req):
        verdict = guard.check(req, self.store, self._profiles(), self.recommend, time.time(),
                              offered=set(self._tools()))
        if req.guard_mode == 'auto' and verdict.allowed:
            suggestion = verdict.suggestion
            candidate = asdict(req) | {'tool': suggestion['tool'], 'model': suggestion.get('model'), 'profile': suggestion.get('profile')}
            try: self._parse(candidate)
            except RunError:
                verdict.allowed = False
                verdict.reasons.append('Recommended tool or level is unavailable')
        return verdict

    def options(self):
        states = pool.profile_states(self.store, self._profiles(), time.time())
        return {'run_mode': self.run_cfg.run_mode,
                'tools': {n: {'levels': sorted(l.levels()), 'harness': l.harness, 'tested': getattr(l, 'tested', False), 'can_ask': getattr(l, 'can_ask', False), 'steer_mode': getattr(l, 'steer_mode', None)} for n,l in self._tools().items()},
                'projects': [p.name for p in self.projects],
                'profiles': [{'id': s.profile.id, 'tool': s.profile.tool, 'account_id': s.profile.account_id,
                              'max_used_percent': s.max_used_percent, 'blocked_until': s.blocked_until} for s in states],
                'defaults': {'level':'edit', 'guard_mode':'block', 'time_limit_min':self.run_cfg.default_time_limit_min},
                'max_concurrent': self.run_cfg.max_concurrent}

    def prepare(self, data):
        self._enabled(); req = self._parse(data); verdict = self._verdict(req)
        resolved = req
        if verdict.allowed and req.guard_mode == 'auto':
            resolved = replace(req, tool=verdict.suggestion['tool'], model=verdict.suggestion.get('model'), profile=verdict.profile)
        nonce, expires = self.nonces.issue(req, resolved) if verdict.allowed else (None, None)
        return {'verdict': asdict(verdict), 'nonce': nonce, 'expires_at': expires}

    def start(self, data):
        self._enabled(); req = self._parse(data)
        resolved = self.nonces.redeem_resolved(data.get('nonce'), req)
        if resolved is None: raise RunError(403, 'confirm again')
        req = resolved
        try: self._parse(asdict(req))
        except RunError as exc: raise RunError(409, str(exc)) from exc
        verdict = (guard.check(replace(req, guard_mode='block'), self.store, self._profiles(), lambda _: [], time.time(), offered=set(self._tools()))
                   if req.guard_mode == 'auto' else self._verdict(req))
        if not verdict.allowed: raise RunError(409, '; '.join(verdict.reasons))
        profile = next ( (p for p in self._profiles() if p.id == verdict.profile and p.tool == req.tool), None)
        try: return self.runner.start(req, profile)
        except Busy as exc: raise RunError(409, str(exc)) from exc
        except ValueError as exc: raise RunError(400, str(exc)) from exc

    def list(self):
        self._enabled(); return self.store.list_runs()

    def get(self, rid):
        self._enabled(); run = self.store.get_run(rid)
        if run is None: raise RunError(404, 'run not found')
        return run

    def output(self, rid):
        self.get(rid); lines = self.runner.output(rid)
        if lines is None: raise RunError(410, 'output no longer available')
        return lines

    def stop(self, rid):
        self.get(rid); self.runner.stop(rid); return {'ok': True}

    def approvals_list(self, state):
        self._enabled()
        if state != 'waiting': raise RunError(400, 'Invalid approval state')
        return [asdict(item) for item in self.approvals.waiting()]

    def answer(self, aid, data):
        self._enabled()
        note = data.get('note')
        if note is not None and not isinstance(note, str): raise RunError(400, 'Invalid note')
        try: return asdict(self.approvals.answer(aid, data.get('decision'), note))
        except KeyError as exc: raise RunError(404, 'approval not found') from exc
        except AnsweredError as exc: raise RunError(409, 'approval already answered') from exc
        except ExpiredError as exc: raise RunError(410, 'approval expired') from exc
        except ValueError as exc: raise RunError(400, str(exc)) from exc

    def _message_key(self, rid, data):
        self.get(rid)
        text = data.get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > 100000:
            raise RunError(400, 'Invalid message')
        return MessageKey(rid, hashlib.sha256(text.encode('utf-8')).hexdigest())

    def message_prepare(self, rid, data):
        key = self._message_key(rid, data)
        nonce, expires = self.nonces.issue(key)
        return {'nonce': nonce, 'expires_at': expires}

    def message(self, rid, data):
        key = self._message_key(rid, data)
        if not self.nonces.redeem(data.get('nonce'), key): raise RunError(403, 'confirm again')
        run = self.get(rid)
        req = request.RunRequest(run['tool'], run['model'], run['project'], run['prompt_preview'],
                                 run['level'], run['guard_mode'], run['account'], 1)
        # Auto was resolved at start; recheck that actual selection rather than selecting again.
        if req.guard_mode == 'auto': req = replace(req, guard_mode='block')
        verdict = guard.check(req, self.store, self._profiles(), self.recommend, time.time(), offered=set(self._tools()))
        if not verdict.allowed: raise RunError(409, '; '.join(verdict.reasons))
        try: self.runner.send(rid, data['text'])
        except (NotIdle, Busy, KeyError) as exc: raise RunError(409, str(exc)) from exc
        except ValueError as exc: raise RunError(409, str(exc)) from exc
        return {'ok': True}

    def _project(self, run):
        project = next((p for p in self.projects if p.name == run['project']), None)
        if project is None or project.path != project.path.resolve() or not project.path.is_dir():
            raise RunError(409, 'project is no longer available')
        return project.path

    def _finished(self, run):
        if run['status'] not in ('idle', 'done', 'failed', 'stopped', 'timed_out', 'rate_limited', 'interrupted'): raise RunError(409, 'run is not finished')

    def diff(self, rid):
        run = self.get(rid)
        commit = None
        try:
            if run['branch']:
                self._finished(run)
                project = self._project(run)
                with workspace._lock:
                    text, truncated = workspace.diff(project, run['branch'], run['base_commit'], workdir=Path(run['workdir']), git_dir=run.get('git_dir'))
                    commit = workspace.revision(project, run['branch'])
            else: text, truncated = '', False
        except ValueError as exc: raise RunError(409, str(exc)) from exc
        except (OSError, subprocess.SubprocessError) as exc: raise RunError(409, f'git failed: {type(exc).__name__}') from exc
        with self._lock: self._viewed[rid] = commit
        return {'diff': text, 'truncated': truncated, 'branch': run['branch'], 'commit': commit}

    def merge(self, rid, data=None):
        run = self.get(rid); self._finished(run)
        with self._lock:
            commit = (data or {}).get('commit')
            if rid not in self._viewed: raise RunError(409, 'view the diff first')
            if not run['branch']: raise RunError(409, 'run has no branch')
            if not commit or commit != self._viewed[rid]: raise RunError(409, 'the run changed after you viewed the diff; view it again')
            try:
                state = workspace.merge(self._project(run), run['branch'], workdir=Path(run['workdir']), git_dir=run.get('git_dir'), commit=commit)
            except ValueError as exc: raise RunError(409, str(exc)) from exc
            except (OSError, subprocess.SubprocessError) as exc: raise RunError(409, f'git failed: {type(exc).__name__}') from exc
            self.store.update_run(rid, merge_state=state)
        return {'merge_state':state}

    def discard(self, rid):
        run = self.get(rid); self._finished(run)
        if not run['branch']: raise RunError(409, 'run has no branch')
        try: workspace.discard(self._project(run), Path(run['workdir']), run['branch'])
        except (OSError, subprocess.SubprocessError) as exc: raise RunError(409, f'git failed: {type(exc).__name__}') from exc
        self.store.update_run(rid, merge_state='discarded')
        return {'merge_state':'discarded'}

    def _goals(self):
        from hq.run.scheduler import GoalScheduler
        with self._lock:
            if not hasattr(self, '_goal_scheduler'):
                self._goal_scheduler = GoalScheduler(self.store, self.runner, self._goal_guard, time.time)
                self._goal_tick()
            return self._goal_scheduler

    def _goal_tick(self):
        self._goal_scheduler.check_timeouts()
        timer = threading.Timer(30, self._goal_tick)
        timer.daemon = True
        timer.start()

    def _goal_guard(self, req):
        self._parse(asdict(req))
        verdict = self._verdict(req)
        if not verdict.allowed: raise ValueError('; '.join(verdict.reasons))
        if req.guard_mode == 'auto':
            req = replace(req, tool=verdict.suggestion['tool'], model=verdict.suggestion.get('model'), profile=verdict.profile)
            self._parse(asdict(req))
        profile = next((p for p in self._profiles() if p.id == verdict.profile and p.tool == req.tool), None)
        return req, profile

    def _goal_call(self, method, *args):
        self._enabled()
        try: return getattr(self._goals(), method)(*args)
        except KeyError as exc: raise RunError(404, 'goal or step not found') from exc
        except (ValueError, TypeError) as exc: raise RunError(400, str(exc)) from exc
        except Busy as exc: raise RunError(409, str(exc)) from exc

    def _goal_key(self, data):
        import json
        body = {k: v for k, v in data.items() if k != 'nonce'}
        return MessageKey('goal', hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest())

    def goal_prepare(self, data):
        self._enabled()
        nonce, expires = self.nonces.issue(self._goal_key(data))
        return {'nonce': nonce, 'expires_at': expires}

    def goal_create(self, data):
        self._enabled()
        if not self.nonces.redeem(data.get('nonce'), self._goal_key(data)): raise RunError(403, 'confirm again')
        return self._goal_call('create', {k: v for k, v in data.items() if k != 'nonce'})

    def goal_list(self):
        self._enabled()
        scheduler = self._goals()
        with scheduler._lock: return [scheduler.get(gid) for gid in scheduler._goals]

    def goal_get(self, gid): return self._goal_call('get', gid)
    def goal_plan(self, gid, data): return self._goal_call('set_plan', gid, data)
    def goal_start_prepare(self, gid): return {'nonce': self._goal_call('prepare_start', gid)}
    def goal_start(self, gid, data): return self._goal_call('start', gid, data.get('nonce'))
    def goal_step(self, gid, sid, action):
        self._goal_call(action + '_step', gid, sid)
        return self.goal_get(gid)
    def goal_stop(self, gid):
        self._goal_call('stop', gid)
        return self.goal_get(gid)

    def goal_diff(self, gid):
        goal = self.goal_get(gid)
        if goal['status'] not in ('done', 'stopped', 'needs_you', 'failed') or not goal.get('branch'): raise RunError(409, 'goal has no finished branch')
        try:
            project = self._project(goal)
            with workspace._lock:
                text, truncated = workspace.diff(project, goal['branch'], goal['base_commit'], workdir=Path(goal['workdir']), git_dir=goal['git_dir'])
                commit = workspace.revision(project, goal['branch'])
        except (ValueError, OSError, subprocess.SubprocessError) as exc: raise RunError(409, str(exc)) from exc
        with self._lock: self._viewed['goal:' + gid] = commit
        return {'diff': text, 'truncated': truncated, 'branch': goal['branch'], 'commit': commit}

    def goal_merge(self, gid, data):
        goal = self.goal_get(gid)
        if goal['status'] != 'done' or not goal.get('branch'): raise RunError(409, 'goal is not finished')
        with self._lock:
            commit = data.get('commit')
            if not commit or self._viewed.get('goal:' + gid) != commit: raise RunError(409, 'view the current diff first')
            try: state = workspace.merge(self._project(goal), goal['branch'], workdir=Path(goal['workdir']), git_dir=goal['git_dir'], commit=commit)
            except (ValueError, OSError, subprocess.SubprocessError) as exc: raise RunError(409, str(exc)) from exc
            self._viewed.pop('goal:' + gid, None)
        return {'merge_state': state}

    def goal_discard(self, gid):
        goal = self.goal_get(gid)
        if goal['status'] not in ('done', 'stopped', 'failed') or not goal.get('branch'): raise RunError(409, 'goal is not finished')
        try: workspace.discard(self._project(goal), Path(goal['workdir']), goal['branch'])
        except (ValueError, OSError, subprocess.SubprocessError) as exc: raise RunError(409, str(exc)) from exc
        return {'merge_state': 'discarded'}
