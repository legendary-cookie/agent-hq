from collections.abc import Mapping
from pathlib import Path

from hq.config import Profile
from hq.run.request import RunRequest
from .base import BaseLauncher, Launch
from hq.run.stream import valid_session_id


class OpencodeLauncher(BaseLauncher):
    name = exe = harness = 'opencode'
    tested = True  # real run checked on this PC, 2026-11-03
    steer_mode = 'resume'

    def build(self, req: RunRequest, profile: Profile | None, workdir: Path, run_id: str,
              env_base: Mapping[str, str], pass_names: list[str]) -> Launch:
        env = self.prepare(req, profile, workdir, env_base, pass_names)
        argv = ['opencode', 'run', '--format', 'json']
        if req.model: argv += ['-m', req.model]
        return Launch(argv + ['--', req.prompt], env, workdir)

    def build_resume(self, req, profile, workdir, run_id, session_id, text, env_base, pass_names):
        if not valid_session_id(session_id): raise ValueError('Invalid session id')
        env = self.prepare(req, profile, workdir, env_base, pass_names)
        argv = ['opencode', 'run', '--format', 'json', '--session=' + session_id]
        if req.model: argv += ['-m', req.model]
        return Launch(argv + ['--', text], env, workdir)
