"""One row per remote agent: who it belongs to, what it's doing, and what it used."""
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .kpis import value_usd
from .status import session_status

COMM_SUFFIX = '/zylos:comm'
# Zylos logs its own housekeeping as messages, which isn't anyone talking to the agent.
# Inferred from real logs: "system" was inbound only and "void" outbound only.
SYSTEM_CHANNELS = ('system', 'scheduler', 'void', 'shell')
# Channels where agents mostly talk to each other. It's real work, so it counts as activity, but
# it's reported apart from messages so that those reflect people.
AGENT_CHANNELS = ('hxa-connect',)
RECENT_S = 3600
QUIET_S = 7 * 86400
WORKING_S = 600  # the longest a session can count as working, so older ones need no lookup
TOKENS = ('tokens_in', 'tokens_out', 'tokens_cache_read', 'tokens_cache_write')
TOP_TIER = ('opus', 'fable')
EMPTY_USAGE = {'tokens': 0, 'cache_read_tokens': 0, 'value_usd': 0.0, 'top_tier_usd': 0.0, 'unpriced_tokens': 0}
_SHAPES = {'today': (3600, 24), 'week': (6 * 3600, 28), 'month': (86400, 30)}


@dataclass(frozen=True)
class Window:
    start: float
    end: float
    prev_start: float
    prev_end: float
    bucket_s: int
    count: int


def rolling_window(name: str, now: float) -> Window:
    """The last 24 hours, 7 days or 30 days, in cells that end at the next clock boundary."""
    if name not in _SHAPES:
        raise ValueError('invalid range')
    bucket, count = _SHAPES[name]
    moment = datetime.fromtimestamp(now)
    if bucket == 86400:
        edge = moment.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    else:
        hours = bucket // 3600
        edge = moment.replace(hour=moment.hour - moment.hour % hours, minute=0, second=0, microsecond=0) + timedelta(hours=hours)
    end = edge.timestamp()
    start = end - bucket * count
    return Window(start, end, start - bucket * count, start, bucket, count)


def load_fleet(config_dir: Path) -> dict:
    """Read the optional fleet.json. A missing or broken file only loses the friendly names."""
    try:
        data = json.loads((Path(config_dir) / 'fleet.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'brand': None, 'agents': {}}
    if not isinstance(data, dict):
        return {'brand': None, 'agents': {}}
    agents = data.get('agents')
    return {'brand': data.get('brand') if isinstance(data.get('brand'), str) else None,
            'agents': {k: v for k, v in agents.items() if isinstance(v, dict)} if isinstance(agents, dict) else {}}


def _usage(conn, cfg, start, end):
    """Return ({host: usage}, {model: usage}) for events in the range."""
    by_host, by_model = {}, {}
    sums = ', '.join(f'coalesce(sum(e.{name}),0)' for name in TOKENS)
    rows = conn.execute(f'''select s.host, coalesce(e.model, s.model), {sums} from events e join sessions s on s.id=e.session_id
                            where s.host is not null and e.ts>=? and e.ts<? group by s.host, coalesce(e.model, s.model)''', (start, end))
    for host, model, *numbers in rows:
        total = sum(numbers)
        if not total:
            continue
        value = value_usd(*numbers, cfg.pricebook.get(model))
        top_tier = any(name in (model or '').lower() for name in TOP_TIER)
        for item in (by_host.setdefault(host, dict(EMPTY_USAGE)), by_model.setdefault(model or 'unknown', dict(EMPTY_USAGE))):
            item['tokens'] += total
            item['cache_read_tokens'] += numbers[2]
            if value is None:
                item['unpriced_tokens'] += total
            else:
                item['value_usd'] += value
                if top_tier:
                    item['top_tier_usd'] += value
    return by_host, by_model


def _per_cell(conn, where, args, win):
    """Counts of matching events in each cell of the window, oldest first."""
    out = [0] * win.count
    for cell, count in conn.execute(f'''select cast((e.ts-?)/? as integer), count(*) from events e join sessions s on s.id=e.session_id
                                       where s.host is not null and e.ts>=? and e.ts<? and {where} group by 1''',
                                    (win.start, win.bucket_s, win.start, win.end, *args)):
        if 0 <= cell < win.count:
            out[cell] = count
    return out


def fleet_series(conn, cfg, win, actions):
    """The whole fleet over time, in the same cells as the activity grid, for the graphs."""
    tokens, value = [0] * win.count, [0.0] * win.count
    sums = ', '.join(f'coalesce(sum(e.{name}),0)' for name in TOKENS)
    for cell, model, *numbers in conn.execute(f'''select cast((e.ts-?)/? as integer), coalesce(e.model, s.model), {sums}
                                                  from events e join sessions s on s.id=e.session_id
                                                  where s.host is not null and e.ts>=? and e.ts<? group by 1, 2''',
                                              (win.start, win.bucket_s, win.start, win.end)):
        if not 0 <= cell < win.count:
            continue
        tokens[cell] += sum(numbers)
        priced = value_usd(*numbers, cfg.pricebook.get(model))
        if priced is not None:
            value[cell] += priced
    skipped = (*SYSTEM_CHANNELS, *AGENT_CHANNELS)
    people = f"s.id like '%{COMM_SUFFIX}' and coalesce(e.tool,'') not in ({','.join('?' for _ in skipped)}) and e.kind in ('user','assistant')"
    between = f"s.id like '%{COMM_SUFFIX}' and e.tool in ({','.join('?' for _ in AGENT_CHANNELS)}) and e.kind in ('user','assistant')"
    return {
        'actions': actions,
        'messages': _per_cell(conn, people, skipped, win),
        'agent_messages': _per_cell(conn, between, AGENT_CHANNELS, win),
        'tokens': tokens,
        'value_usd': [round(v, 4) for v in value],
        'errors': _per_cell(conn, "e.kind='error'", (), win),
    }


def _counts(conn, where, args):
    return dict(conn.execute(f'select s.host, count(*) from events e join sessions s on s.id=e.session_id where s.host is not null and {where} group by s.host', args).fetchall())


def agent_states(conn, now):
    """Return ({host: sessions working now}, {host: newest event time})."""
    working, last_seen = {}, {}
    for sid, host, seen, ended, imported in conn.execute('select id, host, last_event_at, ended_at, imported from sessions where host is not null'):
        if seen is None:
            continue
        last_seen[host] = max(last_seen.get(host, seen), seen)
        if sid.endswith(COMM_SUFFIX) or ended is not None or now - seen >= WORKING_S:
            continue
        kind = conn.execute('select kind from events where session_id=? order by ts desc, id desc limit 1', (sid,)).fetchone()
        if session_status(seen, kind[0] if kind else None, ended, now, bool(imported)) == 'working':
            working[host] = working.get(host, 0) + 1
    return working, last_seen


def fleet_status(health, working, last_seen, now):
    if health and health.get('state') == 'partial' and health.get('error'):
        return 'attention'
    if working:
        return 'working'
    if last_seen is not None and now - last_seen < RECENT_S:
        return 'recent'
    if last_seen is not None and now - last_seen < QUIET_S:
        return 'idle'
    return 'quiet'


def _gap(status, health, win):
    """How many of the newest cells an unreachable agent has no data for."""
    if status != 'attention':
        return 0
    last_ok = (health or {}).get('last_ok')
    if not last_ok:
        return win.count
    return max(0, min(win.count, win.count - int((last_ok - win.start) // win.bucket_s) - 1))


def fleet_overview(conn, cfg, win, now, health, meta) -> dict:
    working, last_seen = agent_states(conn, now)
    usage, models = _usage(conn, cfg, win.start, win.end)
    previous, _ = _usage(conn, cfg, win.prev_start, win.prev_end)
    channels = ','.join('?' for _ in SYSTEM_CHANNELS)
    skipped = (*SYSTEM_CHANNELS, *AGENT_CHANNELS)
    people = ','.join('?' for _ in skipped)
    message = f"s.id like '%{COMM_SUFFIX}' and coalesce(e.tool,'') not in ({people}) and e.kind in ('user','assistant') and e.ts>=? and e.ts<?"
    received = _counts(conn, message + " and e.kind='user'", (*skipped, win.start, win.end))
    sent = _counts(conn, message + " and e.kind='assistant'", (*skipped, win.start, win.end))
    before = _counts(conn, message, (*skipped, win.prev_start, win.prev_end))
    between = _counts(conn, f"s.id like '%{COMM_SUFFIX}' and e.tool in ({','.join('?' for _ in AGENT_CHANNELS)}) and e.kind in ('user','assistant') and e.ts>=? and e.ts<?", (*AGENT_CHANNELS, win.start, win.end))
    errors = _counts(conn, "e.kind='error' and e.ts>=? and e.ts<?", (win.start, win.end))
    tasks = dict(conn.execute('select s.host, count(*) from tasks t join sessions s on s.id=t.session_id where s.host is not null and t.started_at>=? and t.started_at<? group by s.host', (win.start, win.end)).fetchall())
    cells = {}
    # Each cell counts all work: messages, plus what the agent did in its sessions. Tool results and
    # turn ends are left out because each one only closes something that's already counted.
    for host, cell, count in conn.execute(f'''select s.host, cast((e.ts-?)/? as integer), count(*) from events e join sessions s on s.id=e.session_id
                                              where s.host is not null and e.ts>=? and e.ts<? and (
                                                (s.id like '%{COMM_SUFFIX}' and coalesce(e.tool,'') not in ({channels}) and e.kind in ('user','assistant'))
                                                or (s.id not like '%{COMM_SUFFIX}' and e.kind in ('user','assistant','tool_call')))
                                              group by 1, 2''', (win.start, win.bucket_s, win.start, win.end, *SYSTEM_CHANNELS)):
        cells.setdefault(host, {})[cell] = count

    agents = []
    for host in sorted(set(health) | set(last_seen) | set(meta['agents'])):
        info = meta['agents'].get(host, {})
        state = health.get(host)
        status = fleet_status(state, working.get(host), last_seen.get(host), now)
        agents.append({
            'host': host,
            'label': info.get('label') or host,
            'owner': info.get('owner'),
            'group': info.get('group') or 'Agents',
            'status': status,
            'error': state.get('error') if state else None,
            'catching_up': bool(state and state.get('catching_up')),
            'last_seen': last_seen.get(host),
            'messages_in': received.get(host, 0),
            'messages_out': sent.get(host, 0),
            'agent_messages': between.get(host, 0),
            'tasks': tasks.get(host, 0),
            'errors': errors.get(host, 0),
            'cells': [cells.get(host, {}).get(index, 0) for index in range(win.count)],
            'gap': _gap(status, state, win),
            **usage.get(host, EMPTY_USAGE),
        })
    value = sum(a['value_usd'] for a in agents)
    silent = [a for a in agents if a['value_usd'] > 0 and not a['messages_in'] + a['messages_out']]
    days = win.bucket_s * win.count / 86400
    return {
        'window': {'start': win.start, 'end': win.end, 'bucket_s': win.bucket_s, 'count': win.count},
        'totals': {
            'agents': len(agents),
            'working': sum(a['status'] == 'working' for a in agents),
            'active': sum(a['status'] in ('working', 'recent') for a in agents),
            'attention': sum(a['status'] == 'attention' for a in agents),
            'quiet': sum(a['status'] == 'quiet' for a in agents),
            'catching_up': sum(a['catching_up'] for a in agents),
            'messages': sum(a['messages_in'] + a['messages_out'] for a in agents),
            'previous_messages': sum(before.values()),
            'agent_messages': sum(a['agent_messages'] for a in agents),
            'tasks': sum(a['tasks'] for a in agents),
            'tokens': sum(a['tokens'] for a in agents),
            'cache_read_tokens': sum(a['cache_read_tokens'] for a in agents),
            'actions': sum(sum(a['cells']) for a in agents),
            'value_usd': value,
            'top_tier_usd': sum(a['top_tier_usd'] for a in agents),
            'projected_30d_usd': value / days * 30,
            'silent_usd': sum(a['value_usd'] for a in silent),
            'silent_agents': len(silent),
            'unpriced_tokens': sum(a['unpriced_tokens'] for a in agents),
            'previous_value_usd': sum(item['value_usd'] for item in previous.values()),
            'errors': sum(a['errors'] for a in agents),
        },
        'series': fleet_series(conn, cfg, win, [sum(a['cells'][i] for a in agents) for i in range(win.count)]),
        'models': sorted(({'model': name, **item} for name, item in models.items()), key=lambda item: item['tokens'], reverse=True),
        'agents': agents,
    }
