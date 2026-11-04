import os
import copy
import secrets
import threading
import time
from collections import deque
from dataclasses import replace
from pathlib import Path

from hq.run import env, followup, proc, workspace, winexe
from hq.run.stream import parse_line, valid_session_id
from hq.run.approvals import AnsweredError
from hq.run.link import find_session
from hq.run.request import RunRequest


class Busy(Exception):
    pass


_RESUMABLE = ('done', 'interrupted', 'stopped', 'timed_out', 'failed')  # a run without a session is refused separately


class NotIdle(Exception):
    pass


class Runner:
    def __init__(self, store, run_cfg, launchers, worktrees_root, pu_log, on_event,
                 clock=time.time, pu_limit=None, minute_s=60.0, projects=(),
                 link_interval_s=5.0, link_window_s=120.0, link_grace_s=20.0,
                 approvals=None, idle_close_s=1800.0, profiles=()):
        self.store, self.run_cfg = store, run_cfg
        self.approvals, self.idle_close_s = approvals, idle_close_s
        self.worktrees_root = Path(worktrees_root)
        workspace.configure(self.worktrees_root.parent)
        self.launchers = {name: copy.copy(launcher) for name, launcher in launchers.items()}
        for launcher in self.launchers.values():
            if hasattr(launcher, 'worktrees_root'):
                launcher.worktrees_root = self.worktrees_root
        self.pu_log, self.on_event, self.clock = pu_log, on_event, clock
        self.pu_limit = pu_limit or (lambda *args: None)
        self.minute_s = minute_s
        self.link_interval_s, self.link_window_s, self.link_grace_s = link_interval_s, link_window_s, link_grace_s
        self.projects = {project.name: Path(project.path) for project in projects}
        self.profiles = list(profiles)
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
                         output=deque(maxlen=500), warnings=0, thread=None,
                         link_stop=threading.Event(), link_started=time.monotonic(),
                         write_lock=threading.Lock(), idle_timer=None, raw_session_id=None,
                         req=copy.deepcopy(req), profile=copy.deepcopy(profile), launcher=launcher,
                         pass_names=list(self.run_cfg.pass_env.get(req.tool, [])),
                         env_base=dict(os.environ), turns=1)
            self._runs[rid] = state
            thread = threading.Thread(target=self._run, args=(rid, state['req'], state['profile'], launcher, project), daemon=True)
            state['thread'] = thread
        self.on_event({'type': 'run', 'id': rid, 'status': 'starting'})
        result = self.store.get_run(rid)
        thread.start()
        return result

    def _run(self, rid, req, profile, launcher, project, resume_text=None):
        child = None
        timer = None
        exit_code = None
        stderr_thread = None
        started = self.clock()
        try:
            with self._lock:
                stopped = self._runs[rid]['stop']
            if stopped: return
            state = self._runs[rid]
            if resume_text is None:
                workdir, branch, base_commit, git_dir = project, None, None, None
                if req.level != 'read_only':
                    workdir, branch, base_commit, git_dir = workspace.create(project, rid, self.worktrees_root)
                state['workdir'] = workdir
                self.store.update_run(rid, workdir=str(workdir), branch=branch, base_commit=base_commit, git_dir=git_dir)
                self._start_linker(rid, launcher, workdir, self.store.get_run(rid)['started_at'])
            workdir = state['workdir']
            make_driver = getattr(launcher, 'make_driver', None)
            driver = make_driver(replace(req, project=str(workdir)),
                                 state['raw_session_id'] if resume_text is not None else None) if make_driver else None
            state['driver'] = driver
            if driver:
                launch = launcher.build(req, profile, workdir, rid, state['env_base'], state['pass_names'])
                launch = replace(launch, stdin_text=''.join(driver.start(resume_text if resume_text is not None else req.prompt)))
            elif resume_text is None:
                launch = launcher.build(req, profile, workdir, rid, state['env_base'], state['pass_names'])
                if getattr(launcher, 'steer_mode', None) == 'live':
                    launch = replace(launch, stdin_text=launcher.first_input(req.prompt))
            else:
                launch = followup.resume_launch(launcher, req, profile, workdir, rid,
                                                state['raw_session_id'], resume_text,
                                                state['env_base'], state['pass_names'])
            executable = self.executables.get(req.tool)
            if executable is None: raise FileNotFoundError('Executable unavailable')
            argv = winexe.final_argv(launch, executable)
            if launch.argv[0] == 'wsl': argv[0] = str(executable)
            launch = replace(launch, argv=argv)
            with self._lock:
                stopped = self._runs[rid]['stop']
            if stopped: return
            child = proc.spawn(launch, lambda: self._stopped(rid))
            def read_stderr():
                try:
                    while True:
                        line = child.stderr.readline(65536)
                        if not line: break
                        if len(line) == 65536 and not line.endswith('\n'):
                            while True:
                                remainder = child.stderr.readline(65536)
                                if not remainder or remainder.endswith('\n'): break
                            line = line[:65536 - len(' …[truncated]')] + ' …[truncated]'
                        with self._lock:
                            state = self._runs.get(rid)
                            if state is not None:
                                state['output'].append('[stderr] ' + line.rstrip('\r\n'))
                finally:
                    child.stderr.close()
            stderr_thread = threading.Thread(target=read_stderr, daemon=True)
            stderr_thread.start()
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
                timer = self._start_limit(rid, req)
                if launch.stdin_text is not None:
                    try:
                        with self._runs[rid]['write_lock']:
                            child.stdin.write(launch.stdin_text)
                            child.stdin.flush()
                            if getattr(launcher, 'steer_mode', None) != 'live':
                                child.stdin.close()
                    except (BrokenPipeError, OSError): pass
            while True:
                line = child.stdout.readline(65536)
                if not line: break
                if len(line) == 65536 and not line.endswith('\n'):
                    while True:
                        remainder = child.stdout.readline(65536)
                        if not remainder or remainder.endswith('\n'): break
                    line = line[:65536 - len(' …[truncated]')] + ' …[truncated]'
                line = line.rstrip('\r\n')
                with self._lock: self._runs[rid]['output'].append(line)
                if driver:
                    events, replies = driver.on_line(line)
                    for reply in replies: self._write(rid, reply)
                    events = [replace(event, session_id=f'{launcher.harness}:{event.session_id}')
                              if event.session_id else event for event in events]
                else:
                    events = [parse_line(req.tool, line, launcher)]
                for event in events:
                    if event is None or event.kind == 'warning':
                        with self._lock: self._runs[rid]['warnings'] += 1
                        continue
                    if event.session_id:
                        with self._lock:
                            raw_id = event.session_id[len(launcher.harness) + 1:]
                            previous = self._runs[rid]['raw_session_id']
                            if previous and previous != raw_id:
                                self._runs[rid]['warnings'] += 1
                            elif not previous:
                                self._runs[rid]['raw_session_id'] = raw_id
                            if not self._runs[rid].get('session_id'):
                                self._runs[rid]['session_id'] = event.session_id
                                self._runs[rid]['link_stop'].set()
                                self.store.update_run(rid, session_id=event.session_id)
                    if event.kind in ('permission', 'control_unknown') and not launcher.can_ask:
                        with self._lock: self._runs[rid]['warnings'] += 1
                    elif event.kind == 'permission' and self.approvals:
                        self._permission(rid, launcher, event.request)
                    elif event.kind == 'control_unknown':
                        with self._lock: self._runs[rid]['warnings'] += 1
                        self._reply(rid, launcher, event.request, 'deny', 'Unknown control request')
                    elif event.kind == 'turn_end' and getattr(launcher, 'steer_mode', None) == 'live':
                        self._idle(rid)
                    if event.rate_limited:
                        self._rate_limit(rid, req, profile, launcher)
            exit_code = child.wait()
            if stderr_thread: stderr_thread.join(timeout=5)
        except Exception as exc:
            # Make a failure before or during launch visible in the run's output.
            with self._lock: self._runs[rid]['output'].append(f'[agent-hq] run failed: {type(exc).__name__}: {exc}')
            if child and child.alive():
                child.kill_tree()
                exit_code = child.wait()
        finally:
            if timer: timer.cancel()
            if self.approvals: self.approvals.close_run(rid, 'expired')
            with self._lock:
                idle_timer = self._runs[rid].get('idle_timer')
                if idle_timer: idle_timer.cancel()
            if child:
                try: child.close(close_stderr=not (stderr_thread and stderr_thread.is_alive()))
                except (BrokenPipeError, OSError): pass
            with self._lock:
                state = self._runs[rid]
                status = state['stop'] or ('rate_limited' if state.get('limited') else ('done' if exit_code == 0 else 'failed'))
            outcome = {'done': 'ok', 'failed': 'error', 'timed_out': 'error', 'interrupted': 'error',
                       'rate_limited': 'rate_limited', 'stopped': 'cancelled'}[status]
            try:
                self.pu_log(launcher.name, req.model, 'hq-run', outcome, max(0, self.clock() - started), None)
            finally:
                with self._lock:
                    self._runs[rid]['finished'] = True
                    self._status(rid, status, ended_at=self.clock(), exit_code=exit_code)
                    self._runs[rid]['finished_at'] = time.monotonic_ns()
                    finished = sorted((key for key, value in self._runs.items() if value['finished']), key=lambda key: self._runs[key].get('finished_at', 0))
                    for key in finished[:-200]:
                        self._runs[key]['link_stop'].set()
                        del self._runs[key]

    def _write(self, rid, text):
        state = self._runs[rid]
        with state['write_lock']:
            child = state['child']
            try:
                child.stdin.write(text)
                child.stdin.flush()
            except (BrokenPipeError, OSError, ValueError, AttributeError):
                pass

    def _reply(self, rid, launcher, request, decision, note):
        driver = self._runs[rid].get('driver')
        if driver:
            for line in driver.answer(request.get('request_id'), decision, note):
                self._write(rid, line)
        else:
            self._write(rid, launcher.control_reply(request.get('request_id'), decision, note,
                                                     request.get('input') if isinstance(request.get('input'), dict) else {}))
        if self.approvals and not self.approvals.has_waiting(rid):
            self._status(rid, 'running')

    def _permission(self, rid, launcher, request):
        def reply(decision, note):
            self._reply(rid, launcher, request, decision, note)
        try:
            if (not isinstance(request.get('request_id'), str) or not request['request_id']
                    or not isinstance(request.get('tool_name'), str) or not request['tool_name']
                    or not isinstance(request.get('input'), dict)):
                raise ValueError('invalid permission request')
            approval = self.approvals.open(rid, request.get('request_id'), request.get('tool_name'),
                                           request['input'], reply)
        except (AnsweredError, KeyError, ValueError):
            with self._lock: self._runs[rid]['warnings'] += 1
            if isinstance(request.get('request_id'), str) and request['request_id']:
                reply('deny', 'Invalid or duplicate permission request')
            return
        with self.approvals._lock:
            if approval and self.approvals.has_waiting(rid):
                self._status(rid, 'needs_you')

    def _start_limit(self, rid, req):
        """Start (or restart) the per-turn time limit; it is paused while the run is idle."""
        timer = threading.Timer(req.time_limit_min * self.minute_s, self._request_stop, args=(rid, 'timed_out'))
        timer.daemon = True
        with self._lock:
            old = self._runs[rid].get('limit_timer')
            if old: old.cancel()
            self._runs[rid]['limit_timer'] = timer
        timer.start()
        return timer

    def _idle(self, rid):
        if self.approvals and self.approvals.has_waiting(rid): return
        with self._lock:
            state = self._runs[rid]
            if state['stop']: return
            if state.get('limit_timer'): state['limit_timer'].cancel()
            if state['idle_timer']: state['idle_timer'].cancel()
            timer = threading.Timer(self.idle_close_s, self._close_idle, args=(rid,))
            timer.daemon = True
            state['idle_timer'] = timer
            self._status(rid, 'idle')
            timer.start()

    def _close_idle(self, rid):
        with self._lock:
            state = self._runs.get(rid)
            if not state or state['status'] != 'idle': return
            state['status'] = 'closing'
            with state['write_lock']:
                try: state['child'].stdin.close()
                except (OSError, ValueError): pass

    def send(self, rid, text):
        if not isinstance(text, str) or not text or len(text) > 100000:
            raise ValueError('Invalid message')
        with self._lock:
            state = self._runs.get(rid)
            if state is None:
                state = self._restore(rid)
            launcher = state['launcher']
            mode = getattr(launcher, 'steer_mode', None)
            if mode == 'live' and state['status'] == 'idle' and not state['finished']:
                if state['idle_timer']: state['idle_timer'].cancel()
                state['status'] = 'running'
                state['turns'] += 1
                self.store.update_run(rid, status='running', turns=state['turns'])
                state['output'].append(f'[agent-hq] turn {state["turns"]}')
                self._start_limit(rid, state['req'])
                driver = state.get('driver')
                lines = driver.follow_up(text) if driver else [launcher.user_message(text)]
                for line in lines: self._write(rid, line)
                self.on_event({'type': 'run', 'id': rid, 'status': 'running'})
                return
            if mode not in ('live', 'resume') or not state['finished'] or state['status'] not in _RESUMABLE:
                raise NotIdle('The agent is still working')
            if not state['raw_session_id']:
                session_id = state.get('session_id') or self.store.get_run(rid).get('session_id')
                prefix = f'{launcher.harness}:'
                state['raw_session_id'] = session_id.removeprefix(prefix) if session_id else None
            if not state['raw_session_id']:
                raise NotIdle('No session is available to resume')
            if not valid_session_id(state['raw_session_id']):
                raise ValueError('Invalid session id')
            if self._shutting_down or sum(not r['finished'] for r in self._runs.values()) >= self.run_cfg.max_concurrent:
                raise Busy('Maximum concurrent runs reached')
            state['finished'] = False
            state['stop'] = None
            state['child'] = None
            state['status'] = 'starting'
            state['turns'] += 1
            state['output'].append(f'[agent-hq] turn {state["turns"]}')
            self.store.update_run(rid, status='starting', turns=state['turns'], ended_at=None, exit_code=None)
            thread = threading.Thread(target=self._run, args=(rid, state['req'], state['profile'], launcher,
                                                              self.projects[state['req'].project], text), daemon=True)
            state['thread'] = thread
            thread.start()
        self.on_event({'type': 'run', 'id': rid, 'status': 'starting'})

    def _restore(self, rid):
        row = self.store.get_run(rid)
        if row is None:
            raise KeyError(rid)
        launcher = self.launchers.get(row['tool'])
        if not launcher or getattr(launcher, 'steer_mode', None) not in ('live', 'resume') or row['status'] not in _RESUMABLE:
            raise NotIdle('The run cannot be resumed')
        project = self.projects.get(row['project'])
        if project is None or project != project.resolve() or not project.is_dir():
            raise ValueError('Project is no longer allowlisted or available')
        workdir = Path(row['workdir'])
        if not workdir.is_dir():
            raise ValueError('Run workdir no longer exists')
        profile = next((p for p in self.profiles if p.id == row['account'] and p.tool == row['tool']), None)
        if row['account'] and profile is None:
            raise ValueError('Run profile is no longer available')
        req = RunRequest(row['tool'], row['model'], row['project'], row['prompt_preview'] or '',
                         row['level'], row['guard_mode'], row['account'], int(row['time_limit_s'] // 60) or 1)
        session_id = row['session_id']
        raw_id = session_id.removeprefix(f'{launcher.harness}:') if session_id else None
        if raw_id is not None and not valid_session_id(raw_id):
            raise ValueError('Invalid session id')
        state = dict(status=row['status'], stop=None, child=None, finished=True,
                     output=deque(maxlen=500), warnings=0, thread=None,
                     link_stop=threading.Event(), link_started=time.monotonic(),
                     write_lock=threading.Lock(), idle_timer=None, raw_session_id=raw_id,
                     session_id=session_id, req=req, profile=copy.deepcopy(profile), launcher=launcher,
                     pass_names=list(self.run_cfg.pass_env.get(req.tool, [])), env_base=dict(os.environ),
                     workdir=workdir, turns=row['turns'] or 1)
        self._runs[rid] = state
        return state

    def _start_linker(self, rid, launcher, workdir, started_at):
        with self._lock:
            state = self._runs[rid]
            state.setdefault('link_stop', threading.Event())
            state.setdefault('link_started', time.monotonic())
            thread = threading.Thread(target=self._link_session,
                                      args=(rid, launcher, workdir, started_at), daemon=True)
            state['link_thread'] = thread
            thread.start()

    def _link_session(self, rid, launcher, workdir, started_at):
        with self._lock:
            state = self._runs[rid]
            stop = state.setdefault('link_stop', threading.Event())
            start = state.setdefault('link_started', time.monotonic())
        grace = self.link_grace_s if getattr(launcher, 'links_by_stream', True) else 0
        remaining = start + grace - time.monotonic()
        if remaining > 0 and stop.wait(remaining): return
        while time.monotonic() <= start + self.link_window_s:
            with self._lock:
                if stop.is_set() or state['stop'] or state.get('session_id'): return
            session_id = find_session(self.store, launcher.harness, workdir, started_at)
            if session_id:
                with self._lock:
                    if not stop.is_set() and not state['stop'] and not state.get('session_id'):
                        state['session_id'] = session_id
                        self.store.update_run(rid, session_id=session_id)
                return
            remaining = start + self.link_window_s - time.monotonic()
            if remaining <= 0 or stop.wait(min(self.link_interval_s, remaining)): return

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
            state['link_stop'].set()
            child = state['child']
        if child: child.kill_tree()

    def stop(self, rid):
        if self.approvals: self.approvals.close_run(rid, 'denied')
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
            for state in self._runs.values():
                state.get('link_stop', threading.Event()).set()
            link_threads = [state['link_thread'] for state in self._runs.values() if state.get('link_thread')]
            ids = [rid for rid, state in self._runs.items() if not state['finished']]
            threads = [self._runs[rid]['thread'] for rid in ids]
        for rid in ids: self._request_stop(rid, 'interrupted')
        for thread in link_threads:
            if thread is not threading.current_thread(): thread.join(timeout=10)
        for thread in threads:
            if thread is not threading.current_thread() and thread.ident is not None: thread.join(timeout=10)

    def recover(self):
        return self.store.mark_interrupted()
