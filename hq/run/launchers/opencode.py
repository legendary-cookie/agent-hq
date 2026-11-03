from collections.abc import Mapping
from pathlib import Path

from hq.config import Profile
from hq.run.request import RunRequest
from .base import BaseLauncher, Launch


class OpencodeLauncher(BaseLauncher):
    name = exe = harness = 'opencode'

    def build(self, req: RunRequest, profile: Profile | None, workdir: Path, run_id: str,
              env_base: Mapping[str, str], pass_names: list[str]) -> Launch:
        env = self.prepare(req, profile, workdir, env_base, pass_names)
        argv = ['opencode', 'run', '--format', 'json']
        if req.model: argv += ['-m', req.model]
        return Launch(argv + ['--', req.prompt], env, workdir)
