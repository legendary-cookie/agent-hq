from collections.abc import Mapping
from pathlib import Path

from hq.config import Profile
from hq.run.request import RunRequest
import re

from .base import BaseLauncher, Launch


class HermesLauncher(BaseLauncher):
    name = exe = harness = 'hermes'
    # Disabled: on this PC Hermes runs shell commands in its configured terminal folder
    # (C:\Users\Jane Doe\zl_os) even with --in, so a run would act outside the project.
    offered = frozenset()
    disabled_reason = 'Hermes runs shell commands in its configured folder, not the project (--in is ignored)'
    worktree_auto = True
    links_by_stream = False

    def build(self, req: RunRequest, profile: Profile | None, workdir: Path, run_id: str,
              env_base: Mapping[str, str], pass_names: list[str]) -> Launch:
        env = self.prepare(req, profile, workdir, env_base, pass_names)
        if not re.fullmatch(r'run_[A-Za-z0-9_-]+', run_id):
            raise ValueError('Invalid run id')
        usage = self.worktrees_root.parent / 'runs' / f'{run_id}.usage.json'
        argv = ['hermes', '--oneshot=' + req.prompt, '--in', str(workdir)]
        if req.model: argv += ['-m', req.model]
        argv += ['--usage-file', str(usage)]
        if req.level == 'full_auto': argv += ['--yolo']
        return Launch(argv, env, workdir)
