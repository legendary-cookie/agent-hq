from dataclasses import dataclass, field
from typing import Literal

@dataclass(frozen=True)
class SessionRec:
    id: str
    harness: str
    provider: str
    model: str | None
    project: str | None
    title: str | None
    started_at: float
    last_event_at: float
    ended_at: float | None
    parent_id: str | None
    source_ref: str

@dataclass(frozen=True)
class EventRec:
    session_id: str
    ext_id: str
    ts: float
    kind: Literal['user', 'assistant', 'tool_call', 'tool_result', 'error', 'turn_end']
    tool: str | None
    preview: str | None
    tokens_in: int | None
    tokens_out: int | None
    tokens_cache_read: int | None
    tokens_cache_write: int | None
    tokens_estimated: bool
    model: str | None

@dataclass(frozen=True)
class LimitRec:
    harness: str
    provider: str
    scope: str
    window: Literal['5h', 'week', 'day', 'month', 'block']
    used_percent: float
    resets_at: float | None
    source: Literal['measured', 'blocked', 'manual']
    recorded_at: float

@dataclass(frozen=True)
class DelegationRec:
    ext_id: str
    ts: float
    provider: str
    model: str
    task_kind: str
    outcome: str
    tokens: int | None
    duration_s: float | None
    note: str | None

@dataclass(frozen=True)
class Batch:
    sessions: list
    events: list
    limits: list
    delegations: list
    warnings: int = 0
    cursors: list = field(default_factory=list)
