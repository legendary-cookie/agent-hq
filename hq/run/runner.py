import os
import copy
import secrets
import threading
import time
from collections import deque
from dataclasses import replace
from pathlib import Path

from hq.run import env, proc, workspace, winexe
from hq.run.stream import parse_line


class Busy(Exception):
    pass


class Runner:
    def __init__(self, store, run_cfg, launchers, worktrees_root, pu_log, on_event,
                 clock=time.time, pu_limit=None, minute_s=60.0, projects=()):
        self.store, self.run_cfg = store, run_cfg
        self.worktrees_root = Path(worktrees_root)
        self.launchers = {name: copy.copy(launcher) for name, launcher in launchers.items()}
        for launcher in self.launchers.values():
            if hasattr(launcher, 'worktrees_root'):
                launcher.worktrees_root = self.worktrees_root
        self.pu_log, self.on_event, self.clock = pu_log, on_event, clock
        self.pu_limit = pu_limit or (lambda *args: None)
        self.minute_s = minute_s
        self.projects = {project.name: Path(project.path) for project in projects}
        refused = list(self.projects.values()) + [self.worktrees_root]
        self.executables = {name: env.resolve_exe('wsl' if name == 'codex' else launcher.exe, refused)
                            for name, launcher in launchers.items()}
        self._lock = threading.RLock()
        self._runs = {}
        self._shutting_down = False

    def _status(self, rid, status, **fields):
        with self._lock:
            run = self._runs[rid]
            if status in ('running', 'rate_limited') and run['stop']:
                return
            if run['status'] == status:
                if fields: self.store.update_run(rid, **fields)
                return
            run['status'] = status
            self.store.update_run(rid, status=status, **fields)
        self.on_event({'type': 'run', 'id': rid, 'status': status})

    def start(self, req, profile):
        if req.project not in self.projects:
            raise ValueError('Project is not allowlisted')
        project = self.projects[req.project]
        if project != project.resolve() or not project.is_dir():
            raise ValueError('Project path has changed or is invalid')
        launcher = self.launchers[req.tool]
        rid = 'run_' + secrets.token_urlsafe(9)
        now = self.clock()
        rec = dict(id=rid, tool=req.tool, model=req.model, account=profile.id if profile else None,
                   project=req.project, workdir=str(project), level=req.level, guard_mode=req.guard_mode,
                   status='starting', prompt_preview=req.prompt[:200], started_at=now,
                   time_limit_s=req.time_limit_min * self.minute_s)
        with self._lock:
            if self._shutting_down or sum(not r['finished'] for r in self._runs.values()) >= self.run_cfg.max_concurrent:
                raise Busy('Maximum concurrent runs reached')
            self.store.insert_run(rec)
            state = dict(status='starting', stop=None, child=None, finished=False,
                         output=deque(maxlen=500), warnings=0, thread=None)
            self._runs[rid] = state
            thread = threading.Thread(target=self._run, args=(rid, req, profile, launcher, project), daemon=True)
            state['thread'] = thread
        self.on_event({'type': 'run', 'id': rid, 'status': 'starting'})
        result = self.store.get_run(rid)
        thread.start()
        return result

    def _run(self, rid, req, profile, launcher, project):
        child = None
        timer = None
        exit_code = None
        started = self.clock()
        try:
            with self._lock:
                stopped = self._runs[rid]['stop']
            if stopped: return
            workdir, branch = project, None
            if req.level != 'read_only':
                workdir, branch, _ = workspace.create(project, rid, self.worktrees_root)
            self.store.update_run(rid, workdir=str(workdir), branch=branch)
            launch = launcher.build(req, profile, workdir, rid, os.environ,
                                    self.run_cfg.pass_env.get(req.tool, []))
            executable = self.executables.get(req.tool)
            if executable is None: raise FileNotFoundError('Executable unavailable')
            argv = winexe.final_argv(launch, executable)
            if launch.argv[0] == 'wsl': argv[0] = str(executable)
            launch = replace(launch, argv=argv)
            with self._lock:
                stopped = self._runs[rid]['stop']
            if stopped: return
            child = proc.spawn(launch, lambda: self._stopped(rid))
            with self._lock:
                self._runs[rid]['child'] = child
                stopped = self._runs[rid]['stop']
            if stopped:
                child.kill_tree()
            else:
                with self._lock:
                    stopped = self._runs[rid]['stop']
                    if not stopped:
                        self._runs[rid]['status'] = 'running'
                        self.store.update_run(rid, status='running')
                if stopped:
                    child.kill_tree()
                else:
                    self.on_event({'type': 'run', 'id': rid, 'status': 'running'})
                timer = threading.Timer(req.time_limit_min * self.minute_s, self._request_stop, args=(rid, 'timed_out'))
                timer.daemon = True
                timer.start()
                if launch.stdin_text is not None:
                    try:
                        child.stdin.write(launch.stdin_text)
                        child.stdin.close()
                    except (BrokenPipeError, OSError): pass
            for line in child.stdout:
                line = line.rstrip('\r\n')
                with self._lock: self._runs[rid]['output'].append(line)
                event = parse_line(req.tool, line, launcher)
                if event is None:
                    with self._lock: self._runs[rid]['warnings'] += 1
                    continue
                if event.session_id:
                    with self._lock:
                        if not self._runs[rid].get('session_id'):
                            self._runs[rid]['session_id'] = event.session_id
                            self.store.update_run(rid, session_id=event.session_id)
                if event.rate_limited:
                    self._rate_limit(rid, req, profile, launcher)
            exit_code = child.wait()
        except Exception:
            if child and child.alive():
                child.kill_tree()
                exit_code = child.wait()
        finally:
            if timer: timer.cancel()
            if child: child.close()
            with self._lock:
                state = self._runs[rid]
                status = state['stop'] or ('rate_limited' if state.get('limited') else ('done' if exit_code == 0 else 'failed'))
                state['finished'] = True
            outcome = {'done': 'ok', 'failed': 'error', 'timed_out': 'error', 'interrupted': 'error',
                       'rate_limited': 'rate_limited', 'stopped': 'cancelled'}[status]
            try:
                self.pu_log(launcher.name, req.model, 'hq-run', outcome, max(0, self.clock() - started), None)
            finally:
                self._status(rid, status, ended_at=self.clock(), exit_code=exit_code)

    def _rate_limit(self, rid, req, profile, launcher):
        with self._lock:
            state = self._runs[rid]
            if state.get('limited'): return
            state['limited'] = True
            stopped = state['stop']
        now = self.clock()
        with self.store._lock, self.store._writer:
            self.store._writer.execute('INSERT INTO limit_snapshots (account_id,harness,provider,scope,window,used_percent,resets_at,source,recorded_at) VALUES (?,?,?,?,?,?,?,?,?)',
                (profile.account_id if profile else launcher.name, launcher.harness, launcher.name, '*', '5h', 100, now + 3600, 'run', now))
        self.pu_limit(launcher.name, req.model, None)
        if not stopped: self._status(rid, 'rate_limited')

    def _stopped(self, rid):
        with self._lock:
            return bool(self._runs[rid]['stop'])

    def _request_stop(self, rid, status):
        with self._lock:
            state = self._runs.get(rid)
            if not state or state['finished'] or state['stop']: return
            state['stop'] = status
            child = state['child']
        if child: child.kill_tree()

    def stop(self, rid):
        self._request_stop(rid, 'stopped')

    def output(self, rid):
        with self._lock:
            state = self._runs.get(rid)
            return list(state['output']) if state else None

    def warnings(self, rid):
        with self._lock:
            state = self._runs.get(rid)
            return state['warnings'] if state else None

    def shutdown(self):
        with self._lock:
            self._shutting_down = True
            ids = [rid for rid, state in self._runs.items() if not state['finished']]
            threads = [self._runs[rid]['thread'] for rid in ids]
        for rid in ids: self._request_stop(rid, 'interrupted')
        for thread in threads:
            if thread is not threading.current_thread() and thread.ident is not None: thread.join(timeout=10)

    def recover(self):
        return self.store.mark_interrupted()
