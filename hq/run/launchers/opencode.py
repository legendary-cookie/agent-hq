from collections.abc import Mapping
import json
from pathlib import Path

from hq.config import Profile
from hq.run.acp import AcpDriver
from hq.run.request import RunRequest
from .base import BaseLauncher, Launch


class OpencodeLauncher(BaseLauncher):
    name = exe = harness = 'opencode'
    tested = True
    steer_mode = 'live'
    can_ask = True
    offered = frozenset({'ask', 'edit'})

    def make_driver(self, req, resume_session_id=None):
        return AcpDriver(req.project, resume_session_id)

    def build(self, req: RunRequest, profile: Profile | None, workdir: Path, run_id: str,
              env_base: Mapping[str, str], pass_names: list[str]) -> Launch:
        env = self.prepare(req, profile, workdir, env_base, pass_names)
        if req.level == 'ask':
            # opencode's default rule is "*": "allow" (web fetch, outside folders...), so Ask me asks
            # for everything except local reads, and .env reads keep asking (opencode 1.18 shapes).
            permission = {'*': 'ask', 'read': {'*': 'allow', '*.env': 'ask', '*.env.*': 'ask', '*.env.example': 'allow'},
                          'glob': 'allow', 'grep': 'allow', 'list': 'allow', 'edit': 'ask', 'bash': 'ask'}
        else:
            permission = {'edit': 'allow', 'bash': 'allow'}
        config = {'permission': permission}
        if req.model:  # opencode rejects "model": null; leave it out to use opencode's own default
            config['model'] = req.model
        env['OPENCODE_CONFIG_CONTENT'] = json.dumps(config, separators=(',', ':'))
        return Launch(['opencode', 'acp', '--cwd', str(workdir)], env, workdir)

    def build_resume(self, req, profile, workdir, run_id, session_id, text, env_base, pass_names):
        driver = AcpDriver(workdir, session_id)
        launch = self.build(req, profile, workdir, run_id, env_base, pass_names)
        launch.stdin_text = ''.join(driver.start(text))
        return launch
