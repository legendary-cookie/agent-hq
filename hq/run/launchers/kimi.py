from collections.abc import Mapping
from pathlib import Path

from hq.config import Profile
from hq.run.request import RunRequest
from .base import BaseLauncher, Launch


class KimiLauncher(BaseLauncher):
    name = exe = harness = 'kimi'
    offered = frozenset({'edit', 'full_auto'})
    worktree_auto = True

    def build(self, req: RunRequest, profile: Profile | None, workdir: Path, run_id: str,
              env_base: Mapping[str, str], pass_names: list[str]) -> Launch:
        env = self.prepare(req, profile, workdir, env_base, pass_names)
        argv = ['kimi', '--output-format', 'stream-json']
        if req.model: argv += ['-m', req.model]
        if req.level == 'full_auto': argv += ['-y']
        return Launch(argv + ['--prompt=' + req.prompt], env, workdir)
