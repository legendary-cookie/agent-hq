from collections.abc import Mapping
from pathlib import Path

from hq.config import Profile
from hq.run.request import RunRequest
import re

from hq.run.wsl import to_wsl_path
from .base import BaseLauncher, Launch
from hq.run.stream import valid_session_id


class CodexLauncher(BaseLauncher):
    name = exe = harness = 'codex'
    profile_var = 'CODEX_HOME'
    tested = True  # real read-only run checked on this PC, 2026-11-03
    offered = frozenset({'read_only', 'edit', 'full_auto'})
    steer_mode = 'resume'

    def build(self, req: RunRequest, profile: Profile | None, workdir: Path, run_id: str,
              env_base: Mapping[str, str], pass_names: list[str]) -> Launch:
        if req.host and req.level == 'full_auto': raise ValueError('Full auto is unavailable remotely')
        if req.host: workdir = Path('{workdir}')
        env = self.prepare(req, profile, workdir, env_base, pass_names)
        if not re.fullmatch(r'run_[A-Za-z0-9_-]+', run_id):
            raise ValueError('Invalid run id')
        if req.host:
            argv = ['codex', 'exec', '--json']
            if req.model: argv += ['-m', req.model]
            argv += ['-s', 'read-only' if req.level == 'read_only' else 'workspace-write']
            if req.level == 'full_auto': argv += ['-c', 'sandbox_workspace_write.network_access=true']
            return Launch(argv + ['-'], env, workdir, req.prompt)
        if profile: env['WSLENV'] = 'CODEX_HOME/u'
        pidfile = f'/tmp/hq-runs/{run_id}.pid'
        wrapper = Path(__file__).resolve().parent.parent / 'wsl_wrap.sh'
        argv = ['wsl', '-d', 'Debian', '--cd', to_wsl_path(workdir), '--exec', 'bash',
                to_wsl_path(wrapper), pidfile, 'codex', 'exec', '--json']
        if req.model: argv += ['-m', req.model]
        argv += ['-s', 'read-only' if req.level == 'read_only' else 'workspace-write']
        if req.level == 'full_auto': argv += ['-c', 'sandbox_workspace_write.network_access=true']
        return Launch(argv + ['-'], env, workdir, req.prompt, pidfile)

    def build_resume(self, req, profile, workdir, run_id, session_id, text, env_base, pass_names):
        if not valid_session_id(session_id): raise ValueError('Invalid session id')
        launch = self.build(req, profile, workdir, run_id, env_base, pass_names)
        index = launch.argv.index('codex')
        # Real codex 0.164: `exec resume` has no -s flag; set the run's sandbox explicitly with
        # -c sandbox_mode, before the session id, so a resume never falls back to a default.
        mode = 'read-only' if req.level == 'read_only' else 'workspace-write'
        argv = launch.argv[:index] + ['codex', 'exec', 'resume', '--json', '-c', f'sandbox_mode="{mode}"']
        if req.level == 'full_auto': argv += ['-c', 'sandbox_workspace_write.network_access=true']
        return Launch(argv + [session_id, '-'], launch.env, workdir, text, launch.wsl_pidfile)
