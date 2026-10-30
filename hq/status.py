def session_status(last_event_at, last_kind, ended_at, now) -> str:
    age = now - last_event_at
    if ended_at is not None or age >= 86400:
        return 'done'
    if last_kind == 'error':
        return 'error'
    if last_kind == 'turn_end' and age < 600:
        return 'waiting'
    if age < 60 or (last_kind == 'tool_call' and age < 600):
        return 'working'
    return 'idle'
