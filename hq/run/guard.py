from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass

from hq.config import Profile
from hq.run.env import resolve_exe
from hq.run.pool import pick, profile_states
from hq.run.request import RunRequest
from hq.store import Store


_PROVIDER_TO_TOOL = {'codex': 'codex', 'antigravity': 'agy',
                     'hermes': 'hermes', 'opencode': 'opencode'}
_TOOL_TO_PROVIDER = {tool: provider for provider, tool in _PROVIDER_TO_TOOL.items()}


@dataclass
class Verdict:
    allowed: bool
    reasons: list[str]
    warnings: list[str]
    suggestion: dict | None
    profile: str | None
    earliest_reset: float | None


def _installed(tool: str) -> bool:
    return resolve_exe('wsl' if tool == 'codex' else tool, []) is not None


def model_blocked(store: Store, provider: str, model: str | None, now: float) -> float | None:
    with closing(store.conn()) as conn:
        return conn.execute(
            "SELECT max(resets_at) FROM limit_snapshots WHERE harness=? AND provider=? "
            "AND (scope=? OR scope='*') AND window='block' AND source='blocked' "
            "AND resets_at>? AND recorded_at<=?",
            (provider, provider, model, now, now),
        ).fetchone()[0]


def check(req: RunRequest, store: Store, profiles: list[Profile],
          recommend: Callable[[str], list | None], now: float) -> Verdict:
    rows = recommend('hq-run')
    warnings = []
    reasons = []
    if rows is None:
        warnings.append('Recommendations are unavailable')
        rows = []
    suggestion = None
    for row in rows:
        tool = _PROVIDER_TO_TOOL.get(row.get('provider'))
        if not tool or not _installed(tool):
            continue
        if req.guard_mode == 'auto' and model_blocked(store, row['provider'], row.get('model'), now) is not None:
            continue
        candidates = [profile for profile in profiles if profile.tool == tool]
        if req.guard_mode == 'auto' and candidates and pick(profile_states(store, candidates, now), now) is None:
            continue
        suggestion = dict(row, tool=tool)
        break

    tool = req.tool
    if req.guard_mode == 'auto':
        if suggestion is None:
            reasons.append('No installed recommended tool is available')
        else:
            tool = suggestion['tool']
    matching = [profile for profile in profiles if profile.tool == tool]
    if req.guard_mode != 'auto' and req.profile is not None:
        matching = [profile for profile in matching if profile.id == req.profile]
        if not matching:
            reasons.append('Requested profile is unavailable')
    states = profile_states(store, matching, now)
    selected = pick(states, now)
    earliest = None
    if states and selected is None:
        earliest = min(state.blocked_until for state in states if state.blocked_until is not None)
        reasons.append('All matching profiles are blocked')
    if any(state.max_used_percent is not None and state.max_used_percent >= 80 for state in states):
        warnings.append('A quota window is at least 80% used')
    provider = _TOOL_TO_PROVIDER.get(req.tool)
    if (req.guard_mode != 'auto' and provider is not None
            and model_blocked(store, provider, req.model, now) is not None):
        reasons.append('Requested model is blocked')
    if req.guard_mode == 'warn':
        warnings.extend(reasons)
        reasons.clear()
    if suggestion is not None and req.guard_mode == 'auto':
        suggestion['profile'] = selected.id if selected else None
    return Verdict(not reasons, reasons, warnings, suggestion,
                   selected.id if selected else None, earliest)
