"""provider-usage adapter (plan Task 9).

Reads the delegation tracker's usage.db read-only: `calls` become
delegations, active rows in `limits` become `blocked` limit snapshots, and
Hermes calls become synthetic sessions (Hermes keeps no logs of its own
outside its secrets folder). Timestamps in usage.db are ISO-8601 text.
"""
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .base import ro_connect
from ..config import is_forbidden
from ..records import Batch, DelegationRec, EventRec, LimitRec, SessionRec

_CALLS_SQL = 'SELECT id, ts, provider, model, task_kind, outcome, tokens, duration_s, note FROM calls'
_LIMITS_SQL = 'SELECT provider, model, blocked_until, recorded_ts FROM limits'


def _ts(text: str) -> float:
    dt = datetime.fromisoformat(text.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _db_path(root: Path) -> Path:
    return root / 'usage.db' if root.is_dir() else root


class ProviderUsageAdapter:
    name = 'provider_usage'

    def poll(self, store, cfg, now: float | None = None) -> Batch:
        now = time.time() if now is None else now
        sessions, events, limits, delegations, warnings = [], [], [], [], 0
        for root in cfg.paths:
            path = _db_path(Path(root))
            if is_forbidden(path) or not path.is_file():
                continue
            with closing(ro_connect(path)) as conn:
                for call_id, ts_text, provider, model, kind, outcome, tokens, duration, note in conn.execute(_CALLS_SQL):
                    try:
                        ts = _ts(ts_text)
                    except (TypeError, ValueError):
                        warnings += 1
                        continue
                    delegations.append(DelegationRec(str(call_id), ts, provider, model, kind, outcome, tokens, duration, note))
                    if provider == 'hermes':
                        sid = f'hermes:{call_id}'
                        sessions.append(SessionRec(sid, 'hermes', 'ninerouter', model, None, None,
                                                   ts - (duration or 0.0), ts, ts, None, str(path)))
                        events.append(EventRec(sid, f'{call_id}:assistant', ts, 'assistant', None, None,
                                               tokens, None, None, None, True, model))
                        events.append(EventRec(sid, f'{call_id}:turn_end', ts, 'turn_end', None, None,
                                               None, None, None, None, False, None))
                for provider, model, until_text, recorded_text in conn.execute(_LIMITS_SQL):
                    try:
                        until, recorded = _ts(until_text), _ts(recorded_text)
                    except (TypeError, ValueError):
                        warnings += 1
                        continue
                    if until > now:
                        limits.append(LimitRec(provider, provider, model or '*', 'block', 100.0, until, 'blocked', recorded))
        return Batch(sessions, events, limits, delegations, warnings)
