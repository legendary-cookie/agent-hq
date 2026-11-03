from collections.abc import Mapping
from pathlib import Path

from hq.config import Profile
from hq.run.request import RunRequest
from .base import BaseLauncher, Launch


class DshLauncher(BaseLauncher):
    name = exe = harness = 'dsh'
    links_by_stream = False

    def build(self, req: RunRequest, profile: Profile | None, workdir: Path, run_id: str,
              env_base: Mapping[str, str], pass_names: list[str]) -> Launch:
        env = self.prepare(req, profile, workdir, env_base, pass_names)
        return Launch(['dsh', 'headless', '--json', '-'], env, workdir, req.prompt)
