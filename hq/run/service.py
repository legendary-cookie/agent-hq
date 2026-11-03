"""Run API operations and their shared validation."""
import subprocess
import sys
import threading
import time
from dataclasses import asdict, replace
from pathlib import Path

from hq.run import guard, pool, request, workspace
from hq.run.config import load_projects, load_run_config
from hq.run.launchers import REGISTRY
from hq.run.nonce import NonceBook
from hq.run.runner import Busy, Runner


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
        self._viewed = set()
        self.on_event = lambda event: None
        self.runner = (runner_factory or Runner)(store, self.run_cfg, self.launchers, Path(data_dir)/'worktrees',
            self.pu_log, on_event=lambda event: self.on_event(event), pu_limit=self.pu_limit, projects=self.projects)

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
                'tools': {n: {'levels': sorted(l.levels()), 'harness': l.harness, 'tested': getattr(l, 'tested', False)} for n,l in self._tools().items()},
                'projects': [p.name for p in self.projects],
                'profiles': [{'id': s.profile.id, 'tool': s.profile.tool, 'account_id': s.profile.account_id,
                              'max_used_percent': s.max_used_percent, 'blocked_until': s.blocked_until} for s in states],
                'defaults': {'level':'edit', 'guard_mode':'block', 'time_limit_min':self.run_cfg.default_time_limit_min},
                'max_concurrent': self.run_cfg.max_concurrent}

    def prepare(self, data):
        self._enabled(); req = self._parse(data); verdict = self._verdict(req)
        nonce, expires = self.nonces.issue(req) if verdict.allowed else (None, None)
        return {'verdict': asdict(verdict), 'nonce': nonce, 'expires_at': expires}

    def start(self, data):
        self._enabled(); req = self._parse(data)
        if not self.nonces.redeem(data.get('nonce'), req): raise RunError(403, 'confirm again')
        verdict = self._verdict(req)
        if not verdict.allowed: raise RunError(409, '; '.join(verdict.reasons))
        if req.guard_mode == 'auto':
            req = replace(req, tool=verdict.suggestion['tool'], model=verdict.suggestion.get('model'), profile=verdict.profile)
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

    def _project(self, run):
        project = next((p for p in self.projects if p.name == run['project']), None)
        if project is None or project.path != project.path.resolve() or not project.path.is_dir():
            raise RunError(409, 'project is no longer available')
        return project.path

    def _finished(self, run):
        if run['status'] in ('queued', 'starting', 'running'): raise RunError(409, 'run is not finished')

    def diff(self, rid):
        run = self.get(rid)
        if run['branch']:
            self._finished(run)
            text, truncated = workspace.diff(self._project(run), run['branch'], run['base_commit'], workdir=Path(run['workdir']))
        else: text, truncated = '', False
        with self._lock: self._viewed.add(rid)
        return {'diff':text, 'truncated':truncated, 'branch':run['branch']}

    def merge(self, rid):
        run = self.get(rid); self._finished(run)
        with self._lock:
            if rid not in self._viewed: raise RunError(409, 'view the diff first')
            if not run['branch']: raise RunError(409, 'run has no branch')
            state = workspace.merge(self._project(run), run['branch'], workdir=Path(run['workdir']))
            self.store.update_run(rid, merge_state=state)
        return {'merge_state':state}

    def discard(self, rid):
        run = self.get(rid); self._finished(run)
        if not run['branch']: raise RunError(409, 'run has no branch')
        workspace.discard(self._project(run), Path(run['workdir']), run['branch'])
        self.store.update_run(rid, merge_state='discarded')
        return {'merge_state':'discarded'}
