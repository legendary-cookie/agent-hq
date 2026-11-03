from collections.abc import Mapping
from pathlib import Path

from hq.config import Profile
from hq.run.request import RunRequest
from .base import BaseLauncher, Launch
from hq.run.stream import valid_session_id


class AgyLauncher(BaseLauncher):
    name = exe = 'agy'
    harness = 'antigravity'
    tested = True  # real run checked on this PC, 2026-11-03
    offered = frozenset({'edit', 'full_auto'})
    worktree_auto = True
    steer_mode = 'resume'  # live stream-json input unverified; resume uses the checked -p= path

    def build(self, req: RunRequest, profile: Profile | None, workdir: Path, run_id: str,
              env_base: Mapping[str, str], pass_names: list[str]) -> Launch:
        env = self.prepare(req, profile, workdir, env_base, pass_names)
        # Real agy 1.4.9: -p takes the prompt as its value ("-p took '--output-format' as its
        # prompt"), so attach it as one element and keep it last.
        return Launch(self._argv(req) + ['-p=' + req.prompt], env, workdir)

    def _argv(self, req):
        argv = ['agy', '--output-format', 'stream-json']
        if req.model: argv += ['--model', req.model]
        if req.level == 'full_auto': argv += ['--dangerously-skip-permissions']
        return argv

    def build_resume(self, req, profile, workdir, run_id, session_id, text, env_base, pass_names):
        if not valid_session_id(session_id): raise ValueError('Invalid session id')
        env = self.prepare(req, profile, workdir, env_base, pass_names)
        return Launch(self._argv(req) + ['--conversation=' + session_id, '-p=' + text], env, workdir)
