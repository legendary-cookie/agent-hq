def session_status(last_event_at, last_kind, ended_at, now, imported=False) -> str:
    age = now - last_event_at
    if imported:
        return 'active' if age < 600 else 'idle' if age < 86400 else 'done'
    if ended_at is not None or age >= 86400:
        return 'done'
    if last_kind == 'error':
        return 'error'
    if last_kind == 'turn_end' and age < 600:
        return 'waiting'
    if age < 60 or (last_kind == 'tool_call' and age < 600):
        return 'working'
    return 'idle'


_LAST_KIND_SQL = (
    'SELECT session_id, kind FROM (SELECT session_id, kind, ROW_NUMBER() OVER '
    '(PARTITION BY session_id ORDER BY ts DESC, id DESC) AS rn FROM events) WHERE rn = 1')


def last_kinds(conn) -> dict:
    """Kind of each session's most recent event, in one indexed query."""
    return dict(conn.execute(_LAST_KIND_SQL).fetchall())
