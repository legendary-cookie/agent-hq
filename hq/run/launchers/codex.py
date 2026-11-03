from collections.abc import Mapping
from pathlib import Path

from hq.config import Profile
from hq.run.request import RunRequest
import re

from hq.run.wsl import to_wsl_path
from .base import BaseLauncher, Launch


class CodexLauncher(BaseLauncher):
    name = exe = harness = 'codex'
    profile_var = 'CODEX_HOME'
    tested = True  # real read-only run checked on this PC, 2026-11-03
    offered = frozenset({'read_only', 'edit', 'full_auto'})

    def build(self, req: RunRequest, profile: Profile | None, workdir: Path, run_id: str,
              env_base: Mapping[str, str], pass_names: list[str]) -> Launch:
        env = self.prepare(req, profile, workdir, env_base, pass_names)
        if not re.fullmatch(r'run_[A-Za-z0-9_-]+', run_id):
            raise ValueError('Invalid run id')
        if profile: env['WSLENV'] = 'CODEX_HOME/u'
        pidfile = f'/tmp/hq-runs/{run_id}.pid'
        wrapper = Path(__file__).resolve().parent.parent / 'wsl_wrap.sh'
        argv = ['wsl', '-d', 'Debian', '--cd', to_wsl_path(workdir), '--', 'bash',
                to_wsl_path(wrapper), pidfile, 'codex', 'exec', '--json']
        if req.model: argv += ['-m', req.model]
        argv += ['-s', 'read-only' if req.level == 'read_only' else 'workspace-write']
        if req.level == 'full_auto': argv += ['-c', 'sandbox_workspace_write.network_access=true']
        return Launch(argv + ['-'], env, workdir, req.prompt, pidfile)
