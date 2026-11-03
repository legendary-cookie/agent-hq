from collections.abc import Mapping
from pathlib import Path

from hq.config import Profile
from hq.run.request import RunRequest
from .base import BaseLauncher, Launch


class AgyLauncher(BaseLauncher):
    name = exe = 'agy'
    harness = 'antigravity'
    offered = frozenset({'edit', 'full_auto'})
    worktree_auto = True

    def build(self, req: RunRequest, profile: Profile | None, workdir: Path, run_id: str,
              env_base: Mapping[str, str], pass_names: list[str]) -> Launch:
        env = self.prepare(req, profile, workdir, env_base, pass_names)
        argv = ['agy', '-p', '--output-format', 'stream-json', '--input-format', 'text']
        if req.model: argv += ['--model', req.model]
        if req.level == 'full_auto': argv += ['--dangerously-skip-permissions']
        return Launch(argv, env, workdir, req.prompt)
