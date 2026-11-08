"""Read and change one agent's settings: container size, Claude Code, Telegram access, services and persona.

Values are read and written by small Python scripts run inside the container, with the new values on
stdin. Every file is backed up before it changes (name.bak-hq-<time>), only the service a change needs
is restarted, and nothing secret (tokens, logins) is ever read back.
"""
import json
import re

from .pve import ControlError

MODELS = ('', 'sonnet', 'opus', 'haiku', 'opusplan')
MODEL_ID = re.compile(r'claude-[a-z0-9.-]{3,60}(\[1m\])?')
POLICIES = ('allowlist', 'open', 'disabled')
SERVICE = re.compile(r'zylos-[a-z0-9-]{1,40}')
TZ = re.compile(r'[A-Za-z]+(/[A-Za-z0-9_+-]+){0,2}')
CHAT_ID = re.compile(r'-?\d{4,15}')
IDENTITY_MAX = 20000

READ = r'''
import json, os, subprocess, sys
# The agent's login comes from HQ's own records, never guessed from file owners.
login = json.load(sys.stdin)['login']
home = os.path.expanduser('~' + login)
out = {'login': login}
# Files the agent's own user can change are handled carefully, since root does the reading and writing:
# a path that is a link, or that resolves outside the agent's folders, is refused rather than followed.
def safe(path):
    real = os.path.realpath(path)
    if os.path.islink(path) or not any(real.startswith(root) for root in (os.path.realpath(home) + '/', '/opt/zylos/', '/etc/zylos/')):
        raise SystemExit(f'refused: {path} is a link or points outside the agent folders')
    return path
def read_text(path):
    fd = os.open(safe(path), os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, encoding='utf-8') as stream:
        return stream.read()
def load(path):
    try:
        return json.loads(read_text(path))
    except (Exception, SystemExit):
        return None
s = load(home + '/.claude/settings.json') or {}
env = s.get('env') or {}
out['claude'] = {'model': s.get('model') or '', 'auto_updates': env.get('DISABLE_AUTOUPDATER') not in ('1', 1, 'true', True)}
tg_path = next((p for p in (home + '/zylos/components/telegram/config.json', '/opt/zylos/components/telegram/config.json') if os.path.exists(p)), None)
tg = load(tg_path) if tg_path else None
if tg:
    owner = tg.get('owner') or {}
    out['telegram'] = {'enabled': bool(tg.get('enabled', True)), 'owner_chat_id': str(owner.get('chat_id') or ''), 'owner_username': owner.get('username') or '',
                       'dm_policy': tg.get('dmPolicy'), 'dm_allow': [str(x) for x in tg.get('dmAllowFrom') or []], 'group_policy': tg.get('groupPolicy'),
                       'groups': list((tg.get('groups') or {}).keys())}
units = subprocess.run(['systemctl', 'list-unit-files', 'zylos-*.service', '--no-legend', '--plain'], capture_output=True, text=True).stdout.split('\n')
services = []
for line in units:
    parts = line.split()
    if not parts:
        continue
    name = parts[0][:-len('.service')]
    active = subprocess.run(['systemctl', 'is-active', parts[0]], capture_output=True, text=True).stdout.strip()
    services.append({'name': name, 'enabled': parts[1] if len(parts) > 1 else '', 'active': active})
out['services'] = services
tz = ''
if os.path.exists('/etc/zylos/agent.env'):
    for line in open('/etc/zylos/agent.env'):
        if line.startswith('TZ='):
            tz = line.strip()[3:]
out['timezone'] = tz
try:
    out['identity'] = read_text('/opt/zylos/memory/identity.md')[:20000]
except (OSError, SystemExit):
    out['identity'] = None
print(json.dumps(out))
'''

WRITE = r'''
import json, os, pwd, subprocess, sys, time
change = json.load(sys.stdin)
login = change['login']
home = os.path.expanduser('~' + login)
stamp = time.strftime('%Y%m%d-%H%M%S')
done = []
# Files the agent's own user can change are handled carefully, since root does the reading and writing:
# a path that is a link, or that resolves outside the agent's folders, is refused rather than followed.
def safe(path):
    real = os.path.realpath(path)
    if os.path.islink(path) or not any(real.startswith(root) for root in (os.path.realpath(home) + '/', '/opt/zylos/', '/etc/zylos/')):
        raise SystemExit(f'refused: {path} is a link or points outside the agent folders')
    return path
def read_text(path):
    fd = os.open(safe(path), os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, encoding='utf-8') as stream:
        return stream.read()
def write_text(path, text, owner=None):
    """Write through a new temp file that can't be a planted link, keeping the old file's owner and mode."""
    safe(path)
    st = os.lstat(path) if os.path.lexists(path) else None
    tmp = path + '.hq-tmp'
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(text)
        uid, gid = owner or ((st.st_uid, st.st_gid) if st else (os.getuid(), os.getgid()))
        os.fchown(stream.fileno(), uid, gid)
        os.fchmod(stream.fileno(), (st.st_mode & 0o7777) if st else 0o644)
    os.replace(tmp, path)
def backup(path):
    if os.path.lexists(path):
        write_text(f'{path}.bak-hq-{stamp}', read_text(path), owner=(os.lstat(path).st_uid, os.lstat(path).st_gid))
def save_json(path, data):
    backup(path)
    write_text(path, json.dumps(data, indent=2))
section = change['section']
if section == 'claude':
    path = home + '/.claude/settings.json'
    s = json.loads(read_text(path)) if os.path.lexists(path) else {}
    if change['model']:
        s['model'] = change['model']
    else:
        s.pop('model', None)
    env = s.setdefault('env', {})
    if change['auto_updates']:
        env.pop('DISABLE_AUTOUPDATER', None)
    else:
        env['DISABLE_AUTOUPDATER'] = '1'
    save_json(path, s)
    done.append('Claude Code settings saved; the Claude session uses them from its next restart')
elif section == 'telegram':
    path = next(p for p in (home + '/zylos/components/telegram/config.json', '/opt/zylos/components/telegram/config.json') if os.path.exists(p))
    tg = json.loads(read_text(path))
    tg['enabled'] = change['enabled']
    owner = tg.setdefault('owner', {})
    owner['chat_id'], owner['username'] = change['owner_chat_id'] or None, change['owner_username'] or None
    tg['dmPolicy'], tg['groupPolicy'] = change['dm_policy'], change['group_policy']
    tg['dmAllowFrom'] = change['dm_allow']
    tg['groups'] = {k: v for k, v in (tg.get('groups') or {}).items() if k in change['groups']}
    save_json(path, tg)
    subprocess.run(['systemctl', 'restart', 'zylos-telegram'], check=False)
    done.append('Telegram settings saved and the Telegram service restarted')
elif section == 'service':
    unit = change['name'] + '.service'
    for verb in change['verbs']:
        subprocess.run(['systemctl', verb, unit], check=True, capture_output=True)
    done.append(f"{change['name']}: {', '.join(change['verbs'])}")
elif section == 'timezone':
    path = '/etc/zylos/agent.env'
    backup(path)
    lines = [l for l in read_text(path).splitlines() if not l.startswith('TZ=')] + [f"TZ={change['timezone']}"]
    write_text(path, '\n'.join(lines) + '\n')
    done.append('Time zone saved; services use it from their next restart')
elif section == 'identity':
    path = '/opt/zylos/memory/identity.md'
    backup(path)
    user = pwd.getpwnam(login)
    write_text(path, change['text'], owner=(user.pw_uid, user.pw_gid))
    subprocess.run(['runuser', '-u', login, '--', 'sh', '-c', 'cd /opt/zylos/memory && git add identity.md && git -c user.email=hq@example.invalid -c user.name=AgentHQ commit -qm "Persona edited in Agent HQ"'], capture_output=True)
    done.append('Persona saved; the agent reads it at its next session')
print(json.dumps({'done': done}))
'''


class Settings:
    def __init__(self, pve, log):
        self.pve, self.log = pve, log

    def read(self, host):
        vmid = self.pve.agent(host)['vmid']
        config = self.pve.run(['pct', 'config', vmid])
        get = lambda key: (re.search(rf'^{key}: (.+)$', config, re.M) or [None, None])[1]
        container = {'cores': int(get('cores') or 1), 'memory': int(get('memory') or 512), 'swap': int(get('swap') or 0), 'onboot': get('onboot') == '1'}
        login = self.pve.agent(host)['login']
        inside = json.loads(self.pve.run(['pct', 'exec', vmid, '--', 'python3', '-c', READ], timeout=60, stdin=json.dumps({'login': login})) or '{}')
        return {'container': container, **inside}

    def _inside(self, host, change):
        vmid = self.pve.agent(host)['vmid']
        out = self.pve.run(['pct', 'exec', vmid, '--', 'python3', '-c', WRITE], timeout=120, stdin=json.dumps({**change, 'login': self.pve.agent(host)['login']}))
        return json.loads(out.strip().splitlines()[-1]).get('done', [])

    def apply(self, host, section, values):
        """Validate one section's new values, apply them, and say what happened."""
        if section == 'container':
            cores, memory, swap, onboot = values.get('cores'), values.get('memory'), values.get('swap'), values.get('onboot')
            if not (isinstance(cores, int) and 1 <= cores <= 16 and isinstance(memory, int) and 512 <= memory <= 65536
                    and isinstance(swap, int) and 0 <= swap <= 16384 and isinstance(onboot, bool)):
                raise ValueError('cores 1 to 16, memory 512 to 65536 MB, swap up to 16384 MB')
            self.pve.run(['pct', 'set', self.pve.agent(host)['vmid'], '--cores', cores, '--memory', memory, '--swap', swap, '--onboot', int(onboot)])
            self.pve.forget()
            done = ['Container size saved; cores and memory apply straight away']
        elif section == 'claude':
            # Short names, or a full model ID such as claude-opus-5-5, which some agents already use.
            model = values.get('model')
            if not (model in MODELS or (isinstance(model, str) and MODEL_ID.fullmatch(model))) or not isinstance(values.get('auto_updates'), bool):
                raise ValueError('unknown model')
            done = self._inside(host, {'section': 'claude', 'model': values['model'], 'auto_updates': values['auto_updates']})
        elif section == 'telegram':
            allow = [str(x).strip() for x in values.get('dm_allow') or []]
            groups = [str(x) for x in values.get('groups') or []]
            chat = str(values.get('owner_chat_id') or '').strip()
            username = str(values.get('owner_username') or '').strip().lstrip('@')
            if (values.get('dm_policy') not in POLICIES or values.get('group_policy') not in POLICIES or not isinstance(values.get('enabled'), bool)
                    or any(not CHAT_ID.fullmatch(x) for x in allow) or len(allow) > 50 or (chat and not CHAT_ID.fullmatch(chat))
                    or (username and not re.fullmatch(r'[A-Za-z0-9_]{4,32}', username)) or any(not CHAT_ID.fullmatch(g) for g in groups)):
                raise ValueError('check the Telegram IDs: they are numbers, and usernames are 4 to 32 letters, numbers or _')
            done = self._inside(host, {'section': 'telegram', 'enabled': values['enabled'], 'owner_chat_id': chat, 'owner_username': username,
                                        'dm_policy': values['dm_policy'], 'group_policy': values['group_policy'], 'dm_allow': allow, 'groups': groups})
        elif section == 'service':
            name, action = values.get('name'), values.get('action')
            verbs = {'start': ['start'], 'stop': ['stop'], 'restart': ['restart'], 'enable': ['enable', 'start'], 'disable': ['stop', 'disable']}.get(action)
            if not isinstance(name, str) or not SERVICE.fullmatch(name) or not verbs:
                raise ValueError('unknown service or action')
            done = self._inside(host, {'section': 'service', 'name': name, 'verbs': verbs})
        elif section == 'timezone':
            if not isinstance(values.get('timezone'), str) or not TZ.fullmatch(values['timezone']):
                raise ValueError('use a time zone name such as Asia/Singapore')
            done = self._inside(host, {'section': 'timezone', 'timezone': values['timezone']})
        elif section == 'identity':
            text = values.get('text')
            if not isinstance(text, str) or not text.strip() or len(text) > IDENTITY_MAX:
                raise ValueError('the persona must be 1 to 20,000 characters')
            done = self._inside(host, {'section': 'identity', 'text': text})
        else:
            raise ValueError('unknown settings section')
        self.log.log({'action': 'settings_changed', 'host': host, 'section': section,
                      **({'service': values.get('name'), 'service_action': values.get('action')} if section == 'service' else {})})
        return done
