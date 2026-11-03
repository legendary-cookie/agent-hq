from collections.abc import Mapping
import json
from pathlib import Path

from hq.config import Profile
from hq.run.request import RunRequest
from .base import BaseLauncher, Launch


class ClaudeLauncher(BaseLauncher):
    name = exe = 'claude'
    harness = 'claude_code'  # the reader's adapter name
    profile_var = 'CLAUDE_CONFIG_DIR'
    tested = True  # real read-only run checked on this PC, 2026-11-03
    offered = frozenset({'read_only', 'ask', 'edit', 'full_auto'})
    worktree_auto = True
    steer_mode = 'live'
    can_ask = True

    def control_reply(self, request_id, decision, note, tool_input) -> str:
        if decision == 'allow':
            response = {'behavior': 'allow', 'updatedInput': tool_input}
        elif decision == 'deny':
            response = {'behavior': 'deny', 'message': note or 'Denied by the user in Agent HQ.'}
        else:
            raise ValueError('Invalid decision')
        return json.dumps({'type': 'control_response', 'response': {
            'subtype': 'success', 'request_id': request_id, 'response': response}},
            separators=(',', ':')) + '\n'

    def build(self, req: RunRequest, profile: Profile | None, workdir: Path, run_id: str,
              env_base: Mapping[str, str], pass_names: list[str]) -> Launch:
        env = self.prepare(req, profile, workdir, env_base, pass_names)
        argv = ['claude', '-p', '--input-format', 'stream-json', '--output-format', 'stream-json', '--verbose']
        if req.level == 'full_auto':
            argv += ['--dangerously-skip-permissions']
        elif req.level == 'ask':
            argv += ['--permission-mode', 'default', '--permission-prompts', 'host', '--permission-prompt-tool', 'stdio']
        else:
            argv += ['--permission-mode', 'plan' if req.level == 'read_only' else 'acceptEdits']
        if req.model: argv += ['--model', req.model]
        return Launch(argv, env, workdir, self.first_input(req.prompt))
