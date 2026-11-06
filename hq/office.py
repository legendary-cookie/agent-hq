"""What each agent is doing right now, for the Office page.

Everything here comes from recorded events. Nothing is invented for show:

* An agent's activity is its newest event. Claude Code records tool names but not their inputs,
  so the words shown are the session's title plus a plain description of the tool.
* Zylos records that an agent sent or received a message on a channel, but not who to. So who an
  agent is talking to is inferred: a send on one agent and a receive on another within a few
  seconds count as one exchange. Several exchanges between the same agents count as a meeting.
"""
import re

from .fleet import AGENT_CHANNELS, COMM_SUFFIX, SYSTEM_CHANNELS, agent_states, fleet_status
from .insights import API_ERROR_PATTERNS, LIMIT_PATTERNS

LIMIT_RECENT_S = 3 * 3600      # a limit usually lasts hours, so it shows for a while
API_ERROR_RECENT_S = 15 * 60   # an API error is usually brief

WINDOW_S = 6 * 3600
MAX_ROWS = 8000  # newest first, so a busy fleet only loses its oldest history
EXCHANGE_WINDOW_S = 900
MEETING_WINDOW_S = 600
MEETING_EXCHANGES = 3
MATCH_BEFORE_S = 2  # clocks on two containers can differ slightly
MATCH_AFTER_S = 15
EARLIER = 3
FEED = 14
TITLE_CHARS = 90
TOOL_GAP_S = 180

SEARCH = re.compile(r'websearch|webfetch|search|browser|navigate|fetch|chrome', re.I)
DRIVE = re.compile(r'drive', re.I)
ORDERS = re.compile(r'order|shopify|shopee|lazada|tiktok|inventory|invoice', re.I)
READ = re.compile(r'^(read|grep|glob|ls|notebookread)$|read|list|get', re.I)
DRAFT = re.compile(r'^(write|edit|multiedit|notebookedit)$|write|draft|create|update', re.I)
COMMAND = re.compile(r'^(bash|powershell|shell|exec)', re.I)


def kind_of_tool(tool):
    """Which animation fits a tool. Specific matches come first, as names like get_orders fit several."""
    name = tool or ''
    if DRIVE.search(name):
        return 'drive'
    if ORDERS.search(name):
        return 'orders'
    if SEARCH.search(name):
        return 'search'
    if COMMAND.search(name):
        return 'command'
    if DRAFT.search(name):
        return 'draft'
    if READ.search(name):
        return 'read'
    return 'think'


def _clip(text):
    text = ' '.join((text or '').split())
    return text[:TITLE_CHARS - 1] + '…' if len(text) > TITLE_CHARS else text


def _activity(session_id, kind, tool, title, ts):
    """Describe one event, or return None if it's housekeeping."""
    if session_id.endswith(COMM_SUFFIX):
        if tool in SYSTEM_CHANNELS and tool != 'scheduler':
            return None
        if tool == 'scheduler':
            return {'kind': 'schedule', 'tool': tool, 'title': None, 'at': ts}
        return {'kind': 'message', 'tool': tool, 'title': None, 'at': ts, 'between_agents': tool in AGENT_CHANNELS}
    work = kind_of_tool(tool) if kind == 'tool_call' else 'think'
    return {'kind': work, 'tool': tool, 'title': _clip(title), 'at': ts}


def _exchanges(rows, now):
    """Pair each send with the nearest unclaimed receive on another agent."""
    sends = sorted((r for r in rows if r[2] == 'assistant' and now - r[3] < EXCHANGE_WINDOW_S), key=lambda r: r[3])
    receives = [r for r in rows if r[2] == 'user' and now - r[3] < EXCHANGE_WINDOW_S + MATCH_AFTER_S]
    claimed, found = set(), []
    for event_id, host, _, ts in sends:
        best = None
        for other in receives:
            if other[0] in claimed or other[1] == host or not -MATCH_BEFORE_S <= other[3] - ts <= MATCH_AFTER_S:
                continue
            if best is None or abs(other[3] - ts) < abs(best[3] - ts):
                best = other
        if best:
            claimed.add(best[0])
            found.append({'id': event_id, 'from': host, 'to': best[1], 'at': ts})
    return found


def _meetings(exchanges, now):
    """Agents joined by repeated recent exchanges, grouped so a three-way talk is one meeting."""
    counts = {}
    for item in exchanges:
        if now - item['at'] < MEETING_WINDOW_S:
            pair = tuple(sorted((item['from'], item['to'])))
            entry = counts.setdefault(pair, {'count': 0, 'since': item['at'], 'last': item['at']})
            entry['count'] += 1
            entry['since'] = min(entry['since'], item['at'])
            entry['last'] = max(entry['last'], item['at'])
    groups = []
    for pair, entry in counts.items():
        if entry['count'] < MEETING_EXCHANGES:
            continue
        joined = [group for group in groups if group['hosts'] & set(pair)]
        merged = {'hosts': set(pair), **entry}
        for group in joined:
            groups.remove(group)
            merged = {'hosts': merged['hosts'] | group['hosts'], 'count': merged['count'] + group['count'],
                      'since': min(merged['since'], group['since']), 'last': max(merged['last'], group['last'])}
        groups.append(merged)
    return [{**group, 'hosts': sorted(group['hosts'])} for group in sorted(groups, key=lambda g: g['since'])]


def office_snapshot(conn, now, health, meta):
    working, last_seen = agent_states(conn, now)
    rows = conn.execute(
        '''select e.id, s.host, s.id, s.title, e.kind, e.tool, e.ts from events e join sessions s on s.id=e.session_id
           where s.host is not null and e.ts>=? and e.ts<=? and e.kind in ('user','assistant','tool_call')
           order by e.ts desc, e.id desc limit ?''', (now - WINDOW_S, now + 60, MAX_ROWS)).fetchall()
    latest, latest_session, earlier, comm, from_person = {}, {}, {}, [], {}
    for event_id, host, session_id, title, kind, tool, ts in rows:
        if session_id.endswith(COMM_SUFFIX) and tool in AGENT_CHANNELS:
            comm.append((event_id, host, kind, ts))
        # The newest message a person sent this agent (rows come newest first), for the ringing phone.
        if session_id.endswith(COMM_SUFFIX) and kind == 'user' and tool not in AGENT_CHANNELS and tool not in SYSTEM_CHANNELS:
            from_person.setdefault(host, ts)
        item = _activity(session_id, kind, tool, title, ts)
        if not item:
            continue
        if host not in latest:
            latest[host] = item
            latest_session[host] = session_id
            continue
        # A reply between tool calls says little, so show the tool used just before it in the same session.
        if latest[host]['kind'] == 'think' and kind == 'tool_call' and session_id == latest_session[host] \
                and latest[host]['at'] - ts < TOOL_GAP_S and not latest[host].get('tool'):
            latest[host] = {**item, 'at': latest[host]['at']}
            continue
        history = earlier.setdefault(host, [])
        # Everything in one session is one piece of work, whatever tools it used.
        key = item['title'] or item['kind']
        if len(history) < EARLIER and key != (latest[host]['title'] or latest[host]['kind']) \
                and not any(key == (old['title'] or old['kind']) for old in history):
            history.append({'kind': item['kind'], 'title': item['title'], 'at': ts})
    exchanges = _exchanges(comm, now)
    meetings = _meetings(exchanges, now)
    feed = [{'at': item['at'], 'type': 'activity', 'host': host, 'kind': item['kind'], 'title': item['title'],
             'between_agents': bool(item.get('between_agents'))} for host, item in latest.items()]
    feed += [{'at': item['at'], 'type': 'exchange', 'host': item['from'], 'to': item['to']} for item in exchanges]
    feed += [{'at': item['since'], 'type': 'meeting', 'hosts': item['hosts']} for item in meetings]
    feed.sort(key=lambda entry: entry['at'], reverse=True)

    # Recent trouble, from the text Claude Code writes when the plan or the API refuses work.
    trouble = {}
    for name, patterns, window in (('limit_at', LIMIT_PATTERNS, LIMIT_RECENT_S), ('api_error_at', API_ERROR_PATTERNS, API_ERROR_RECENT_S)):
        clause = ' or '.join('e.preview like ?' for _ in patterns)
        for host, ts in conn.execute(f'''select s.host, max(e.ts) from events e join sessions s on s.id=e.session_id
                                         where s.host is not null and e.kind='assistant' and ({clause}) and e.ts>=? group by s.host''',
                                     (*patterns, now - window)):
            trouble.setdefault(host, {})[name] = ts

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
            'department': info.get('department'),
            'status': status,
            'error': state.get('error') if state else None,
            'catching_up': bool(state and state.get('catching_up')),
            'last_seen': last_seen.get(host),
            'activity': latest.get(host),
            'earlier': earlier.get(host, []),
            'last_from_person': from_person.get(host),
            'limit_at': trouble.get(host, {}).get('limit_at'),
            'api_error_at': trouble.get(host, {}).get('api_error_at'),
        })
    return {'now': now, 'agents': agents, 'exchanges': exchanges, 'meetings': meetings, 'feed': feed[:FEED]}
