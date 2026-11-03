from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from hq.config import Profile, is_forbidden
from hq.run.env import child_env
from hq.run.request import RunRequest


@dataclass
class Launch:
    argv: list[str]
    env: dict[str, str]
    cwd: Path
    stdin_text: str | None = None
    wsl_pidfile: str | None = None


class Launcher(Protocol):
    name: str
    harness: str
    exe: str
    profile_var: str | None
    links_by_stream: bool

    def levels(self) -> set[str]: ...
    def build(self, req: RunRequest, profile: Profile | None, workdir: Path, run_id: str,
              env_base: Mapping[str, str], pass_names: list[str]) -> Launch: ...
    def parse(self, line: str): ...


class BaseLauncher:
    profile_var: str | None = None
    links_by_stream = True
    offered: frozenset[str] = frozenset({'edit'})
    worktree_auto = False

    def __init__(self, worktrees_root: Path = Path('data/worktrees')):
        self.worktrees_root = Path(worktrees_root)

    def levels(self) -> set[str]:
        return set(self.offered)

    def parse(self, line: str):
        from hq.run.stream import parse_json
        return parse_json(line, self.name)

    def prepare(self, req: RunRequest, profile: Profile | None, workdir: Path,
                env_base: Mapping[str, str], pass_names: list[str]) -> dict[str, str]:
        if req.level not in self.offered:
            raise ValueError('Level is not offered')
        if is_forbidden(workdir) or is_forbidden(self.worktrees_root):
            raise ValueError('Forbidden workspace path')
        root = self.worktrees_root.resolve()
        path = workdir.resolve()
        if req.level == 'full_auto' and self.worktree_auto and (path == root or not path.is_relative_to(root)):
            raise ValueError('Full auto requires a worktree')
        extra = {}
        if profile:
            if profile.tool != self.name or is_forbidden(Path(profile.config_dir).expanduser()):
                raise ValueError('Invalid profile')
            if self.profile_var:
                extra[self.profile_var] = profile.config_dir
        return child_env(env_base, extra, pass_names)
