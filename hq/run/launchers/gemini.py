from collections.abc import Mapping
from pathlib import Path

from hq.config import Profile
from hq.run.request import RunRequest
from .base import BaseLauncher, Launch


class GeminiLauncher(BaseLauncher):
    name = exe = 'gemini'
    harness = 'gemini_cli'  # the reader's adapter name
    offered = frozenset({'read_only', 'full_auto'})
    links_by_stream = False

    def build(self, req: RunRequest, profile: Profile | None, workdir: Path, run_id: str,
              env_base: Mapping[str, str], pass_names: list[str]) -> Launch:
        env = self.prepare(req, profile, workdir, env_base, pass_names)
        argv = ['gemini']
        if req.model: argv += ['-m', req.model]
        if req.level == 'full_auto': argv += ['--sandbox', '--yolo']
        return Launch(argv + ['--prompt=' + req.prompt], env, workdir)
