"""Run fixed commands on the Proxmox host as root, over SSH with HQ's own key.

Only containers that belong to an agent in hosts.json can be touched, never the host's other guests,
and CT 500 never. Every argument is checked and quoted, so nothing typed in HQ becomes shell code.
"""
import json
import os
import re
import shlex
import subprocess
import threading
import time
from pathlib import Path

PVE_HOST = os.environ.get('HQ_PVE_HOST', '')
EXCLUDED = {500}
POWER = {'start': 'start', 'shutdown': 'shutdown', 'stop': 'stop', 'reboot': 'reboot'}
USER = re.compile(r'[a-z_][a-z0-9_-]{0,31}')
SESSION = re.compile(r'[A-Za-z0-9_.-]{1,60}')
TIMEOUTS = {'start': 120, 'shutdown': 150, 'stop': 60, 'reboot': 180}


class ControlError(Exception):
    pass


def vmid_of(host_name):
    match = re.match(r'ct(\d{3,4})-', host_name or '')
    return int(match.group(1)) if match else None


def login_of(ssh):
    user = (ssh or '').split('@')[0]
    return user if USER.fullmatch(user) else None


class Pve:
    def __init__(self, key: Path, known_hosts: Path, hosts, runner=subprocess.run, host=None):
        self.key, self.known_hosts, self.runner = Path(key), Path(known_hosts), runner
        self.host = PVE_HOST if host is None else host
        self.agents = {}
        for host in hosts:
            vmid = vmid_of(host.name)
            if vmid and vmid not in EXCLUDED and login_of(host.ssh):
                self.agents[host.name] = {'vmid': vmid, 'login': login_of(host.ssh)}
        self._cache = (0.0, None)
        self._lock = threading.Lock()

    def configured(self):
        return bool(self.host and re.fullmatch(r'[a-z_][a-z0-9_-]*@[A-Za-z0-9][A-Za-z0-9.:-]*', self.host)) and self.key.is_file() and self.known_hosts.is_file()

    def ssh_args(self, tty=False):
        return ['ssh', '-i', str(self.key), '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'ConnectTimeout=10',
                '-o', 'StrictHostKeyChecking=yes', '-o', f'UserKnownHostsFile={self.known_hosts}', '-o', 'ServerAliveInterval=30',
                *(['-tt'] if tty else []), self.host]

    def add_agent(self, host_name, ssh):
        """Start controlling a newly created agent. The dict is replaced rather than changed, so code
        reading the old one at the same moment is never disturbed."""
        vmid = vmid_of(host_name)
        if not vmid or vmid in EXCLUDED or not login_of(ssh):
            raise ControlError('HQ can only control the agents it watches')
        self.agents = {**self.agents, host_name: {'vmid': vmid, 'login': login_of(ssh)}}

    def agent(self, host_name):
        found = self.agents.get(host_name)
        if not found:
            raise ControlError('HQ can only control the agents it watches')
        return found

    def run(self, words, timeout=60, stdin=None):
        """Run one command on the host. Secrets go in stdin, never in the command line."""
        if not self.configured():
            raise ControlError('HQ has no Proxmox access yet')
        command = ' '.join(shlex.quote(str(w)) for w in words)
        try:
            done = self.runner(self.ssh_args() + [command], capture_output=True, text=True, timeout=timeout, input=stdin)
        except subprocess.TimeoutExpired:
            raise ControlError('the Proxmox host took too long to answer') from None
        if done.returncode != 0:
            raise ControlError((done.stderr or done.stdout or f'failed with code {done.returncode}').strip()[-300:])
        return done.stdout

    def in_agent(self, host_name, script, as_root=False, timeout=60, stdin=None):
        """Run a fixed script inside an agent's container, as its login user unless as_root."""
        found = self.agent(host_name)
        words = ['pct', 'exec', found['vmid'], '--']
        words += ['sh', '-c', script] if as_root else ['runuser', '-u', found['login'], '--', 'sh', '-c', script]
        return self.run(words, timeout, stdin=stdin)

    def python_as_agent(self, host_name, script, data, timeout=60):
        """Run a fixed Python script inside the container as the agent's own user, with JSON on stdin."""
        found = self.agent(host_name)
        return self.run(['pct', 'exec', found['vmid'], '--', 'runuser', '-u', found['login'], '--', 'python3', '-c', script], timeout, stdin=json.dumps(data))

    # Containers
    def containers(self):
        """Status of the agents' containers, cached for a few seconds."""
        with self._lock:
            at, value = self._cache
            if value is not None and time.time() - at < 8:
                return value
        rows = json.loads(self.run(['pvesh', 'get', '/cluster/resources', '--type', 'vm', '--output-format', 'json']) or '[]')
        wanted = {found['vmid']: name for name, found in self.agents.items()}
        value = {}
        for row in rows:
            if row.get('type') == 'lxc' and row.get('vmid') in wanted:
                value[wanted[row['vmid']]] = {'vmid': row['vmid'], 'status': row.get('status'), 'uptime': row.get('uptime'),
                                              'cpu': row.get('cpu'), 'cpus': row.get('maxcpu'), 'mem': row.get('mem'), 'maxmem': row.get('maxmem'),
                                              'disk': row.get('disk'), 'maxdisk': row.get('maxdisk'), 'name': row.get('name')}
        with self._lock:
            self._cache = (time.time(), value)
        return value

    def forget(self):
        with self._lock:
            self._cache = (0.0, None)

    def power(self, host_name, action):
        if action not in POWER:
            raise ControlError('unknown action')
        vmid = self.agent(host_name)['vmid']
        self.run(['pct', POWER[action], vmid], TIMEOUTS[action])
        self.forget()

    # Claude sessions
    def session_info(self, host_name):
        """The agent's Claude tmux session, and whether the Zylos runtime service can restart it."""
        # Ends with "true" so a missing tmux or runtime service is an answer, not a failure.
        out = self.in_agent(host_name, "tmux ls -F '#{session_name}' 2>/dev/null; echo '--'; systemctl cat zylos-claude-runtime >/dev/null 2>&1 && echo runtime; true")
        names, _, rest = out.partition('--')
        sessions = [s.strip() for s in names.splitlines() if SESSION.fullmatch(s.strip())]
        claude = next((s for s in sessions if s.startswith('claude')), sessions[0] if sessions else None)
        return {'session': claude, 'sessions': sessions, 'restartable': 'runtime' in rest}

    def restart_claude(self, host_name):
        if not self.session_info(host_name)['restartable']:
            raise ControlError('this agent isn’t started by the Zylos runtime service, so HQ doesn’t know how to restart only its Claude session. Restart the container instead')
        self.in_agent(host_name, 'systemctl restart zylos-claude-runtime', as_root=True, timeout=90)

    def send_text(self, host_name, text):
        """Type a message into the agent's Claude session and press Enter."""
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError('the message must be 1 to 4000 characters')
        session = self.session_info(host_name)['session']
        if not session:
            raise ControlError('this agent has no Claude session running')
        line = ' '.join(text.split())  # One line, so Enter is pressed once at the end.
        # "--" stops a message that starts with "-" being read as options, and tmux treats an argument
        # ending in ";" as a command separator, so a final ";" is written as "\;" to keep it.
        if line.endswith(';'):
            line = line[:-1] + '\\;'
        self.in_agent(host_name, f'tmux send-keys -t {shlex.quote(session)} -l -- {shlex.quote(line)} && tmux send-keys -t {shlex.quote(session)} Enter')

    def terminal_command(self, host_name, mode):
        """The ssh command for a live terminal: the Claude session (watch or control) or a root shell."""
        found = self.agent(host_name)
        if mode == 'root':
            inner = ['pct', 'enter', found['vmid']]
        else:
            session = self.session_info(host_name)['session']
            if not session:
                raise ControlError('this agent has no Claude session running')
            attach = ['tmux', 'attach', '-t', session] + (['-r'] if mode == 'watch' else [])
            inner = ['pct', 'exec', found['vmid'], '--', 'runuser', '-u', found['login'], '--', 'env', 'TERM=xterm-256color', *attach]
        return self.ssh_args(tty=True) + [' '.join(shlex.quote(str(w)) for w in inner)]
