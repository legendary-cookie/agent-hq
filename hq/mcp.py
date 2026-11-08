"""An MCP endpoint so AI agents can use HQ as a tool server.

Agents connect over MCP's "Streamable HTTP" transport (JSON-RPC 2.0 in a POST to /mcp, JSON answers, no
event stream) with their own access key: `Authorization: Bearer hqa_...`. Keys are made and revoked in
Settings by a person who has passed the password check. Only a SHA-256 of each key is kept.

Every key can read: the fleet, one agent's status, health warnings, Proxmox capacity, update status and
9router's status (its accounts' limits included). Two powers are off unless a person ticks them for that key, because an agent can be talked
into things by what it reads: "message" (type a message into another agent's Claude session) and
"control" (restart an agent's Claude). Logins, message text, transcripts and passwords are never exposed.
Every call is logged in the control log (tool and target only, never message text).
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
SCOPES = {'message': 'Send messages to agents', 'control': 'Restart an agent’s Claude'}
KEY_PREFIX = 'hqa_'
NAME = re.compile(r'[A-Za-z0-9 _.()-]{1,60}')
RATE_PER_MIN = 120
MAX_KEYS = 100


class AgentKeys:
    """agent-keys.json in HQ's data folder (0600). Holds names, scopes, use counts and key hashes."""

    def __init__(self, path: Path, clock=time.time):
        self.path, self.clock = Path(path), clock
        self._lock = threading.Lock()
        self._calls = {}    # key id -> recent call times, for the rate limit
        self._unsaved = {}  # key id -> uses not yet written to the file

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return []
        return [k for k in data.get('keys', []) if isinstance(k, dict) and isinstance(k.get('hash'), str)] if isinstance(data, dict) else []

    def _save(self, keys):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + '.tmp')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump({'keys': keys}, out)
        os.replace(tmp, self.path)

    @staticmethod
    def _hash(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def public(self):
        with self._lock:
            return [{k: v for k, v in key.items() if k != 'hash'} for key in self._load()]

    def create(self, name, scopes):
        name = str(name or '').strip()
        if not NAME.fullmatch(name):
            raise ValueError('give the key a short name, such as the agent it is for')
        if not isinstance(scopes, list) or any(s not in SCOPES for s in scopes):
            raise ValueError('unknown permission')
        token = KEY_PREFIX + secrets.token_urlsafe(32)
        with self._lock:
            keys = self._load()
            if len(keys) >= MAX_KEYS:
                raise ValueError('too many keys; revoke some first')
            key = {'id': secrets.token_hex(6), 'name': name, 'scopes': sorted(set(scopes)), 'created_at': self.clock(),
                   'last_used_at': None, 'uses': 0, 'hash': self._hash(token)}
            keys.append(key)
            self._save(keys)
        return token, {k: v for k, v in key.items() if k != 'hash'}

    def revoke(self, key_id):
        with self._lock:
            keys = self._load()
            kept = [k for k in keys if k.get('id') != key_id]
            if len(kept) == len(keys):
                raise ValueError('no such key')
            self._save(kept)

    def check(self, header):
        """The key for an Authorization header, or None. Counts the use and applies the rate limit."""
        if not isinstance(header, str) or not header.startswith('Bearer ' + KEY_PREFIX):
            return None
        digest = self._hash(header[len('Bearer '):].strip())
        with self._lock:
            keys = self._load()
            key = next((k for k in keys if hmac.compare_digest(k['hash'], digest)), None)
            if not key:
                return None
            now = self.clock()
            recent = [t for t in self._calls.get(key['id'], []) if now - t < 60]
            if len(recent) >= RATE_PER_MIN:
                return {**key, 'limited': True}
            self._calls[key['id']] = recent + [now]
            self._unsaved[key['id']] = self._unsaved.get(key['id'], 0) + 1
            # Written at most once a minute, so a busy agent doesn't rewrite the file on every call.
            if not key.get('last_used_at') or now - key['last_used_at'] > 60:
                key['last_used_at'] = now
                key['uses'] = key.get('uses', 0) + self._unsaved.pop(key['id'])
                self._save(keys)
            return dict(key)


def _schema(props=None, required=()):
    return {'type': 'object', 'properties': props or {}, 'required': list(required), 'additionalProperties': False}


HOST = {'type': 'string', 'description': 'The agent’s host name, such as ct300-worker (see list_agents).'}
TOOLS = [
    {'name': 'list_agents', 'scope': None, 'description': 'Every agent in the fleet: label, owner, group, status (working, recent, idle, quiet, attention), last active time, and today’s messages, tasks and errors.',
     'inputSchema': _schema({'range': {'type': 'string', 'enum': ['today', 'week', 'month'], 'description': 'Period for the counts. Default today.'}})},
    {'name': 'get_agent', 'scope': None, 'description': 'One agent in detail: status and counts, its container (running, CPU, memory, disk, uptime, last backup), versions (Claude Code, Zylos, security updates) and any health warnings about it.',
     'inputSchema': _schema({'host': HOST}, ['host'])},
    {'name': 'health_warnings', 'scope': None, 'description': 'Problems HQ sees now: unreachable agents, usage limits, API errors, full disks or memory, offline Proxmox nodes, missing backups.',
     'inputSchema': _schema()},
    {'name': 'proxmox_capacity', 'scope': None, 'description': 'Proxmox host capacity: nodes, CPU and memory use, memory promised to guests, storage free, and roughly how many more agents fit.',
     'inputSchema': _schema()},
    {'name': 'update_status', 'scope': None, 'description': 'Versions on every agent and which are behind on Claude Code, Zylos or security updates.',
     'inputSchema': _schema()},
    {'name': 'router_status', 'scope': None, 'description': '9router (the AI router): up or down, version, routing strategy, each account’s status, weekly limit left and reset time, which account is used next, available combos and models, and usage over the last day.',
     'inputSchema': _schema()},
    {'name': 'send_message', 'scope': 'message', 'description': 'Type a one-line message into another agent’s Claude session, as if a person sent it there. Use sparingly; it is logged.',
     'inputSchema': _schema({'host': HOST, 'text': {'type': 'string', 'minLength': 1, 'maxLength': 2000}}, ['host', 'text'])},
    {'name': 'restart_claude', 'scope': 'control', 'description': 'Restart an agent’s Claude session (staff agents started by the Zylos runtime service only). Work in progress in that session stops. Logged.',
     'inputSchema': _schema({'host': HOST}, ['host'])},
]


class ToolError(Exception):
    """A problem to report to the agent as a tool result with isError, not as a protocol error."""


class McpServer:
    def __init__(self, keys: AgentKeys, providers, log, version='1.0'):
        """providers: callables fleet(range), infra(), watch(), updates(), router(), send(host, text), restart(host)."""
        self.keys, self.p, self.log, self.version = keys, providers, log, version

    def tools_for(self, key):
        scopes = set(key.get('scopes') or [])
        return [t for t in TOOLS if t['scope'] is None or t['scope'] in scopes]

    def handle(self, message, key):
        """One JSON-RPC message in, (status, answer or None) out."""
        if not isinstance(message, dict) or message.get('jsonrpc') != '2.0' or not isinstance(message.get('method'), str):
            return 400, _error(None, -32600, 'expected a JSON-RPC 2.0 request')
        method, mid, params = message['method'], message.get('id'), message.get('params') or {}
        if 'id' not in message:  # a notification, such as notifications/initialized
            return 202, None
        if not isinstance(params, dict):
            return 200, _error(mid, -32602, 'params must be an object')
        if method == 'initialize':
            asked = params.get('protocolVersion')
            return 200, _result(mid, {
                'protocolVersion': asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                'capabilities': {'tools': {'listChanged': False}},
                'serverInfo': {'name': 'agent-hq', 'title': 'Agent HQ', 'version': self.version},
                'instructions': 'Agent HQ watches the agent fleet. Use list_agents first to find host names. Read tools are safe; '
                                'send_message and restart_claude change other agents and are logged, so only use them when a person asked you to.',
            })
        if method == 'ping':
            return 200, _result(mid, {})
        if method == 'tools/list':
            return 200, _result(mid, {'tools': [{k: v for k, v in t.items() if k != 'scope'} for t in self.tools_for(key)]})
        if method == 'tools/call':
            name, args = params.get('name'), params.get('arguments') or {}
            tool = next((t for t in self.tools_for(key) if t['name'] == name), None)
            if not tool:
                return 200, _error(mid, -32602, f'unknown tool or not allowed for this key: {name}')
            if not isinstance(args, dict):
                return 200, _error(mid, -32602, 'arguments must be an object')
            entry = {'action': 'mcp_call', 'key': key.get('name'), 'tool': name}
            if isinstance(args.get('host'), str):
                entry['host'] = args['host'][:80]
            if name == 'send_message':
                args = {**args, 'sender': key.get('name')}
            try:
                data = getattr(self, 'tool_' + name)(**args)
            except ToolError as exc:
                self.log.log({**entry, 'error': str(exc)[:200]})
                return 200, _result(mid, {'content': [{'type': 'text', 'text': str(exc)}], 'isError': True})
            except TypeError:
                return 200, _error(mid, -32602, 'unexpected or missing arguments')
            if tool['scope'] or name in ('get_agent',):
                self.log.log(entry)  # reads of the whole fleet aren't logged one by one; actions and per-agent reads are
            return 200, _result(mid, {'content': [{'type': 'text', 'text': json.dumps(data, ensure_ascii=False)}], 'structuredContent': data, 'isError': False})
        return 200, _error(mid, -32601, f'unknown method {method}')

    # Tools
    def _agents(self, period='today'):
        return (self.p['fleet'](period) or {}).get('agents', [])

    def _known(self, host):
        if not isinstance(host, str) or not any(a.get('host') == host for a in self._agents()):
            raise ToolError(f'no agent called {host!r}; use list_agents for the host names')
        return host

    def tool_list_agents(self, range='today'):
        if range not in ('today', 'week', 'month'):
            raise ToolError('range is today, week or month')
        return {'range': range, 'agents': [{k: a.get(k) for k in ('host', 'label', 'owner', 'group', 'status', 'last_seen', 'messages_in', 'messages_out', 'agent_messages', 'tasks', 'errors')}
                                           for a in self._agents(range)]}

    def tool_get_agent(self, host):
        host = self._known(host)
        agent = next(a for a in self._agents() if a.get('host') == host)
        snap = self.p['infra']() or {}
        guest = next((g for g in snap.get('guests', []) if g.get('host') == host), None)
        versions = ((self.p['updates']() or {}).get('agents') or {}).get(host)
        warnings = [w for w in (self.p['watch']() or {}).get('warnings', []) if w.get('host') == host]
        return {
            'agent': {k: agent.get(k) for k in ('host', 'label', 'owner', 'group', 'status', 'last_seen', 'messages_in', 'messages_out', 'tasks', 'errors', 'tokens', 'value_usd')},
            'container': {k: guest.get(k) for k in ('vmid', 'node', 'status', 'uptime', 'cpu', 'cpus', 'mem', 'maxmem', 'disk', 'maxdisk', 'last_backup')} if guest else None,
            'versions': {k: versions.get(k) for k in ('claude', 'zylos', 'hermes', 'codex', 'security', 'reboot', 'claude_behind', 'zylos_behind', 'hermes_behind', 'codex_behind')} if versions else None,
            'warnings': [w.get('text') for w in warnings],
        }

    def tool_health_warnings(self):
        watch = self.p['watch']() or {}
        return {'checked_at': watch.get('checked_at'), 'warnings': [{k: w.get(k) for k in ('kind', 'host', 'text', 'since')} for w in watch.get('warnings', [])]}

    def tool_proxmox_capacity(self):
        snap = self.p['infra']()
        if not snap:
            raise ToolError('HQ has no Proxmox reading yet')
        return {'cluster': snap.get('cluster'), 'totals': snap.get('totals'),
                'nodes': [{k: n.get(k) for k in ('name', 'status', 'uptime', 'cpu', 'cpus', 'mem', 'maxmem', 'allocated_mem', 'free_mem', 'free_disk', 'room_for_agents')} for n in snap.get('nodes', [])],
                'storage': [{k: s.get(k) for k in ('id', 'node', 'shared', 'used', 'total', 'status')} for s in snap.get('storage', [])]}

    def tool_update_status(self):
        up = self.p['updates']() or {}
        return {'checked_at': up.get('checked_at'), 'latest': up.get('latest'), 'totals': up.get('totals'),
                'agents': {h: {k: v.get(k) for k in ('claude', 'zylos', 'hermes', 'codex', 'security', 'reboot', 'claude_behind', 'zylos_behind', 'hermes_behind', 'codex_behind')} for h, v in (up.get('agents') or {}).items()}}

    def tool_router_status(self):
        snap = (self.p.get('router') or (lambda: None))()
        if not snap:
            raise ToolError('HQ has no 9router reading yet')
        if not snap.get('found'):
            raise ToolError('HQ didn’t find a 9router container')
        settings = snap.get('settings') or {}
        return {'up': snap.get('up'), 'version': snap.get('version'), 'strategy': settings.get('fallbackStrategy'),
                'provider_strategies': settings.get('providerStrategies'), 'in_use': snap.get('in_use'),
                'accounts': [{k: c.get(k) for k in ('id', 'name', 'provider', 'active', 'test_status', 'rate_limited_until', 'limit', 'rotation_position', 'requests_24h', 'tokens_24h')}
                             for c in snap.get('connections') or []],
                'api_keys': [{k: x.get(k) for k in ('name', 'active', 'restricted', 'allow', 'vmid', 'requests_24h', 'requests_30d', 'tokens_30d', 'last_used_at')} for x in snap.get('api_keys') or []],
                'combos': snap.get('combos'), 'models': snap.get('models'), 'usage_30d': (snap.get('usage') or {}).get('total')}

    def tool_send_message(self, host, text, sender='an agent'):
        host = self._known(host)
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ToolError('the message must be 1 to 2000 characters')
        try:
            # Labelled, so the receiving agent never takes it for a message from a person.
            self.p['send'](host, f'[Message from {sender} via HQ, not from a person] {text}')
        except Exception as exc:  # noqa: BLE001 - reported to the agent, which can decide what to do
            raise ToolError(f'couldn’t send: {str(exc)[:200]}') from None
        return {'sent': True, 'host': host, 'chars': len(text)}

    def tool_restart_claude(self, host):
        host = self._known(host)
        try:
            self.p['restart'](host)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f'couldn’t restart: {str(exc)[:200]}') from None
        return {'restarted': True, 'host': host}


def _result(mid, result):
    return {'jsonrpc': '2.0', 'id': mid, 'result': result}


def _error(mid, code, text):
    return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': code, 'message': text}}
