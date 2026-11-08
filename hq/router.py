"""9router, the AI router on its own container: health, accounts, rotation, limits, models, settings and usage.

HQ finds the container by name (`9router` in its hostname) on the Proxmox host and reads 9router's SQLite
database read-only inside it, over HQ's existing SSH key. Tokens and refresh tokens are never read out: only
whitelisted fields leave the container. API keys can be created, paused, resumed and deleted through 9router's
own API (Router.key_action); a key's value leaves the container only when the owner copies it or has just made it.
"""
import json
import re
import threading
import time

from .control.pve import ControlError

REFRESH_S = 60
USAGE_DAYS = 30

# Runs inside the 9router container as root. Opens the database read-only and prints one JSON line.
INNER = r'''
import json, os, sqlite3, subprocess, time, urllib.request
out = {'errors': []}
def svc(name):
    try:
        return subprocess.run(['systemctl', 'is-active', name], capture_output=True, text=True, timeout=10).stdout.strip() or 'unknown'
    except Exception:
        return 'unknown'
out['services'] = {'9router': svc('9router'), 'headroom': svc('headroom')}
try:
    with urllib.request.urlopen('http://127.0.0.1:20128/api/health', timeout=5) as r:
        out['health'] = r.status
except Exception as exc:
    out['health'] = str(exc)[:120]
try:
    with urllib.request.urlopen('http://127.0.0.1:8787/health', timeout=5) as r:
        out['headroom_health'] = r.status
except Exception as exc:
    out['headroom_health'] = str(exc)[:120]
try:
    db = sqlite3.connect('file:/var/lib/9router/db/data.sqlite?mode=ro', uri=True, timeout=10)
    out['version'] = dict(db.execute('select key, value from _meta')).get('appVersion')
    safe = ('testStatus', 'expiresAt', 'lastError', 'lastErrorAt', 'errorCode', 'rateLimitedUntil', 'backoffLevel', 'lastPingAt')
    conns = []
    for row in db.execute('select id, provider, authType, name, email, priority, isActive, data, updatedAt from providerConnections order by priority'):
        data = json.loads(row[7] or '{}')
        item = {'id': row[0], 'provider': row[1], 'auth': row[2], 'name': row[3], 'email': row[4], 'priority': row[5], 'active': bool(row[6]), 'updated': row[8]}
        item.update({k: data.get(k) for k in safe})
        if isinstance(item.get('lastError'), str):
            item['lastError'] = item['lastError'][:200]
        conns.append(item)
    out['connections'] = conns
    s = json.loads((db.execute('select data from settings').fetchone() or ['{}'])[0] or '{}')
    keep = ('fallbackStrategy', 'providerStrategies', 'comboStrategies', 'headroomEnabled', 'headroomCodeAware',
            'cavemanEnabled', 'cavemanLevel', 'ponytailEnabled', 'ponytailLevel')
    settings = {k: s.get(k) for k in keep if k in s}
    ap = s.get('claudeAutoPing') or {}
    settings['autoPing'] = {'enabled': bool(ap.get('enabled')),
                            'connections': {k: bool(v) for k, v in (ap.get('connections') or {}).items()},
                            'schedules': {k: bool((v or {}).get('enabled')) for k, v in (ap.get('schedules') or {}).items()}}
    out['settings'] = settings
    out['combos'] = [{'name': r[0], 'kind': r[1], 'models': r[2]} for r in db.execute('select name, kind, models from combos')]
    # Keys: never the value itself (HQ copies it on request through key_action instead).
    keys = []
    for kid, name, active, created, restricted, allow in db.execute('select id, name, isActive, createdAt, accessRestricted, accessAllow from apiKeys'):
        try:
            allow = json.loads(allow) if allow else []
        except ValueError:
            allow = []
        keys.append({'id': kid, 'name': name, 'active': bool(active), 'created': created, 'restricted': bool(restricted),
                     'allow': [str(a)[:80] for a in allow][:50] if isinstance(allow, list) else []})
    out['api_keys'] = keys
    out['disabled_models'] = {r[0]: r[1] for r in db.execute("select key, value from kv where scope='disabledModels'")}
    key_ids = {r[0]: r[1] for r in db.execute('select key, id from apiKeys')}
    since = time.time() - %(days)d * 86400
    usage = []
    for ts, provider, model, conn, key, pt, ct, cost, status, tokens in db.execute(
            'select timestamp, provider, model, connectionId, apiKey, promptTokens, completionTokens, cost, status, tokens from usageHistory order by id desc limit 50000'):
        try:
            t = float(ts)
            t = t / 1000 if t > 1e11 else t
        except (TypeError, ValueError):
            try:
                from datetime import datetime
                t = datetime.fromisoformat(str(ts).replace('Z', '+00:00')).timestamp()
            except ValueError:
                continue
        if t < since:
            break
        usage.append([round(t), provider, model, conn, key_ids.get(key, 'other') if key else None, pt or 0, ct or 0, cost or 0, status])
    out['usage'] = usage
except Exception as exc:
    out['errors'].append('database: ' + str(exc)[:200])
try:
    r = json.load(open('/var/lib/9router/rotate-status.json'))
    out['rotation'] = {'ran': r.get('ran'), 'ok': r.get('ok'), 'results': [
        {'provider': x.get('provider'), 'order': [{k: o.get(k) for k in ('priority', 'account', 'tier', 'why')} for o in (x.get('order') or [])]}
        for x in (r.get('results') or [])]}
except Exception as exc:
    out['errors'].append('rotation: ' + str(exc)[:120])
try:
    cat = json.load(open('/var/lib/9router/model-catalog.json'))
    provs = {c['provider'] for c in out.get('connections', [])}
    out['models'] = {p: sorted((cat.get('providers') or {}).get(p) or {}) for p in provs}
    out['catalog'] = {'models': len(cat.get('models') or {}), 'providers': len(cat.get('providers') or {}), 'synced': cat.get('syncedAt')}
except Exception as exc:
    out['errors'].append('models: ' + str(exc)[:120])
print(json.dumps(out))
''' % {'days': USAGE_DAYS}

# Runs on the Proxmox host: find the 9router container and run INNER inside it.
SCRIPT = r'''
import json, subprocess, sys
inner = sys.stdin.read()
rows = subprocess.run(['pct', 'list'], capture_output=True, text=True, timeout=30).stdout.splitlines()[1:]
found = [r.split() for r in rows if '9router' in r]
if not found:
    print(json.dumps({'missing': True})); sys.exit(0)
vmid, status = found[0][0], found[0][1]
if status != 'running':
    print(json.dumps({'vmid': vmid, 'status': status})); sys.exit(0)
out = subprocess.run(['pct', 'exec', vmid, '--', 'python3', '-c', inner], capture_output=True, text=True, timeout=90)
answer = json.loads(out.stdout.strip().splitlines()[-1]) if out.stdout.strip() else {'errors': [(out.stderr or 'no answer')[-200:]]}
answer.update({'vmid': vmid, 'status': status})
print(json.dumps(answer))
'''

# Runs inside the 9router container as root: one key action through 9router's own API on localhost, signed
# with 9router's local CLI token (made the same way as 9router's CLI does, from files only root can read),
# so HQ needs no dashboard password. __REQ__ is replaced with the checked request. Prints one JSON line.
KEY_INNER = r'''
import hashlib, json, urllib.error, urllib.request
req = json.loads(__REQ__)
d = '/var/lib/9router'
tok = hashlib.sha256((open(d + '/machine-id').read().strip() + '9r-cli-auth' + open(d + '/auth/cli-secret').read().strip()).encode()).hexdigest()[:16]
class Bad(Exception):
    pass
def call(method, path, body=None):
    r = urllib.request.Request('http://127.0.0.1:20128' + path, method=method, data=None if body is None else json.dumps(body).encode(),
                               headers={'x-9r-cli-token': tok, 'content-type': 'application/json'})
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get('error')
        except Exception:
            msg = None
        raise Bad(str(msg or '9router answered %s' % e.code)[:200])
try:
    a, kid = req['action'], req.get('id')
    if a == 'create':
        made = call('POST', '/api/keys', {'name': req['name']})
        if req.get('access'):
            call('PUT', '/api/keys/' + made['id'], {'access': req['access']})
        out = {'id': made['id'], 'name': made['name'], 'key': made['key']}
    elif a in ('pause', 'resume'):
        call('PUT', '/api/keys/' + kid, {'isActive': a == 'resume'}); out = {'ok': True}
    elif a == 'delete':
        call('DELETE', '/api/keys/' + kid); out = {'ok': True}
    elif a == 'reveal':
        out = {'key': call('GET', '/api/keys/' + kid)['key']['key']}
    else:
        raise Bad('unknown action')
except Bad as exc:
    out = {'error': str(exc)}
except Exception as exc:
    out = {'error': type(exc).__name__ + ': ' + str(exc)[:160]}
print(json.dumps(out))
'''
KEY_ACTIONS = ('create', 'pause', 'resume', 'delete', 'reveal')
KEY_ID = re.compile(r'[A-Za-z0-9-]{1,64}')
KEY_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,59}')


def check_key_request(action, snapshot, key_id=None, name=None, access=None):
    """The request for KEY_INNER, checked against what 9router has now. Raises ValueError."""
    if action not in KEY_ACTIONS:
        raise ValueError('unknown key action')
    keys = (snapshot or {}).get('api_keys') or []
    if action == 'create':
        if not isinstance(name, str) or not KEY_NAME.fullmatch(name.strip()):
            raise ValueError('name the key with letters, numbers, dots, dashes or underscores, up to 60 characters')
        name = name.strip()
        if any(k.get('name') == name for k in keys):
            raise ValueError(f'a key called {name} already exists')
        req = {'action': 'create', 'name': name}
        if access is not None:
            if not isinstance(access, dict) or set(access) - {'restricted', 'allow'} or not isinstance(access.get('restricted'), bool):
                raise ValueError('access must be {restricted, allow}')
            allow = access.get('allow') or []
            if not isinstance(allow, list) or len(allow) > 50 or not all(isinstance(m, str) and 0 < len(m.strip()) <= 120 for m in allow):
                raise ValueError('allow up to 50 model names')
            req['access'] = {'restricted': access['restricted'], 'allow': [m.strip() for m in allow]}
        return req
    if not isinstance(key_id, str) or not KEY_ID.fullmatch(key_id) or not any(k.get('id') == key_id for k in keys):
        raise ValueError('unknown key')
    return {'action': action, 'id': key_id}


WHY = re.compile(r'(?P<window>[\w ()]+?) (?P<left>\d+(?:\.\d+)?)% left(?:, resets (?P<reset>\S+))?')


def _when(value):
    """ISO time or epoch (s or ms) to epoch seconds."""
    if value in (None, ''):
        return None
    try:
        t = float(value)
        return t / 1000 if t > 1e11 else t
    except (TypeError, ValueError):
        pass
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def parse_why(text):
    """'weekly (7d) 58% left, resets 2026-11-08T08:00:00Z' -> window, percent left, reset time."""
    match = WHY.search(text or '')
    if not match:
        return None
    return {'window': match.group('window').strip(), 'left_pct': float(match.group('left')), 'resets_at': _when(match.group('reset'))}


def _auto_ping(ap, conn):
    at_reset = bool((ap.get('connections') or {}).get(conn.get('id')))
    scheduled = bool((ap.get('schedules') or {}).get(conn.get('id')))
    return {'auto_ping': at_reset or scheduled,
            'auto_ping_how': 'at reset' if at_reset and not scheduled else 'scheduled' if scheduled and not at_reset else 'at reset and scheduled' if at_reset else None,
            'last_ping_at': _when(conn.get('lastPingAt'))}


def summarize(raw, now):
    """Shape what the container printed into what the Router page shows."""
    if raw.get('missing'):
        return {'found': False}
    out = {'found': True, 'vmid': raw.get('vmid'), 'status': raw.get('status'), 'version': raw.get('version'),
           'services': raw.get('services') or {}, 'health': raw.get('health'), 'headroom_health': raw.get('headroom_health'),
           'settings': raw.get('settings') or {}, 'combos': raw.get('combos') or [],
           'models': raw.get('models') or {}, 'catalog': raw.get('catalog') or {}, 'disabled_models': raw.get('disabled_models') or {},
           'errors': raw.get('errors') or [], 'checked_at': now}
    out['up'] = out['status'] == 'running' and out['services'].get('9router') == 'active' and out['health'] == 200
    rotation = raw.get('rotation') or {}
    limits = {}
    order = {}
    for result in rotation.get('results') or []:
        for position, item in enumerate(result.get('order') or []):
            key = (result.get('provider'), item.get('priority'))
            limits[key] = parse_why(item.get('why'))
            order[key] = position + 1
    usage = raw.get('usage') or []
    day = now - 86400
    conns = []
    for c in raw.get('connections') or []:
        key = (c.get('provider'), c.get('priority'))
        mine = [u for u in usage if u[3] == c.get('id')]
        limited = _when(c.get('rateLimitedUntil'))
        conns.append({
            'id': c.get('id'), 'provider': c.get('provider'), 'auth': c.get('auth'), 'name': c.get('name') or c.get('email'),
            'email': c.get('email'), 'priority': c.get('priority'), 'active': c.get('active'), 'test_status': c.get('testStatus'),
            'token_expires_at': _when(c.get('expiresAt')), 'last_error': c.get('lastError'), 'last_error_at': _when(c.get('lastErrorAt')),
            'error_code': c.get('errorCode'), 'backoff_level': c.get('backoffLevel') or 0,
            'rate_limited_until': limited if limited and limited > now else None,
            'limit': limits.get(key), 'rotation_position': order.get(key),
            'requests_24h': sum(1 for u in mine if u[0] >= day), 'tokens_24h': sum(u[5] + u[6] for u in mine if u[0] >= day),
            'last_used_at': max((u[0] for u in mine), default=None),
            # 9router pings an account when its own switch is on (ping right after each limit reset) or its schedule is.
            # The top-level "enabled" flag isn't what 9router checks, so it isn't used here.
            **_auto_ping(out['settings'].get('autoPing') or {}, c),
        })
    out['connections'] = conns
    keys = []
    for k in raw.get('api_keys') or []:
        mine = [u for u in usage if u[4] == k.get('id')]
        ct = re.match(r'ct(\d+)(?:-|$)', k.get('name') or '')
        keys.append({**k, 'vmid': int(ct.group(1)) if ct else None,
                     'requests_24h': sum(1 for u in mine if u[0] >= day), 'requests_30d': len(mine),
                     'tokens_30d': sum(u[5] + u[6] for u in mine), 'cost_30d': round(sum(u[7] for u in mine), 4),
                     'errors_30d': sum(1 for u in mine if u[8] not in (None, 'ok', 'success', 200, '200')),
                     'last_used_at': max((u[0] for u in mine), default=None)})
    out['api_keys'] = keys
    # Which account the router uses next: the first in rotation order that's active and not cooling down.
    ready = sorted((c for c in conns if c['active'] and not c['rate_limited_until']), key=lambda c: (c['rotation_position'] or 99, c['priority'] or 99))
    out['in_use'] = ready[0]['id'] if ready else None
    out['rotation_ran_at'] = _when(rotation.get('ran'))
    out['usage'] = usage_summary(usage, conns, now, {k['id']: k['name'] for k in keys})
    out['plan'] = plan_summary(usage, conns, keys, now)
    for k in keys:
        k.update(out['plan']['keys'].get(k['id']) or {'plan_pct': None, 'cost_week': 0.0})
    return out


WEEK = 7 * 86400


def plan_summary(usage, conns, keys, now):
    """How much of the Claude plans' weekly limits each agent key used, as the main usage figure.

    Subscription (OAuth) accounts are paid by plan, so the real cost is how much of the weekly limit is gone.
    Each account's current week runs to its reset time. A key's share of an account is its share of that
    account's usage in the week, weighted by API-equivalent cost (a Fable request uses the limit faster than a
    Haiku one). A key's plan % is the sum over accounts of share x that account's % used, divided by the number
    of plan accounts, so it's a % of the fleet's whole weekly capacity, and the keys add up to the fleet total.
    The week's usage from before an account came to 9router is spread over the keys by the same shares.
    API-key accounts are paid per token: their cost is real money and is reported as such.
    """
    plan_accounts, api_cost, accounts = [], 0.0, []
    for c in conns:
        is_plan = (c.get('auth') or '').lower() in ('oauth', 'oauth2', 'subscription')
        limit = c.get('limit') or {}
        resets = limit.get('resets_at')
        start = resets - WEEK if resets and 'week' in (limit.get('window') or '') else now - WEEK
        mine = [u for u in usage if u[3] == c['id'] and u[0] >= start]
        cost = sum(u[7] for u in mine)
        used = None if limit.get('left_pct') is None else max(0.0, min(100.0, 100.0 - float(limit['left_pct'])))
        row = {'id': c['id'], 'name': c['name'], 'plan': is_plan, 'used_pct': used, 'resets_at': resets, 'week_start': start,
               'cost_week': round(cost, 2), 'by_key': {}}
        for u in mine:
            row['by_key'][u[4]] = row['by_key'].get(u[4], 0.0) + u[7]
        accounts.append(row)
        if is_plan:
            plan_accounts.append(row)
        else:
            api_cost += cost
    n = len(plan_accounts)
    per_key = {}
    for k in keys:
        pct = 0.0
        for a in plan_accounts:
            if a['used_pct'] is not None and a['cost_week'] > 0:
                pct += a['by_key'].get(k['id'], 0.0) / sum(a['by_key'].values()) * a['used_pct']
        cost = sum(a['by_key'].get(k['id'], 0.0) for a in accounts)
        per_key[k['id']] = {'plan_pct': round(pct / n, 2) if n else None, 'cost_week': round(cost, 2),
                            'api_paid': round(sum(a['by_key'].get(k['id'], 0.0) for a in accounts if not a['plan']), 2)}
    known = [a['used_pct'] for a in plan_accounts if a['used_pct'] is not None]
    billing = 'none' if not accounts else 'plan' if not api_cost and plan_accounts else 'api' if not plan_accounts else 'mixed'
    by_vmid = {}
    for k in keys:
        if k.get('vmid') is not None:
            v = by_vmid.setdefault(k['vmid'], {'plan_pct': 0.0, 'cost_week': 0.0, 'api_paid': 0.0})
            for f in v:
                v[f] = round(v[f] + (per_key[k['id']][f] or 0.0), 2)
    for a in accounts:
        a.pop('by_key')
    used_pct = round(sum(known) / len(known), 1) if known else None
    attributed = sum(v['plan_pct'] or 0.0 for v in per_key.values())
    return {'billing': billing, 'used_pct': used_pct, 'plan_accounts': n,
            # Weekly use 9router can't put on any key: accounts used this week before they came to 9router (the old pool).
            'unattributed_pct': round(max(0.0, used_pct - attributed), 1) if used_pct is not None else None,
            'api_cost_week': round(api_cost, 2), 'accounts': accounts, 'keys': per_key, 'by_vmid': by_vmid}


def usage_summary(usage, conns, now, key_names=None):
    """Totals, a 30-day daily series, and breakdowns by model, account and API key."""
    names = {c['id']: c['name'] for c in conns}
    days = [0] * USAGE_DAYS
    start = now - USAGE_DAYS * 86400
    by = {'model': {}, 'account': {}, 'key': {}}
    total = {'requests': 0, 'prompt_tokens': 0, 'completion_tokens': 0, 'cost': 0.0, 'errors': 0}
    for t, provider, model, conn, key, pt, ct, cost, status in usage:
        if t < start:
            continue
        total['requests'] += 1
        total['prompt_tokens'] += pt
        total['completion_tokens'] += ct
        total['cost'] += cost
        bad = status not in (None, 'ok', 'success', 200, '200')
        total['errors'] += bad
        days[min(USAGE_DAYS - 1, int((t - start) // 86400))] += pt + ct
        for kind, label in (('model', f'{provider}/{model}' if provider else model), ('account', names.get(conn, conn or 'unknown')), ('key', (key_names or {}).get(key, key) if key else 'none')):
            row = by[kind].setdefault(label or 'unknown', {'requests': 0, 'tokens': 0, 'cost': 0.0})
            row['requests'] += 1
            row['tokens'] += pt + ct
            row['cost'] += cost
    total['cost'] = round(total['cost'], 4)
    return {'total': total, 'daily_tokens': days, 'days': USAGE_DAYS,
            **{f'by_{k}': sorted(({'name': n, **v, 'cost': round(v['cost'], 4)} for n, v in rows.items()), key=lambda r: -r['tokens']) for k, rows in by.items()}}


HOST = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}')


class KeyAssignments:
    """Who each 9router API key is for: agents (by HQ host) and other tools, kept by HQ. 9router has no such field.
    Keys named ct<id>-... are matched to that agent automatically when nothing is saved for them."""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()

    def all(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def set(self, key_id, agents=None, tools=None, note=None):
        if not isinstance(key_id, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,64}', key_id):
            raise ValueError('unknown key')
        agents = agents or []
        tools = tools or []
        if not isinstance(agents, list) or len(agents) > 50 or not all(isinstance(a, str) and HOST.fullmatch(a) for a in agents):
            raise ValueError('choose up to 50 agents')
        if not isinstance(tools, list) or len(tools) > 20 or not all(isinstance(x, str) and 0 < len(x.strip()) <= 60 for x in tools):
            raise ValueError('give up to 20 tool names of up to 60 characters')
        note = (note or '').strip()
        if len(note) > 200:
            raise ValueError('keep the note under 200 characters')
        with self._lock:
            data = self.all()
            if not agents and not tools and not note:
                data.pop(key_id, None)
            else:
                data[key_id] = {'agents': sorted(set(agents)), 'tools': [x.strip() for x in tools], 'note': note}
            self.path.write_text(json.dumps(data), encoding='utf-8')
        return data.get(key_id)


class Router:
    def __init__(self, pve, clock=time.time):
        self.pve, self.clock = pve, clock
        self._lock = threading.Lock()
        self._snapshot, self._error = None, None

    def refresh(self):
        out = self.pve.run(['python3', '-c', SCRIPT], timeout=150, stdin=INNER)
        snapshot = summarize(json.loads(out.strip().splitlines()[-1]), self.clock())
        with self._lock:
            self._snapshot, self._error = snapshot, None
        return snapshot

    def snapshot(self):
        with self._lock:
            return self._snapshot, self._error

    def key_action(self, action, key_id=None, name=None, access=None):
        """Create, pause, resume, delete or reveal a 9router API key. The value (create, reveal) is returned to
        the caller only: it isn't kept, logged or put in the snapshot."""
        req = check_key_request(action, self.snapshot()[0], key_id, name, access)
        inner = KEY_INNER.replace('__REQ__', repr(json.dumps(req)))
        out = self.pve.run(['python3', '-c', SCRIPT], timeout=120, stdin=inner)
        answer = json.loads(out.strip().splitlines()[-1])
        if answer.get('missing') or answer.get('status') != 'running':
            raise ControlError('9router isn’t running')
        problem = answer.get('error') or (answer.get('errors') or [None])[0]
        if problem:
            raise ControlError(f'9router: {problem}')
        if action != 'reveal':
            threading.Thread(target=self._refresh_quietly, daemon=True).start()
        return {k: answer[k] for k in ('id', 'name', 'key') if k in answer}

    def _refresh_quietly(self):
        try:
            self.refresh()
        except Exception:  # noqa: BLE001 - the 60 s loop tries again
            pass

    def run(self, stop):
        while not stop.is_set():
            if self.pve.configured():
                try:
                    self.refresh()
                except (ControlError, ValueError, IndexError) as exc:
                    with self._lock:
                        self._error = str(exc)[:300]
                except Exception as exc:  # noqa: BLE001 - the loop must keep going
                    with self._lock:
                        self._error = f'{type(exc).__name__}: {str(exc)[:200]}'
            stop.wait(REFRESH_S)
