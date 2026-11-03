from contextlib import closing
from dataclasses import dataclass

from hq.config import Profile
from hq.store import Store


@dataclass(frozen=True)
class ProfileState:
    profile: Profile
    max_used_percent: float | None
    blocked_until: float | None
    last_used: float | None


def profile_states(store: Store, profiles: list[Profile], now: float) -> list[ProfileState]:
    states = []
    with closing(store.conn()) as conn:
        for profile in profiles:
            rows = conn.execute(
                'SELECT window, used_percent, resets_at, source FROM limit_snapshots '
                'WHERE account_id=? AND recorded_at<=? ORDER BY recorded_at DESC, id DESC',
                (profile.account_id, now),
            ).fetchall()
            latest = {}
            for window, percent, reset, source in rows:
                latest.setdefault(window, (percent, reset, source))
            latest = {window: values for window, values in latest.items()
                      if values[1] is None or values[1] > now}
            percentages = [100.0 if source == 'blocked' else percent
                           for percent, reset, source in latest.values()
                           if percent is not None or source == 'blocked']
            resets = [reset for percent, reset, source in latest.values()
                      if percent is not None and percent >= 100 and reset is not None and reset > now]
            last_used = conn.execute(
                'SELECT max(started_at) FROM runs WHERE account=? AND started_at<=?',
                (profile.account_id, now),
            ).fetchone()[0]
            states.append(ProfileState(profile, max(percentages, default=None),
                                       max(resets, default=None), last_used))
    return states


def pick(states: list[ProfileState], now: float) -> Profile | None:
    eligible = [state for state in states if state.blocked_until is None or state.blocked_until <= now]
    if not eligible:
        return None
    # Unknown usage sorts after measured usage; never-used profiles win ties.
    best = min(eligible, key=lambda state: (
        state.max_used_percent is None,
        state.max_used_percent if state.max_used_percent is not None else 0,
        state.last_used is not None,
        state.last_used if state.last_used is not None else 0,
    ))
    return best.profile
