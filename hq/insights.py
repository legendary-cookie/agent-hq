"""Measures of how the whole agent stack is doing, beyond cost: speed, reliability and how work is spread.

Every figure comes from recorded events. Where an event can't say something for certain, the measure
is named for what it counts, such as "limit messages seen" rather than "limit hits".
"""
from datetime import datetime

from .fleet import AGENT_CHANNELS, COMM_SUFFIX, SYSTEM_CHANNELS

# Claude Code writes these into the reply when the plan or the API refuses the work.
LIMIT_PATTERNS = ('%usage limit%', '%hit your limit%', '%rate limit%', '%rate_limit%')
API_ERROR_PATTERNS = ('API Error%', '%overloaded_error%', '%Request timed out%')
REPLY_WITHIN_S = 6 * 3600  # A reply later than this is treated as a new conversation, not an answer.
TOP_TOOLS = 12


def _quantile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def reply_times(rows):
    """Pair each reply with the oldest unanswered message on the same agent and channel.

    rows: (host, channel, kind, ts), sorted by ts. Group chats often get no reply, so an unanswered
    message is only dropped once it is too old to count, never counted as a failure.
    """
    pending, found = {}, []
    for host, channel, kind, ts in rows:
        key = (host, channel)
        if kind == 'user':
            pending.setdefault(key, ts)
        elif kind == 'assistant' and key in pending:
            waited = ts - pending.pop(key)
            if 0 <= waited <= REPLY_WITHIN_S:
                found.append((host, channel, waited, ts))
    return found


def _summary(waits):
    return {'replies': len(waits), 'median_s': _quantile(waits, .5), 'p90_s': _quantile(waits, .9)}


def _like(column, patterns):
    return '(' + ' or '.join(f'{column} like ?' for _ in patterns) + ')', tuple(patterns)


def insights(conn, win, now):
    start, end = win.start, win.end
    hidden = (*SYSTEM_CHANNELS, *AGENT_CHANNELS)
    marks = ','.join('?' for _ in hidden)

    # Speed of replies to people, from message times only.
    rows = conn.execute(f'''select s.host, e.tool, e.kind, e.ts from events e join sessions s on s.id=e.session_id
                            where s.id like '%{COMM_SUFFIX}' and s.host is not null and e.kind in ('user','assistant')
                            and coalesce(e.tool,'') not in ({marks}) and e.ts>=? and e.ts<? order by e.ts, e.id''',
                        (*hidden, start - REPLY_WITHIN_S, end)).fetchall()
    # Messages from before the period are read so that a reply early in it can be paired, but only
    # replies inside the period count.
    timed = [(host, channel, waited, ts) for host, channel, waited, ts in reply_times(rows) if ts >= start]
    found = [(host, channel, waited) for host, channel, waited, _ in timed]
    by_channel, by_host = {}, {}
    for host, channel, waited in found:
        by_channel.setdefault(channel or 'other', []).append(waited)
        by_host.setdefault(host, []).append(waited)

    # Reliability, from the text Claude Code writes when work is refused or fails.
    limit_sql, limit_args = _like('e.preview', LIMIT_PATTERNS)
    error_sql, error_args = _like('e.preview', API_ERROR_PATTERNS)
    limits = dict(conn.execute(f'''select s.host, count(*) from events e join sessions s on s.id=e.session_id
                                   where s.host is not null and e.kind='assistant' and {limit_sql} and e.ts>=? and e.ts<? group by s.host''',
                               (*limit_args, start, end)).fetchall())
    api_errors = dict(conn.execute(f'''select s.host, count(*) from events e join sessions s on s.id=e.session_id
                                       where s.host is not null and e.kind='assistant' and {error_sql} and e.ts>=? and e.ts<? group by s.host''',
                                   (*error_args, start, end)).fetchall())
    tool_errors = dict(conn.execute('''select s.host, count(*) from events e join sessions s on s.id=e.session_id
                                       where s.host is not null and e.kind='error' and e.ts>=? and e.ts<? group by s.host''', (start, end)).fetchall())
    last_limit = conn.execute(f'''select s.host, max(e.ts) from events e join sessions s on s.id=e.session_id
                                  where s.host is not null and e.kind='assistant' and {limit_sql} and e.ts>=? and e.ts<? group by s.host''',
                              (*limit_args, start, end)).fetchall()

    # Tasks: a task runs from a person's message to the agent's last action before the next one.
    tasks = conn.execute('''select s.host, t.started_at, t.ended_at, t.outcome from tasks t join sessions s on s.id=t.session_id
                            where s.host is not null and s.id not like ? and t.started_at>=? and t.started_at<?''',
                         (f'%{COMM_SUFFIX}', start, end)).fetchall()
    durations = [ended - started for _, started, ended, _ in tasks if ended is not None and ended >= started]
    outcomes = {}
    for _, _, _, outcome in tasks:
        outcomes[outcome or 'unknown'] = outcomes.get(outcome or 'unknown', 0) + 1

    # How work is spread: tools, and the hours of the week when agents are busy.
    tools = conn.execute('''select coalesce(e.tool,'unknown'), count(*) from events e join sessions s on s.id=e.session_id
                            where s.host is not null and e.kind='tool_call' and e.ts>=? and e.ts<? group by 1 order by 2 desc limit ?''',
                         (start, end, TOP_TOOLS)).fetchall()
    tool_total = conn.execute('''select count(*) from events e join sessions s on s.id=e.session_id
                                 where s.host is not null and e.kind='tool_call' and e.ts>=? and e.ts<?''', (start, end)).fetchone()[0]
    week = [[0] * 24 for _ in range(7)]
    system_marks = ','.join('?' for _ in SYSTEM_CHANNELS)
    for ts, in conn.execute(f'''select e.ts from events e join sessions s on s.id=e.session_id where s.host is not null and e.ts>=? and e.ts<? and (
                                  (s.id like '%{COMM_SUFFIX}' and coalesce(e.tool,'') not in ({system_marks}) and e.kind in ('user','assistant'))
                                  or (s.id not like '%{COMM_SUFFIX}' and e.kind in ('user','assistant','tool_call')))''',
                            (start, end, *SYSTEM_CHANNELS)):
        moment = datetime.fromtimestamp(ts)
        week[moment.weekday()][moment.hour] += 1

    # The same measures over time, in the activity grid's cells, for the graphs.
    count, size = getattr(win, 'count', 0), getattr(win, 'bucket_s', 0)
    cell = lambda ts: int((ts - start) // size) if size and start <= ts < end else -1  # noqa: E731
    trend = {name: [0] * count for name in ('replies', 'limit_messages', 'api_errors', 'tool_errors', 'tasks')}
    waits = [[] for _ in range(count)]
    for _, _, waited, ts in timed:
        if 0 <= cell(ts) < count:
            trend['replies'][cell(ts)] += 1
            waits[cell(ts)].append(waited)
    for name, where, args in (('limit_messages', f"e.kind='assistant' and {limit_sql}", limit_args),
                              ('api_errors', f"e.kind='assistant' and {error_sql}", error_args),
                              ('tool_errors', "e.kind='error'", ())):
        for ts, in conn.execute(f'''select e.ts from events e join sessions s on s.id=e.session_id
                                   where s.host is not null and {where} and e.ts>=? and e.ts<?''', (*args, start, end)):
            if 0 <= cell(ts) < count:
                trend[name][cell(ts)] += 1
    for _, started, _, _ in tasks:
        if 0 <= cell(started) < count:
            trend['tasks'][cell(started)] += 1
    trend['reply_median_s'] = [_quantile(w, .5) for w in waits]

    hosts = sorted(set(limits) | set(api_errors) | set(tool_errors) | set(by_host))
    return {
        'window': {'start': start, 'end': end, 'bucket_s': size, 'count': count},
        'trend': trend,
        'replies': {'all': _summary([w for _, _, w in found]),
                    'by_channel': {channel: _summary(waits) for channel, waits in sorted(by_channel.items())}},
        'tasks': {'count': len(tasks), 'median_s': _quantile(durations, .5), 'p90_s': _quantile(durations, .9), 'outcomes': outcomes},
        'reliability': {'limit_messages': sum(limits.values()), 'api_errors': sum(api_errors.values()), 'tool_errors': sum(tool_errors.values()),
                        'last_limit': {host: ts for host, ts in last_limit}},
        'tools': {'total': tool_total, 'top': [{'tool': name, 'count': n} for name, n in tools]},
        'week': week,
        'agents': [{'host': host, 'reply': _summary(by_host.get(host, [])), 'limit_messages': limits.get(host, 0),
                    'api_errors': api_errors.get(host, 0), 'tool_errors': tool_errors.get(host, 0)} for host in hosts],
        'generated_at': now,
    }
