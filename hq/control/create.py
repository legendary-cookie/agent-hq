"""Create a new agent by cloning an existing one and wiping the copy before it can reach anything.

This follows the recipe used for CT 309-313 (wipe309.sh .. wipe313.sh on the Proxmox host), made safer:
the clone first boots with its network unplugged (link_down), so the copied Telegram bot, HXA identity
and logins can't connect, and the wipe runs inside the container, where its symlinks point to the
right place. Only after the new identity is written is the network plugged in.
"""
import ipaddress
import json
import os
import re
import threading
import time
import uuid

from .pve import USER, ControlError

NAME = re.compile(r'[a-z][a-z0-9-]{1,24}')
LABEL = re.compile(r"[\w .'-]{1,40}", re.U)
BOT_TOKEN = re.compile(r'\d{6,12}:[A-Za-z0-9_-]{30,50}')
GATEWAY = os.environ.get('HQ_AGENT_GATEWAY', '192.0.2.1')
SUBNET = ipaddress.ip_network(os.environ.get('HQ_AGENT_SUBNET', '192.0.2.0/24'))
IP_RANGE = (60, 99)  # where staff agents live (see docs/ip-map.md)
GROUPS = ('Staff', 'Management', 'Department')
SOURCE_SNAPSHOT = 'hq-clone-source'
pause = time.sleep  # waits for a container to boot; tests replace it

# Runs inside the new container as root while its network is unplugged. Paths are relative to the
# container, so the links from /home/zylos/zylos into /opt/zylos resolve inside it, never the host.
WIPE = r'''
set -u
for unit in zylos-telegram zylos-hxa-connect zylos-claude-runtime zylos-scheduler zylos-c4-dispatcher zylos-activity-monitor zylos-creds-puller; do
  systemctl stop "$unit" 2>/dev/null
done
empty() { [ -d "$1" ] && find "$1" -mindepth 1 -xdev -delete 2>/dev/null; true; }
# The agent's login comes from HQ's records (passed as $1), never guessed from who owns /opt/zylos.
login="$1"
home=$(getent passwd "$login" | cut -d: -f6)
[ -n "$home" ] && [ -d "$home" ] || { echo "no home folder for $login"; exit 3; }
Z=/opt/zylos
# Conversations, schedules, logs and searchable memory copied from the source agent
for d in comm-bridge scheduler logs memory-search activity-monitor web-console/media media; do empty "$Z/$d"; done
for d in comm-bridge scheduler logs memory-search activity-monitor; do [ -d "$home/zylos/$d" ] && [ ! -L "$home/zylos/$d" ] && empty "$home/zylos/$d"; done
# Memory: keep company knowledge, drop everything about the source agent and its people
M=$Z/memory
rm -rf "$M/sessions" "$M/users" "$M/archive" "$M/.git" "$M"/identity.md* "$M/state.md" "$M/reference/decisions.md" "$M/reference/ideas.md"
mkdir -p "$M/sessions" "$M/users"
# Telegram and HXA: logs, media, backups, and the HXA identity (a new one is issued later)
for c in telegram whatsapp hxa-connect; do
  for d in logs media data sessions; do empty "$Z/components/$c/$d"; empty "$home/zylos/components/$c/$d"; done
  rm -f "$Z/components/$c"/config.json.bak* "$home/zylos/components/$c"/config.json.bak*
done
rm -f "$Z/components/hxa-connect/config.json" "$home/zylos/components/hxa-connect/config.json"
systemctl disable zylos-hxa-connect 2>/dev/null
# Old secrets backups, Claude transcripts and history, and the copied login (the puller fetches its own)
rm -f "$Z"/.env.bak* /etc/zylos/agent.env.bak*
rm -rf "$home/.claude/projects" "$home/.claude/todos" "$home/.claude/shell-snapshots" "$home/.claude/statsig" "$home/.claude/hq-limits.json"
rm -f "$home/.claude/.credentials.json" "$home/.claude.json.backup"
# Fresh SSH host keys and machine id, so the copy isn't mistaken for the source
rm -f /etc/ssh/ssh_host_* && ssh-keygen -A >/dev/null 2>&1
rm -f /etc/machine-id && systemd-machine-id-setup >/dev/null 2>&1
# Stop here, before the network is ever plugged in, if the copied login or transcripts survived.
for left in "$home/.claude/.credentials.json" "$home/.claude/projects"; do
  [ -e "$left" ] && { echo "still there after the wipe: $left"; exit 3; }
done
echo WIPED "$login"
'''

# Also inside the container, with the settings JSON on stdin so the bot token never appears in a command line.
IDENTITY = r'''
import json, os, re, sys
cfg = json.load(sys.stdin)
login = cfg['login']  # from HQ's records
home = os.path.expanduser('~' + login)

def set_env(path, key, value):
    if not os.path.exists(path):
        return
    lines = [l for l in open(path).read().splitlines() if not l.startswith(key + '=')]
    lines.append(f'{key}={value}')
    open(path, 'w').write('\n'.join(lines) + '\n')

for path in ('/etc/zylos/agent.env', '/opt/zylos/.env'):
    set_env(path, 'TELEGRAM_BOT_TOKEN', cfg['bot_token'])
    if path == '/opt/zylos/.env':
        lines = [l for l in open(path).read().splitlines() if not l.startswith('WEB_PASSWORD=')] if os.path.exists(path) else []
        if lines:
            open(path, 'w').write('\n'.join(lines) + '\n')

for base in ('/opt/zylos', home + '/zylos'):
    tg = base + '/components/telegram/config.json'
    if os.path.exists(tg) and not os.path.islink(tg):
        data = json.load(open(tg))
        data['owner'] = {'chat_id': cfg['owner_chat_id'], 'username': cfg['owner_username'], 'note': f"{cfg['owner']} (set by Agent HQ)"}
        data['dmAllowFrom'] = [cfg['owner_chat_id']] if cfg['owner_chat_id'] else []
        data['groups'] = {}
        json.dump(data, open(tg, 'w'), indent=2)

identity = f"""# Identity

I am {cfg['label']}, the AI assistant of {cfg['owner']}.

## Role
{cfg['role'] or 'General work assistant for ' + cfg['owner'] + '.'}

## Owner
- {cfg['owner']}{' (Telegram @' + cfg['owner_username'] + ')' if cfg['owner_username'] else ''}

This agent was created by Agent HQ from a copy of another agent. Its memory about people and past work was cleared, and company knowledge was kept.
"""
open('/opt/zylos/memory/identity.md', 'w').write(identity)
open('/opt/zylos/memory/state.md', 'w').write('# State\n\nNew agent. No work yet.\n')
os.system(f'chown -R {login}:{login} /opt/zylos/memory')
# A fresh history for the new memory; the copied one held the source agent's past.
os.system(f"runuser -u {login} -- sh -c 'cd /opt/zylos/memory && git init -q && git add -A && git -c user.email=hq@example.invalid -c user.name=AgentHQ commit -qm \"New agent created by Agent HQ\"' >/dev/null 2>&1")
print('IDENTITY', login)
'''


def used_ips(configs):
    return {match.group(1) for config in configs for match in re.finditer(r'ip=(\d+\.\d+\.\d+\.\d+)', config)}


def free_ips(configs):
    used = used_ips(configs)
    return [str(SUBNET.network_address + n) for n in range(IP_RANGE[0], IP_RANGE[1] + 1) if str(SUBNET.network_address + n) not in used]


class Creator:
    def __init__(self, pve, log, register, clock=time.time):
        self.pve, self.log, self.register, self.clock = pve, log, register, clock
        self.jobs = {}
        self._lock = threading.Lock()

    def suggest(self):
        """The next free container id and IP address, from the host's own records."""
        nextid = int(self.pve.run(['pvesh', 'get', '/cluster/nextid']).strip() or 0)
        configs = self.pve.run(['sh', '-c', 'cat /etc/pve/lxc/*.conf /etc/pve/qemu-server/*.conf 2>/dev/null'])
        ids = [int(m) for m in re.findall(r'^ct(\d+)-', '\n'.join(self.pve.agents), re.M)]
        staff_next = max([i for i in ids if 300 <= i < 400] + [299]) + 1
        vmid = staff_next if staff_next >= nextid or not self._exists(staff_next) else nextid
        while vmid == 500 or (vmid != staff_next and self._exists(vmid)):  # CT 500 is never used by fleet tooling
            vmid += 1
        ips = free_ips([configs])
        return {'vmid': vmid, 'ip': ips[0] if ips else None, 'ips': ips[:10], 'sources': sorted(self.pve.agents)}

    def _exists(self, vmid):
        try:
            self.pve.run(['pct', 'status', vmid])
            return True
        except ControlError:
            return False

    def validate(self, form):
        name, label, owner = form.get('name'), form.get('label'), form.get('owner')
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError('the short name must be 2 to 25 lowercase letters, numbers or dashes, starting with a letter')
        if not isinstance(label, str) or not LABEL.fullmatch(label) or not isinstance(owner, str) or not LABEL.fullmatch(owner):
            raise ValueError('the display name and owner must be 1 to 40 letters, numbers or spaces')
        vmid = form.get('vmid')
        if not isinstance(vmid, int) or not 100 <= vmid <= 999 or vmid == 500:
            raise ValueError('choose a container number from 100 to 999')
        if self._exists(vmid):
            raise ValueError(f'container {vmid} already exists')
        try:
            ip = ipaddress.ip_address(form.get('ip'))
        except ValueError:
            raise ValueError('enter a valid IP address') from None
        if ip not in SUBNET or str(ip) in (GATEWAY, str(SUBNET.network_address), str(SUBNET.broadcast_address)):
            raise ValueError(f'the IP must be in {SUBNET}')
        if str(ip) in used_ips([self.pve.run(['sh', '-c', 'cat /etc/pve/lxc/*.conf /etc/pve/qemu-server/*.conf 2>/dev/null'])]):
            raise ValueError(f'{ip} is already used by another container')
        source = form.get('source')
        self.pve.agent(source)
        if not BOT_TOKEN.fullmatch(form.get('bot_token') or ''):
            raise ValueError('paste the new bot’s token from BotFather (it looks like 123456789:ABC…)')
        chat_id = form.get('owner_chat_id')
        if chat_id not in (None, '') and not re.fullmatch(r'-?\d{4,15}', str(chat_id)):
            raise ValueError('the owner’s Telegram ID is a number')
        username = (form.get('owner_username') or '').lstrip('@')
        if username and not re.fullmatch(r'[A-Za-z0-9_]{4,32}', username):
            raise ValueError('that Telegram username doesn’t look right')
        group = form.get('group') if form.get('group') in GROUPS else 'Staff'
        role = str(form.get('role') or '')[:600]
        return {'name': name, 'label': label.strip(), 'owner': owner.strip(), 'vmid': vmid, 'ip': str(ip), 'source': source, 'group': group,
                'bot_token': form['bot_token'], 'owner_chat_id': str(chat_id) if chat_id not in (None, '') else None, 'owner_username': username or None, 'role': role}

    def start(self, form):
        plan = self.validate(form)
        with self._lock:
            if any(j['state'] == 'running' for j in self.jobs.values()):
                raise ControlError('another agent is being created; wait for it to finish')
            job_id = uuid.uuid4().hex[:10]
            self.jobs[job_id] = {'id': job_id, 'name': plan['name'], 'label': plan['label'], 'vmid': plan['vmid'], 'state': 'running', 'steps': [], 'error': None,
                                 'started_at': self.clock(), 'ended_at': None, 'host': f"ct{plan['vmid']}-{plan['name']}"}
        threading.Thread(target=self._run, args=(job_id, plan), daemon=True).start()
        return job_id

    def _step(self, job, text):
        job['steps'].append({'at': self.clock(), 'text': text})

    def _run(self, job_id, plan):
        job = self.jobs[job_id]
        src = self.pve.agent(plan['source'])
        vmid, hostname, pve_name = plan['vmid'], f"ct{plan['vmid']}-{plan['name']}", f"{plan['name']}-ai-agent"
        created = online = False
        self.log.log({'action': 'create_started', 'host': hostname, 'source': plan['source'], 'ip': plan['ip']})
        try:
            self._step(job, f'Copying {plan["source"]} into CT {vmid}')
            self.pve.run(['pct', 'snapshot', src['vmid'], SOURCE_SNAPSHOT, '--description', 'Temporary, for HQ to copy this agent'], timeout=300)
            try:
                self.pve.run(['pct', 'clone', src['vmid'], vmid, '--snapname', SOURCE_SNAPSHOT, '--full', '1', '--hostname', pve_name, '--storage', os.environ.get('HQ_AGENT_STORAGE', 'local-lvm')], timeout=1800)
                created = True
            finally:
                self.pve.run(['pct', 'delsnapshot', src['vmid'], SOURCE_SNAPSHOT], timeout=300)
            config = self.pve.run(['pct', 'config', vmid])
            net = re.search(r'^net0: (.+)$', config, re.M)
            if not net:
                raise ControlError('the copy has no network setting')
            # Same bridge and settings as the source, a new address, a new MAC, and the link unplugged.
            parts = [p for p in net.group(1).split(',') if not p.startswith(('ip=', 'hwaddr=', 'gw=', 'link_down='))]
            self.pve.run(['pct', 'set', vmid, '--net0', ','.join(parts + [f'ip={plan["ip"]}/{SUBNET.prefixlen}', f'gw={GATEWAY}', 'link_down=1']), '--onboot', '0', '--tags', 'staff;prod;hq-created'])
            self._step(job, 'Starting it with the network unplugged')
            self.pve.run(['pct', 'start', vmid], timeout=180)
            pause(8)
            self._step(job, 'Wiping the copied memory, conversations, logins and identities')
            login = src['login']
            if not USER.fullmatch(login):
                raise ControlError('the source agent’s login name looks wrong')
            out = self.pve.run(['pct', 'exec', vmid, '--', 'sh', '-c', WIPE, 'wipe', login], timeout=600)
            if f'WIPED {login}' not in out:
                raise ControlError('the wipe didn’t finish, so the copy was kept offline')
            self._step(job, 'Writing the new identity and Telegram settings')
            self.pve.run(['pct', 'exec', vmid, '--', 'python3', '-c', IDENTITY], timeout=120, stdin=json.dumps({**plan, 'login': login}))
            self._step(job, 'Plugging in the network and restarting')
            self.pve.run(['pct', 'set', vmid, '--net0', ','.join(parts + [f'ip={plan["ip"]}/{SUBNET.prefixlen}', f'gw={GATEWAY}']), '--onboot', '1'])
            online = True
            self.pve.run(['pct', 'reboot', vmid], timeout=240)
            pause(10)
            host_key = self.pve.run(['pct', 'exec', vmid, '--', 'cat', '/etc/ssh/ssh_host_ed25519_key.pub']).split()[:2]
            # Claude starts only once the agent's own login has arrived from the fleet manager.
            self._step(job, 'Waiting for its login, then starting Claude')
            self.pve.run(['pct', 'exec', vmid, '--', 'sh', '-c',
                          f'h=$(getent passwd {login} | cut -d: -f6); for i in $(seq 1 30); do [ -s "$h/.claude/.credentials.json" ] && break; sleep 5; done; '
                          'systemctl restart zylos-claude-runtime 2>/dev/null; true'], timeout=240)
            self._step(job, 'Adding it to HQ')
            self.register({'name': hostname, 'ssh': f'{login}@{plan["ip"]}', 'ip': plan['ip'], 'host_key': ' '.join(host_key), 'label': plan['label'], 'owner': plan['owner'],
                           'group': plan['group'], 'source': plan['source']})
            job['state'] = 'done'
            self._step(job, f'{plan["label"]} is running as {hostname}. Send its bot a message on Telegram to check it answers. HXA Connect stays off until it is issued its own identity')
            self.log.log({'action': 'create_done', 'host': hostname})
        except Exception as exc:  # noqa: BLE001 - any failure must end the job, or every later create is blocked
            job['state'], job['error'] = 'failed', (str(exc) or type(exc).__name__)[:400]
            if online:
                # The wipe and new identity are done, so it's left running rather than stopped half-way.
                leftover = f' CT {vmid} was wiped and is online, but HQ isn’t watching it yet. Check it in Proxmox, then add it to hosts.json or delete it.'
            elif created:
                leftover = f' CT {vmid} was left stopped with its network unplugged, so you can inspect it or delete it in Proxmox.'
            else:
                leftover = ' Nothing was created.'
            if created and not online:
                try:
                    self.pve.run(['pct', 'stop', vmid], timeout=120)
                except ControlError:
                    pass
            self._step(job, 'Failed: ' + job['error'] + '.' + leftover)
            self.log.log({'action': 'create_failed', 'host': hostname, 'error': job['error']})
        finally:
            job['ended_at'] = self.clock()

    def job_list(self):
        with self._lock:
            return sorted((json.loads(json.dumps(j)) for j in self.jobs.values()), key=lambda j: j['started_at'], reverse=True)[:10]
