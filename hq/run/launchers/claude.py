from collections.abc import Mapping
from pathlib import Path

from hq.config import Profile
from hq.run.request import RunRequest
from .base import BaseLauncher, Launch


class ClaudeLauncher(BaseLauncher):
    name = exe = harness = 'claude'
    profile_var = 'CLAUDE_CONFIG_DIR'
    offered = frozenset({'read_only', 'edit', 'full_auto'})
    worktree_auto = True

    def build(self, req: RunRequest, profile: Profile | None, workdir: Path, run_id: str,
              env_base: Mapping[str, str], pass_names: list[str]) -> Launch:
        env = self.prepare(req, profile, workdir, env_base, pass_names)
        argv = ['claude', '-p', '--output-format', 'stream-json', '--verbose']
        if req.level == 'full_auto':
            argv += ['--dangerously-skip-permissions']
        else:
            argv += ['--permission-mode', 'plan' if req.level == 'read_only' else 'acceptEdits']
        if req.model: argv += ['--model', req.model]
        return Launch(argv, env, workdir, req.prompt)
