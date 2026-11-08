"""User accounts, roles and permissions for HQ.

Each person signs in with a username and password. A user has one role; a role is a list of permissions
(which pages they can see and which actions they can take). "admin" is built in, has every permission and
can't be changed; "operator" and "viewer" are built in but editable; more roles can be added.

Files beside the database, all 0600: users.json (salted scrypt hashes only), roles.json, sessions.json
(hashes of session tokens). The old single-password login.json is moved over to the user "admin" the first
time, with the same password, and its open sessions keep working.

Changing a user's password, role or disabled flag ends that user's sessions (except, for one's own password
change, the session making it).
"""
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

from .auth import FIRST_LOCK_S, FREE_ATTEMPTS, MAX_LENGTH, MAX_LOCK_S, MIN_LENGTH, SESSION_S, _SCRYPT, _digest, _hash, Locked

MAX_SESSIONS_PER_USER = 20
USERNAME = re.compile(r'[a-z][a-z0-9._-]{1,31}')
ROLE_ID = re.compile(r'[a-z][a-z0-9-]{1,31}')

CATALOG = [
    ('view.overview', 'Pages', 'Overview and Office', 'The Overview page, alerts and the Office view.'),
    ('view.agents', 'Pages', 'Agents', 'The agent list and each agent’s details.'),
    ('view.transcripts', 'Pages', 'Transcripts', 'Read conversation transcripts and watch an agent’s live terminal.'),
    ('view.usage', 'Pages', 'Usage and Insights', 'Usage, costs, limits and the Insights page.'),
    ('view.router', 'Pages', 'Router', 'The 9router page: accounts, limits, usage and API key names.'),
    ('view.proxmox', 'Pages', 'Proxmox', 'Host, storage, containers and backups.'),
    ('view.updates', 'Pages', 'Updates', 'Tool versions on each agent and update history.'),
    ('view.memory', 'Pages', 'Memory', 'Read agents’ memory files.'),
    ('view.activity', 'Pages', 'Activity', 'The log of what people and HQ did.'),
    ('view.settings', 'Pages', 'Settings', 'See HQ settings, alert settings and HQ agent keys.'),
    ('agents.message', 'Actions', 'Message agents', 'Type a message into an agent’s Claude session.'),
    ('agents.control', 'Actions', 'Control agents', 'Start, stop and reboot containers, and restart Claude.'),
    ('agents.terminal', 'Actions', 'Use terminals', 'Type into an agent’s terminal, including as root.'),
    ('agents.create', 'Actions', 'Create and configure agents', 'Make new agents and change agents’ settings.'),
    ('memory.edit', 'Actions', 'Edit memory', 'Change agents’ memory files.'),
    ('updates.run', 'Actions', 'Run updates', 'Check, update and roll back tools, and change the update schedule.'),
    ('router.manage', 'Actions', 'Manage 9router keys', 'Create, copy, pause, resume, delete and assign 9router API keys.'),
    ('settings.manage', 'Actions', 'Change settings', 'Alerts, HQ agent keys, limits and HQ settings.'),
    ('goals.manage', 'Actions', 'Goals and runs', 'Start and stop goals and runs, and answer approvals.'),
    ('accounts.manage', 'Actions', 'Manage accounts', 'Add and change users and roles. Gives full control of HQ.'),
]
PERMISSIONS = [p[0] for p in CATALOG]
VIEWS = [p for p in PERMISSIONS if p.startswith('view.')]
BUILTIN_ROLES = {
    'admin': {'label': 'Admin', 'description': 'Everything, including accounts.', 'permissions': PERMISSIONS},
    'operator': {'label': 'Operator', 'description': 'Sees everything and runs the fleet, but can’t manage accounts or settings.',
                 'permissions': VIEWS + ['agents.message', 'agents.control', 'agents.terminal', 'memory.edit', 'updates.run', 'goals.manage']},
    'viewer': {'label': 'Viewer', 'description': 'Read only, without transcripts or memory.',
               'permissions': [v for v in VIEWS if v not in ('view.transcripts', 'view.memory', 'view.settings')]},
}


def required_permission(method, path, data=None, query=None):
    """The permission a request needs, or None when any signed-in user may make it."""
    data = data or {}
    query = query or {}
    if method == 'GET':
        if path in ('/api/me', '/api/brand', '/api/control', '/api/stream', '/api/health', '/api/settings'):
            return None
        if path == '/api/terminal':
            mode = (query.get('mode') or ['watch'])[0]
            return 'view.transcripts' if mode == 'watch' else 'agents.terminal'
        if path.startswith('/api/sessions/') or path == '/api/control/session':
            return 'view.transcripts'
        table = [('/api/accounts', 'accounts.manage'), ('/api/router', 'view.router'), ('/api/infra', 'view.proxmox'),
                 ('/api/updates', 'view.updates'), ('/api/memory', 'view.memory'), ('/api/activity', 'view.activity'),
                 ('/api/control/log', 'view.activity'), ('/api/agent-keys', 'view.settings'), ('/api/watch', 'view.settings'),
                 ('/api/agents/new', 'agents.create'), ('/api/agents/settings', 'view.agents'), ('/api/sessions', 'view.agents'),
                 ('/api/goals', 'view.agents'), ('/api/runs', 'view.agents'), ('/api/insights', 'view.usage'), ('/api/limits', 'view.usage'),
                 ('/api/timeline', 'view.usage'), ('/api/delegations', 'view.usage'), ('/api/recommend', 'view.usage')]
        for prefix, need in table:
            if path == prefix or path.startswith(prefix + '/'):
                return need
        return 'view.overview'  # fleet, overview, office, series, kpis, alerts ...
    # Writes
    if path in ('/api/login', '/api/logout', '/api/control/elevate', '/api/account/password'):
        return None
    if path.startswith('/api/control/'):
        return 'agents.message' if path == '/api/control/send' else 'agents.control'
    table = [('/api/accounts', 'accounts.manage'), ('/api/router', 'router.manage'), ('/api/updates', 'updates.run'),
             ('/api/memory', 'memory.edit'), ('/api/agents', 'agents.create'), ('/api/goals', 'goals.manage'),
             ('/api/runs', 'goals.manage'), ('/api/approvals', 'goals.manage')]
    for prefix, need in table:
        if path == prefix or path.startswith(prefix + '/'):
            return need
    return 'settings.manage'  # agent keys, alerts, limits, HQ settings, importer, anything new


def label_of(permission):
    return next((c[2] for c in CATALOG if c[0] == permission), permission)


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(data, stream, indent=1)
    os.replace(temp, path)


def _read(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


class Accounts:
    def __init__(self, folder: Path, clock=time.time):
        self.folder = Path(folder)
        self.clock = clock
        self.users_path, self.roles_path = self.folder / 'users.json', self.folder / 'roles.json'
        self.sessions_path = self.folder / 'sessions.json'
        self._lock = threading.RLock()
        self._hashing = threading.Lock()
        self._waiting = threading.BoundedSemaphore(4)
        self._failures = {}  # username -> (count, locked_until)
        self._users, self._roles, self._sessions, self._stamp = {}, {}, {}, None
        self._refresh()

    def _file_stamp(self):
        try:
            s = self.users_path.stat()
            return s.st_mtime_ns, s.st_size
        except OSError:
            return None

    def _refresh(self):
        """Pick up changes made outside this process (hq --set-password, or login.json set while running)."""
        with self._lock:
            self._migrate()
            stamp = self._file_stamp()
            if stamp == self._stamp:
                return
            self._users = _read(self.users_path, {})
            self._roles = _read(self.roles_path, {})
            saved = _read(self.sessions_path, {})
            now = self.clock()
            self._sessions = {}
            for key, value in saved.items():
                try:
                    if len(value) == 3 and float(value[0]) > now and value[1] in self._users:
                        self._sessions[str(key)] = (float(value[0]), str(value[1]), int(value[2]))
                except (TypeError, ValueError):
                    continue
            self._stamp = stamp

    # setup ------------------------------------------------------------------------------------------------
    def _migrate(self):
        """First start with accounts: login.json becomes user "admin" (same password); its sessions carry over."""
        old = self.folder / 'login.json'
        if self.users_path.exists() or not old.is_file():
            return
        record = _read(old, None)
        if not isinstance(record, dict) or not {'salt', 'hash'} <= set(record):
            return
        now = self.clock()
        users = {'admin': {'name': 'Admin', 'role': 'admin', 'disabled': False, 'created': now, 'version': 1,
                           'password': {k: record[k] for k in ('salt', 'hash', 'n', 'r', 'p') if k in record}}}
        _write(self.users_path, users)
        sessions = _read(self.sessions_path, {})
        moved = {}
        for key, value in sessions.items():
            try:
                if isinstance(value, list) and len(value) == 2 and float(value[0]) > now:
                    moved[key] = [float(value[0]), 'admin', 1]
            except (TypeError, ValueError):
                continue
        _write(self.sessions_path, moved)
        os.replace(old, old.with_name('login.json.pre-accounts'))

    def configured(self):
        self._refresh()
        with self._lock:
            return bool(self._users)

    def _save(self):
        _write(self.users_path, self._users)
        _write(self.roles_path, self._roles)
        self._stamp = self._file_stamp()

    def _save_sessions(self):
        try:
            _write(self.sessions_path, {k: list(v) for k, v in self._sessions.items()})
        except OSError:
            pass

    def roles(self):
        """Built-in roles (with any saved edits) plus custom ones. Admin always has every permission."""
        with self._lock:
            out = {}
            for rid, base in BUILTIN_ROLES.items():
                saved = self._roles.get(rid) or {}
                out[rid] = {'label': saved.get('label', base['label']), 'description': saved.get('description', base['description']),
                            'permissions': list(PERMISSIONS) if rid == 'admin' else [p for p in saved.get('permissions', base['permissions']) if p in PERMISSIONS],
                            'builtin': True, 'locked': rid == 'admin'}
            for rid, saved in self._roles.items():
                if rid not in BUILTIN_ROLES:
                    out[rid] = {'label': saved.get('label', rid), 'description': saved.get('description', ''),
                                'permissions': [p for p in saved.get('permissions', []) if p in PERMISSIONS], 'builtin': False, 'locked': False}
            return out

    def permissions_of(self, username):
        with self._lock:
            user = self._users.get(username)
            if not user or user.get('disabled'):
                return set()
            role = self.roles().get(user.get('role'))
            return set(role['permissions']) if role else set()

    # sign-in ----------------------------------------------------------------------------------------------
    def _matches(self, user, password):
        record = user.get('password') or {}
        try:
            params = {key: int(record[key]) for key in ('n', 'r', 'p')}
            return hmac.compare_digest(_hash(password, bytes.fromhex(record['salt']), params), bytes.fromhex(record['hash']))
        except (KeyError, ValueError):
            return False

    def _raise_if_locked(self, username):
        with self._lock:
            wait = self._failures.get(username, (0, 0.0))[1] - self.clock()
        if wait > 0:
            raise Locked(int(wait) + 1)

    def _verify(self, username, password):
        """Check a user's password, counting wrong answers towards that username's lockout. Raises Locked."""
        username = (username or '').strip().lower() if isinstance(username, str) else ''
        self._refresh()
        self._raise_if_locked(username)
        if not isinstance(password, str) or not 0 < len(password) <= MAX_LENGTH:
            return False
        with self._lock:
            user = self._users.get(username)
        if not self._waiting.acquire(blocking=False):
            raise Locked(1)
        try:
            with self._hashing:
                self._raise_if_locked(username)
                # Hash even for an unknown user, so timing doesn't tell which usernames exist.
                good = self._matches(user, password) if user else (_hash(password, b'0' * 16, _SCRYPT) and False)
        finally:
            self._waiting.release()
        with self._lock:
            if good and not user.get('disabled'):
                self._failures.pop(username, None)
                return True
            count = self._failures.get(username, (0, 0.0))[0] + 1
            until = self.clock() + min(MAX_LOCK_S, FIRST_LOCK_S * 2 ** (count - FREE_ATTEMPTS)) if count >= FREE_ATTEMPTS else 0.0
            self._failures[username] = (count, until)
            return False

    def sign_in(self, username, password):
        """A session token, or None. Raises Locked while that username is blocked."""
        username = (username or 'admin').strip().lower() if isinstance(username, str) else 'admin'
        if not self._verify(username, password):
            return None
        with self._lock:
            now = self.clock()
            self._sessions = {k: v for k, v in self._sessions.items() if v[0] > now}
            mine = sorted((v[0], k) for k, v in self._sessions.items() if v[1] == username)
            while len(mine) >= MAX_SESSIONS_PER_USER:
                self._sessions.pop(mine.pop(0)[1], None)
            token = secrets.token_urlsafe(32)
            user = self._users[username]
            self._sessions[_digest(token)] = (now + SESSION_S, username, int(user.get('version', 1)))
            user['last_sign_in'] = now
            self._save()
            self._save_sessions()
            return token

    def user_for(self, token):
        """The username a session token belongs to, or None."""
        if not isinstance(token, str) or not token:
            return None
        self._refresh()
        with self._lock:
            session = self._sessions.get(_digest(token))
            if not session or session[0] <= self.clock():
                return None
            user = self._users.get(session[1])
            if not user or user.get('disabled') or int(user.get('version', 1)) != session[2]:
                return None
            return session[1]

    def valid(self, token):
        return self.user_for(token) is not None

    def check(self, token, password):
        """Confirm the signed-in user's own password (for the password check before risky actions)."""
        username = self.user_for(token)
        return bool(username) and self._verify(username, password)

    def sign_out(self, token):
        if isinstance(token, str):
            with self._lock:
                if self._sessions.pop(_digest(token), None) is not None:
                    self._save_sessions()

    def _end_sessions(self, username, keep=None):
        keep_digest = _digest(keep) if keep else None
        self._sessions = {k: v for k, v in self._sessions.items() if v[1] != username or k == keep_digest}

    # people and roles -------------------------------------------------------------------------------------
    def me(self, username):
        with self._lock:
            user = self._users.get(username) or {}
            role = self.roles().get(user.get('role')) or {}
            return {'username': username, 'name': user.get('name') or username, 'role': user.get('role'),
                    'role_label': role.get('label'), 'permissions': sorted(role.get('permissions', [])), 'admin': user.get('role') == 'admin'}

    def listing(self, me=None):
        with self._lock:
            now = self.clock()
            roles = self.roles()
            users = [{'username': name, 'name': u.get('name') or name, 'role': u.get('role'), 'disabled': bool(u.get('disabled')),
                      'created': u.get('created'), 'last_sign_in': u.get('last_sign_in'), 'you': name == me,
                      'sessions': sum(1 for v in self._sessions.values() if v[1] == name and v[0] > now)}
                     for name, u in sorted(self._users.items())]
            return {'users': users,
                    'roles': [{'id': rid, **r, 'users': sum(1 for u in self._users.values() if u.get('role') == rid)} for rid, r in roles.items()],
                    'catalog': [{'id': c[0], 'group': c[1], 'label': c[2], 'description': c[3]} for c in CATALOG]}

    def _password_record(self, password):
        if not isinstance(password, str) or not MIN_LENGTH <= len(password) <= MAX_LENGTH:
            raise ValueError(f'the password must be at least {MIN_LENGTH} characters')
        salt = secrets.token_bytes(16)
        return {'salt': salt.hex(), 'hash': _hash(password, salt, _SCRYPT).hex(), **_SCRYPT}

    def _admins(self, users=None):
        users = self._users if users is None else users
        return [n for n, u in users.items() if u.get('role') == 'admin' and not u.get('disabled')]

    def set_password(self, username, password, keep=None):
        record = self._password_record(password)
        with self._lock:
            if username not in self._users:
                if self._users:
                    raise ValueError('no such user')
                self._users[username] = {'name': username.title(), 'role': 'admin', 'disabled': False, 'created': self.clock(), 'version': 0}
            user = self._users[username]
            user['password'] = record
            user['version'] = int(user.get('version', 1)) + 1
            if keep:  # one's own change: this session stays, under the new version
                d = _digest(keep)
                if d in self._sessions and self._sessions[d][1] == username:
                    self._sessions[d] = (self._sessions[d][0], username, user['version'])
            self._end_sessions(username, keep)
            self._save()
            self._save_sessions()

    def change_own_password(self, token, current, new):
        username = self.user_for(token)
        if not username:
            raise ValueError('sign in again')
        if not self._verify(username, current):
            raise ValueError('the current password is wrong')
        self.set_password(username, new, keep=token)

    def act(self, data, me):
        """Admin changes from the Accounts page. Raises ValueError with a message for the person."""
        action = data.get('action')
        with self._lock:
            roles = self.roles()
            if action == 'create_user':
                name = str(data.get('username') or '').strip().lower()
                if not USERNAME.fullmatch(name):
                    raise ValueError('usernames are 2-32 lowercase letters, digits, dots, dashes or underscores, starting with a letter')
                if name in self._users:
                    raise ValueError(f'{name} already exists')
                if data.get('role') not in roles:
                    raise ValueError('choose a role')
                record = self._password_record(data.get('password'))
                self._users[name] = {'name': str(data.get('name') or name).strip()[:60], 'role': data['role'], 'disabled': False,
                                     'created': self.clock(), 'version': 1, 'password': record}
                self._save()
                return {'ok': True, 'username': name}
            if action in ('update_user', 'set_password', 'delete_user'):
                name = str(data.get('username') or '')
                if name not in self._users:
                    raise ValueError('no such user')
                if action == 'set_password':
                    self.set_password(name, data.get('password'))
                    return {'ok': True}
                after = {k: dict(v) for k, v in self._users.items()}
                if action == 'delete_user':
                    if name == me:
                        raise ValueError('you can’t delete yourself')
                    after.pop(name)
                else:
                    user = after[name]
                    if 'name' in data:
                        user['name'] = str(data.get('name') or name).strip()[:60]
                    if 'role' in data:
                        if data['role'] not in roles:
                            raise ValueError('choose a role')
                        user['role'] = data['role']
                    if 'disabled' in data:
                        if name == me and data['disabled']:
                            raise ValueError('you can’t disable yourself')
                        user['disabled'] = bool(data['disabled'])
                    if user.get('role') != self._users[name].get('role') or user.get('disabled') != bool(self._users[name].get('disabled')):
                        user['version'] = int(user.get('version', 1)) + 1  # role or access changed: sign them in again
                if not self._admins(after):
                    raise ValueError('there has to be at least one active admin')
                self._users = after
                self._end_sessions(name) if action == 'delete_user' else None
                self._save()
                self._save_sessions()
                return {'ok': True}
            if action in ('create_role', 'update_role', 'delete_role'):
                if action == 'create_role':
                    label = str(data.get('label') or '').strip()[:40]
                    rid = re.sub(r'[^a-z0-9]+', '-', label.lower()).strip('-')[:32]
                    if not label or not ROLE_ID.fullmatch(rid or ''):
                        raise ValueError('give the role a name')
                    if rid in roles:
                        raise ValueError('a role with that name exists')
                    self._roles[rid] = {'label': label, 'description': str(data.get('description') or '')[:200],
                                        'permissions': self._clean(data.get('permissions'))}
                    self._save()
                    return {'ok': True, 'id': rid}
                rid = data.get('id')
                if rid not in roles:
                    raise ValueError('no such role')
                if rid == 'admin':
                    raise ValueError('the admin role can’t be changed')
                if action == 'delete_role':
                    if rid in BUILTIN_ROLES:
                        raise ValueError('built-in roles can’t be deleted')
                    if any(u.get('role') == rid for u in self._users.values()):
                        raise ValueError('move its users to another role first')
                    self._roles.pop(rid, None)
                else:
                    saved = dict(self._roles.get(rid) or {k: roles[rid][k] for k in ('label', 'description', 'permissions')})
                    if 'label' in data:
                        saved['label'] = str(data.get('label') or roles[rid]['label']).strip()[:40]
                    if 'description' in data:
                        saved['description'] = str(data.get('description') or '')[:200]
                    if 'permissions' in data:
                        saved['permissions'] = self._clean(data.get('permissions'))
                    self._roles[rid] = saved
                self._save()
                return {'ok': True}
        raise ValueError('unknown action')

    @staticmethod
    def prompt(folder, username='admin'):
        """hq --set-password [--user NAME]: set a password from a terminal. Nothing is echoed or logged.
        On a fresh install this creates the first admin."""
        import getpass
        first = getpass.getpass(f'New password for {username}: ')
        if first != getpass.getpass('Repeat password: '):
            print('The passwords do not match. Nothing was changed.')
            return 1
        try:
            Accounts(folder).set_password(username, first)
        except ValueError as exc:
            print(f'{exc}. Nothing was changed.')
            return 1
        print(f'Password saved for {username}. Their open sessions have been signed out.')
        return 0

    @staticmethod
    def _clean(permissions):
        if not isinstance(permissions, list) or not all(isinstance(p, str) for p in permissions):
            raise ValueError('permissions must be a list')
        unknown = [p for p in permissions if p not in PERMISSIONS]
        if unknown:
            raise ValueError(f'unknown permission: {unknown[0]}')
        return [p for p in PERMISSIONS if p in permissions]
