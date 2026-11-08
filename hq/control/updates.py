"""Versions, the update watchdog, and updates for agent containers.

The audit is the read-only check from deploy/fleet-updates/fleet-audit.sh, run inside each agent's
container. The watchdog repeats it on the schedule set on the Updates page (every 12 hours unless changed),
and an agent is checked again as soon as an update on it ends. An update takes a Proxmox snapshot first,
unless snapshots are switched off, and stops before changing anything if the snapshot fails. Snapshots can
be deleted automatically once the agent has passed its checks, either straight away or after a wait.
"""
import json
import re
import shlex
import threading
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from .pve import ControlError

THIN_LIMIT = 0.88  # don't snapshot onto a thin pool fuller than this
VERSION = re.compile(r'\d+\.\d+\.\d+([.-][0-9A-Za-z.]+)?')
SNAPSHOT = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,39}')
# npm prefixes an agent's tools may be installed under: system-wide, Hermes's Node on 219, or a user's own
# npm folder or nvm Node. Anything else from the audit is ignored, so a container can't steer the install
# command somewhere else. A prefix under a home folder is installed as that user.
PREFIXES = ('/usr', '/usr/local', '/root/.hermes/node')
USER_PREFIX = re.compile(r'(/root|/home/[a-z_][a-z0-9_-]{0,31})/(\.local|\.npm-global|\.nvm/versions/node/v\d+\.\d+\.\d+)')
HOME = re.compile(r'/root|/home/[a-z_][a-z0-9_-]{0,31}')
TOOLS = ('claude', 'zylos', 'hermes', 'codex')  # what the audit looks for, and what can be updated
KINDS = TOOLS + ('security',)
NAMES = {'claude': 'Claude Code', 'zylos': 'Zylos', 'hermes': 'Hermes', 'codex': 'Codex', 'security': 'Security updates'}
NPM = {'claude': '@anthropic-ai/claude-code', 'codex': '@openai/codex'}

DEFAULT_SETTINGS = {
    'snapshot': True,            # take a snapshot before each update
    'cleanup': 'keep',           # keep | after_check | after_hours
    'cleanup_hours': 24,         # with after_hours: how long the agent has to stay healthy first
    'schedule': {'mode': 'every', 'hours': 12, 'time': '03:00', 'day': 0},  # every | daily | weekly; day 0 = Monday
}

# From fleet-audit.sh, without the platform-service part. Prints key=value pairs on one line.
CHECK = r'''
export PATH=/usr/local/sbin:/usr/local/bin:$PATH
out() { printf '%s=%s ' "$1" "$2"; }
out debian "$(cat /etc/debian_version 2>/dev/null || echo unknown)"
newest=$(find /var/lib/apt/lists -maxdepth 1 -type f -printf '%T@\n' 2>/dev/null | sort -n | tail -n 1 | cut -d. -f1)
if [ -n "$newest" ]; then out list_days "$(( ($(date +%s) - newest) / 86400 ))"; else out list_days none; fi
sim=$(apt-get -s -o Debug::NoLocking=1 dist-upgrade 2>/dev/null | grep '^Inst ')
out pending "$(printf '%s' "$sim" | grep -c '^Inst')"
out security "$(printf '%s' "$sim" | grep -ci 'security')"
if [ -f /var/run/reboot-required ]; then out reboot yes; else out reboot no; fi
command -v node >/dev/null 2>&1 && out node "$(node --version 2>/dev/null | tr -d v)"
ver() { sed -n 's/^ *"version": *"\([^"]*\)".*/\1/p' "$1" | head -n 1; }
# Versions are read from package files rather than by running the tools, which can start updates of their own.
# Packages from npm: system-wide first, then each user's own npm folder or nvm Node.
npm_find() {
  for prefix in /usr /usr/local /root/.hermes/node /root/.local /root/.npm-global /root/.nvm/versions/node/v* \
                /home/*/.local /home/*/.npm-global /home/*/.nvm/versions/node/v*; do
    pkg=$prefix/lib/node_modules/$1/package.json
    [ -f "$pkg" ] && { out "$2" "$(ver "$pkg")"; out "$2_prefix" "$prefix"; return 0; }
  done
  return 1
}
# Claude Code's native install (~/.local/bin/claude -> ~/.local/share/claude/versions/<version>) comes first,
# because that's the claude its user runs.
native=
for home in /root /home/*; do
  link=$(readlink -f "$home/.local/bin/claude" 2>/dev/null)
  case "$link" in
    "$home"/.local/share/claude/versions/*) out claude "$(basename "$link")"; out claude_home "$home"; native=1; break ;;
  esac
done
[ -n "$native" ] || npm_find @anthropic-ai/claude-code claude
npm_find @openai/codex codex
npm_find zylos zylos || { command -v zylos >/dev/null 2>&1 && out zylos "$(zylos --version 2>/dev/null | head -n 1)"; }
# Hermes: newer versions say 0.0.0 in pyproject.toml and take their version from git tags.
if [ -f /usr/local/lib/hermes-agent/pyproject.toml ]; then
  hv=$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' /usr/local/lib/hermes-agent/pyproject.toml | head -n 1)
  if [ -z "$hv" ] || [ "$hv" = 0.0.0 ]; then
    hv=$(git -C /usr/local/lib/hermes-agent describe --tags 2>/dev/null | sed 's/^v//; s/[+-].*//')
  fi
  out hermes "$hv"
fi
for home in /root /home/*; do
  for file in "$home/.claude/settings.json" "$home/start-claude.sh"; do
    [ -f "$file" ] && grep -qE 'DISABLE_(AUTOUPDATER|UPDATES)' "$file" && out updates_held yes
  done
done
echo
'''

# Hermes updates itself from git ("hermes update"). Its code belongs to root; the config it may migrate is
# the one the agent uses, so files there keep their owner.
HERMES_UPDATE = r'''
# pct exec gives a short PATH without /usr/local/bin, where uv is. Without uv, hermes update falls back to
# pip, which its virtualenv doesn't have, and fails after pulling the new code.
export PATH=/usr/local/sbin:/usr/local/bin:$PATH
home=/root/.hermes
for candidate in /home/*/.hermes; do
  [ -d "$candidate" ] && { home="$candidate"; break; }
done
owner=$(stat -c %U:%G "$home" 2>/dev/null || echo root:root)
export PATH=$PATH:$home/node/bin  # on 219, Node and npm live with Hermes
cd /usr/local/lib/hermes-agent

# What newer Hermes needs that Debian 12 agents don't have (found on AgentOps and Customer Insights, 2026-11-06):
# 1. Build tools: some Python packages are compiled (libolm for the Matrix extra needs make, a C compiler, cmake).
need=
for tool in make gcc g++ cmake; do command -v $tool >/dev/null 2>&1 || need=1; done
if [ -n "$need" ]; then
  export DEBIAN_FRONTEND=noninteractive
  { apt-get update -qq && apt-get install -y -qq --no-install-recommends build-essential cmake; } >/tmp/hq-hermes-apt.log 2>&1 \
    || { tail -n 10 /tmp/hq-hermes-apt.log; echo "Couldn't install the build tools Hermes needs"; exit 1; }
fi
# 2. pnpm: Hermes's Node parts refuse npm.
if ! command -v pnpm >/dev/null 2>&1; then
  command -v npm >/dev/null 2>&1 || { echo "Hermes needs pnpm and there's no npm to install it with"; exit 1; }
  npm install -g --no-fund --no-audit pnpm@10 >/tmp/hq-hermes-pnpm.log 2>&1 || { tail -n 10 /tmp/hq-hermes-pnpm.log; exit 1; }
fi
# 3. The checkout belongs to root; without this, git refuses it for the agent's user and Hermes reports "vunknown".
git config --system --get-all safe.directory 2>/dev/null | grep -qx /usr/local/lib/hermes-agent \
  || git config --system --add safe.directory /usr/local/lib/hermes-agent
# A failed npm attempt leaves an untracked package-lock.json, which marks the checkout "dirty". Hermes uses pnpm.
[ -f pnpm-lock.yaml ] && ! git ls-files --error-unmatch package-lock.json >/dev/null 2>&1 && rm -f package-lock.json
# 4. Python 3.11.4 or later for Hermes's package manager (tarfile's filter argument); Debian 12 has 3.11.2. Move
# Hermes onto uv's Python 3.12 in /opt/hermes-python, readable by every user. The old environment is kept as .venv,
# where Hermes reads which extras were installed and carries them over; if the rebuild fails it is put back.
if ! venv/bin/python3 -c 'import sys; sys.exit(sys.version_info < (3, 11, 4))' 2>/dev/null; then
  command -v uv >/dev/null 2>&1 || { echo "Hermes needs Python 3.11.4 or newer and uv isn't installed to fetch it"; exit 1; }
  export UV_PYTHON_INSTALL_DIR=/opt/hermes-python
  uv python install 3.12 >/tmp/hq-hermes-python.log 2>&1 || { tail -n 10 /tmp/hq-hermes-python.log; exit 1; }
  py=$(uv python find 3.12)
  case "$py" in /opt/hermes-python/*) ;; *) echo "Python 3.12 wasn't installed in /opt/hermes-python"; exit 1 ;; esac
  chmod -R a+rX /opt/hermes-python
  rm -rf .venv && mv venv .venv
  if ! { uv venv --python "$py" venv && uv pip install --python venv/bin/python -e ".[all]"; } >>/tmp/hq-hermes-python.log 2>&1; then
    rm -rf venv; mv .venv venv; tail -n 10 /tmp/hq-hermes-python.log; echo "Hermes was left on its old Python"; exit 1
  fi
fi

# The checkout is root's, so the update runs as root. Hermes refuses to touch environments under its home that
# belong to someone else, so the home is root's for the update and goes back to its owner afterwards, even on failure.
if [ -d "$home" ] && [ "$owner" != root:root ]; then
  chown -R root:root "$home"
  trap 'chown -R "$owner" "$home"' EXIT
fi
# hermes update exits 0 even when a step didn't finish, leaving new code on old packages. Treat that as a failure.
bad='did not finish|install failed|update failed|completion failed|dependencies failed|refusing to update|no update was applied'
HERMES_HOME=$home /usr/local/bin/hermes update --yes --no-backup </dev/null >/tmp/hq-hermes-update.log 2>&1 || { tail -n 20 /tmp/hq-hermes-update.log; exit 1; }
# The first update from an old Hermes runs the old updater, which pulls the new code but installs its Node parts
# with npm, which the new code refuses (Data, 2026-11-06). A second run uses the new updater and finishes the job.
# GitHub sometimes times out on Hermes's release check ("git ls-remote ... timed out"), so up to three passes in all.
for pass in 2 3; do
  grep -qiE "$bad" /tmp/hq-hermes-update.log || break
  mv /tmp/hq-hermes-update.log /tmp/hq-hermes-update-first.log
  [ "$pass" = 3 ] && sleep 30
  HERMES_HOME=$home /usr/local/bin/hermes update --yes --no-backup </dev/null >/tmp/hq-hermes-update.log 2>&1 || { tail -n 20 /tmp/hq-hermes-update.log; exit 1; }
  [ -f pnpm-lock.yaml ] && ! git ls-files --error-unmatch package-lock.json >/dev/null 2>&1 && rm -f package-lock.json
done
if grep -qiE "$bad" /tmp/hq-hermes-update.log; then
  grep -iE "$bad" /tmp/hq-hermes-update.log | tail -n 3; exit 1
fi
/usr/local/bin/hermes --version >/dev/null 2>&1 || { echo "Hermes doesn't start after the update"; exit 1; }
'''


def parse(line):
    values = {}
    for key, value in re.findall(r'([a-z_]+)=(\S*)', line):
        values[key] = value

    def version(key):
        return values.get(key) if VERSION.fullmatch(values.get(key) or '') else None

    def prefix(key):
        given = values.get(key) or ''
        return given if given in PREFIXES or USER_PREFIX.fullmatch(given) else None
    return {
        'debian': values.get('debian'),
        'list_days': int(values['list_days']) if values.get('list_days', '').isdigit() else None,
        'pending': int(values['pending']) if values.get('pending', '').isdigit() else None,
        'security': int(values['security']) if values.get('security', '').isdigit() else None,
        'reboot': values.get('reboot') == 'yes',
        'node': values.get('node'),
        'claude': version('claude'), 'claude_prefix': prefix('claude_prefix'),
        'claude_home': values.get('claude_home') if HOME.fullmatch(values.get('claude_home') or '') else None,
        'zylos_prefix': prefix('zylos_prefix'),
        'zylos': version('zylos'),
        'hermes': version('hermes'),
        'codex': version('codex'), 'codex_prefix': prefix('codex_prefix'),
        'updates_held': values.get('updates_held') == 'yes',
    }


def owner(path):
    """The user an install under this path belongs to: the home folder's owner, or root."""
    match = re.match(r'/home/([a-z_][a-z0-9_-]{0,31})(/|$)', path or '')
    return match.group(1) if match else 'root'


def as_user(user, command):
    """Run a command as that user, with their own HOME, from a root shell in the container."""
    return command if user == 'root' else f'su - {user} -s /bin/bash -c {shlex.quote(command)}'


def _fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'agent-hq', 'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def latest_versions(fetch=_fetch):
    """Newest releases, from npm and GitHub. A failed lookup leaves that one unknown."""
    out = {}
    try:
        tags = fetch('https://registry.npmjs.org/-/package/@anthropic-ai%2fclaude-code/dist-tags')
        out['claude_stable'], out['claude_latest'] = tags.get('stable'), tags.get('latest')
    except Exception:  # Network or format problems only lose the comparison.
        pass
    try:
        out['codex_latest'] = fetch('https://registry.npmjs.org/-/package/@openai%2fcodex/dist-tags').get('latest')
    except Exception:
        pass
    for key, repo in (('zylos_latest', 'zylos-ai/zylos-core'), ('hermes_latest', 'NousResearch/hermes-agent')):
        try:
            out[key] = str(fetch(f'https://api.github.com/repos/{repo}/releases/latest').get('tag_name', '')).lstrip('v') or None
        except Exception:
            pass
    return {k: v for k, v in out.items() if v and VERSION.fullmatch(v)}


def newer(a, b):
    """True if version a is newer than b."""
    def parts(v):
        return [int(x) for x in re.findall(r'\d+', v or '')[:3]]
    return bool(a and b) and parts(a) > parts(b)


def clean_settings(data, old=None):
    """Check and fill in the Updates settings. Raises ValueError with a message for the page."""
    out = json.loads(json.dumps(old or DEFAULT_SETTINGS))
    data = data or {}
    if 'snapshot' in data:
        out['snapshot'] = bool(data['snapshot'])
    if 'cleanup' in data:
        if data['cleanup'] not in ('keep', 'after_check', 'after_hours'):
            raise ValueError('choose when snapshots are deleted')
        out['cleanup'] = data['cleanup']
    if 'cleanup_hours' in data:
        hours = data['cleanup_hours']
        if not isinstance(hours, (int, float)) or isinstance(hours, bool) or not 1 <= hours <= 720:
            raise ValueError('keep snapshots for 1 to 720 hours')
        out['cleanup_hours'] = int(hours)
    if 'schedule' in data:
        given, schedule = data['schedule'] or {}, out['schedule']
        if given.get('mode', schedule['mode']) not in ('every', 'daily', 'weekly'):
            raise ValueError('choose how often to check')
        schedule['mode'] = given.get('mode', schedule['mode'])
        if 'hours' in given:
            hours = given['hours']
            if not isinstance(hours, (int, float)) or isinstance(hours, bool) or not 1 <= hours <= 168:
                raise ValueError('check every 1 to 168 hours')
            schedule['hours'] = int(hours)
        if 'time' in given:
            if not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', str(given['time'])):
                raise ValueError('give the time as HH:MM')
            schedule['time'] = given['time']
        if 'day' in given:
            if given['day'] not in range(7):
                raise ValueError('choose a day of the week')
            schedule['day'] = given['day']
    return out


def last_due(schedule, now):
    """The latest scheduled check time at or before now, for daily and weekly schedules (local time)."""
    hour, minute = map(int, schedule['time'].split(':'))
    point = datetime.fromtimestamp(now).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if schedule['mode'] == 'weekly':
        point -= timedelta(days=(point.weekday() - schedule['day']) % 7)
        if point.timestamp() > now:
            point -= timedelta(days=7)
    elif point.timestamp() > now:
        point -= timedelta(days=1)
    return point.timestamp()


def next_check(schedule, last, now):
    """When the next scheduled check is due. Never-checked means now."""
    if not last:
        return now
    if schedule['mode'] == 'every':
        return last + schedule['hours'] * 3600
    due = last_due(schedule, now)
    if last < due:
        return due  # missed one, so it's due now
    return due + (7 if schedule['mode'] == 'weekly' else 1) * 86400


class Updates:
    def __init__(self, pve, path: Path, log, clock=time.time, settings_path=None):
        self.pve, self.path, self.log, self.clock = pve, Path(path), log, clock
        self.settings_path = Path(settings_path) if settings_path else self.path.with_name('update-settings.json')
        self._lock = threading.Lock()
        self._running = False
        self.jobs = {}
        try:
            self.state = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.state = {'checked_at': None, 'agents': {}, 'latest': {}}
        self.state.setdefault('cleanups', [])
        try:
            self.settings = clean_settings(json.loads(self.settings_path.read_text(encoding='utf-8')))
        except (OSError, ValueError):
            self.settings = clean_settings({})

    def _save(self):
        with self._lock:
            text = json.dumps(self.state)
        self.path.write_text(text, encoding='utf-8')

    # Settings
    def set_settings(self, data):
        with self._lock:
            self.settings = clean_settings(data, self.settings)
            settings = json.loads(json.dumps(self.settings))
        self.settings_path.write_text(json.dumps(settings), encoding='utf-8')
        return settings

    # The watchdog
    def _audit(self, name):
        try:
            return {**parse(self.pve.in_agent(name, CHECK, as_root=True, timeout=180)), 'ok': True}
        except ControlError as exc:
            return {'ok': False, 'error': str(exc)[:200]}

    def check(self):
        """Audit every agent container, in parallel, and save the result."""
        with self._lock:
            if self._running:
                return False
            self._running = True
        try:
            containers = self.pve.containers()
            names = [name for name in sorted(self.pve.agents) if containers.get(name, {}).get('status') == 'running']
            with ThreadPoolExecutor(max_workers=4) as pool:
                agents = dict(zip(names, pool.map(self._audit, names)))
            latest = latest_versions()
            with self._lock:
                self.state = {**self.state, 'checked_at': self.clock(), 'agents': agents, 'latest': latest}
            self._save()
            return True
        finally:
            with self._lock:
                self._running = False

    def check_one(self, host):
        """Audit one agent again and put the answer in its row, as after an update."""
        info = self._audit(host)
        with self._lock:
            self.state.setdefault('agents', {})[host] = {**info, 'checked_at': self.clock()}
        self._save()
        return info

    def checking(self):
        with self._lock:
            return self._running

    def watch(self, stop: threading.Event):
        """Check on the schedule from the settings, and delete snapshots whose wait is over. Wakes every minute."""
        while not stop.is_set():
            now = self.clock()
            if now >= next_check(self.settings['schedule'], self.state.get('checked_at'), now):
                try:
                    self.check()
                except ControlError:
                    pass  # Proxmox unreachable or not set up yet; the next round tries again
                except Exception as exc:  # noqa: BLE001 - the watchdog must keep running whatever one check does
                    self.log.log({'action': 'update_check_failed', 'error': (str(exc) or type(exc).__name__)[:200]})
                    with self._lock:  # don't retry a failing check every minute
                        self.state['checked_at'] = self.clock()
            try:
                self.run_cleanups()
            except Exception as exc:  # noqa: BLE001
                self.log.log({'action': 'snapshot_cleanup_failed', 'error': (str(exc) or type(exc).__name__)[:200]})
            if stop.wait(60):
                break

    def summary(self):
        with self._lock:
            state = json.loads(json.dumps(self.state))
            settings = json.loads(json.dumps(self.settings))
        latest = state.get('latest', {})
        targets = {'claude': latest.get('claude_stable') or latest.get('claude_latest'), 'zylos': latest.get('zylos_latest'),
                   'hermes': latest.get('hermes_latest'), 'codex': latest.get('codex_latest')}
        agents = state.get('agents', {})
        for info in agents.values():
            for tool in TOOLS:
                info[f'{tool}_behind'] = newer(targets[tool], info.get(tool))
        state['totals'] = {
            'checked': len(agents),
            **{f'{tool}_behind': sum(1 for a in agents.values() if a.get(f'{tool}_behind')) for tool in TOOLS},
            **{f'{tool}_installed': sum(1 for a in agents.values() if a.get(tool)) for tool in TOOLS},
            'security': sum(a.get('security') or 0 for a in agents.values()),
            'reboot': sum(1 for a in agents.values() if a.get('reboot')),
            'failed': sum(1 for a in agents.values() if not a.get('ok', True)),
        }
        state['checking'] = self.checking()
        state['settings'] = settings
        now = self.clock()
        state['next_check_at'] = max(now, next_check(settings['schedule'], state.get('checked_at'), now))
        return state

    # Snapshots
    def snapshots(self, host):
        vmid = self.pve.agent(host)['vmid']
        rows = json.loads(self.pve.run(['pvesh', 'get', f'/nodes/pve/lxc/{vmid}/snapshot', '--output-format', 'json']) or '[]')
        return [{'name': r.get('name'), 'taken_at': r.get('snaptime'), 'description': (r.get('description') or '').strip()[:120]}
                for r in rows if r.get('name') and r.get('name') != 'current']

    def _thin_ok(self, vmid):
        config = self.pve.run(['pct', 'config', vmid])
        match = re.search(r'^rootfs: ([^:]+):', config, re.M)
        if not match:
            raise ControlError('couldn’t find this container’s disk')
        status = json.loads(self.pve.run(['pvesh', 'get', f'/nodes/pve/storage/{match.group(1)}/status', '--output-format', 'json']))
        used, total = status.get('used') or 0, status.get('total') or 0
        if total and used / total > THIN_LIMIT:
            raise ControlError(f'storage {match.group(1)} is {round(used / total * 100)}% full, so a snapshot could fill it. Free some space first')

    def _busy(self, host):
        with self._lock:
            if any(j['host'] == host and j['state'] == 'running' for j in self.jobs.values()):
                raise ControlError('an update is running on this agent; wait for it to finish first')

    def rollback(self, host, name):
        if not SNAPSHOT.fullmatch(name or ''):
            raise ValueError('invalid snapshot name')
        self._busy(host)
        self.pve.run(['pct', 'rollback', self.pve.agent(host)['vmid'], name, '--start', '1'], timeout=300)
        self._forget_cleanup(host, name)
        self.pve.forget()

    def delete_snapshot(self, host, name):
        if not SNAPSHOT.fullmatch(name or ''):
            raise ValueError('invalid snapshot name')
        if name == 'hq-clone-source':
            raise ControlError('this snapshot is in use while a new agent is copied from this one; it is removed when that finishes')
        self._busy(host)
        self.pve.run(['pct', 'delsnapshot', self.pve.agent(host)['vmid'], name], timeout=300)
        self._forget_cleanup(host, name)

    def _forget_cleanup(self, host, name):
        with self._lock:
            before = len(self.state['cleanups'])
            self.state['cleanups'] = [c for c in self.state['cleanups'] if not (c['host'] == host and c['name'] == name)]
            changed = len(self.state['cleanups']) != before
        if changed:
            self._save()

    def _healthy(self, host):
        """The agent's container is running and the audit still runs inside it."""
        self.pve.forget()
        if self.pve.containers().get(host, {}).get('status') != 'running':
            return False, 'the container isn’t running'
        info = self.check_one(host)
        if not info.get('ok'):
            return False, info.get('error') or 'the check inside the container failed'
        return True, None

    def run_cleanups(self):
        """Delete HQ snapshots whose wait is over, if the agent still passes its checks. Otherwise keep them."""
        now = self.clock()
        with self._lock:
            due = [c for c in self.state['cleanups'] if c['due'] <= now]
        for item in due:
            host, name = item['host'], item['name']
            with self._lock:
                if any(j['host'] == host and j['state'] == 'running' for j in self.jobs.values()):
                    continue  # try again after that update
            ok, why = self._healthy(host)
            if ok:
                try:
                    self.delete_snapshot(host, name)
                    self.log.log({'action': 'snapshot_auto_deleted', 'host': host, 'snapshot': name})
                    continue
                except ControlError as exc:
                    why = str(exc)
            self._forget_cleanup(host, name)
            self.log.log({'action': 'snapshot_kept', 'host': host, 'snapshot': name, 'error': (why or '')[:200]})

    # Updates
    def start(self, host, kind, version=None, snapshot=None):
        if kind not in KINDS:
            raise ValueError('unknown update')
        if kind in NPM and not VERSION.fullmatch(version or ''):
            raise ValueError(f'choose a {NAMES[kind]} version')
        self.pve.agent(host)
        with self._lock:
            info = self.state.get('agents', {}).get(host) or {}
        if kind in TOOLS and info.get('ok') and not info.get(kind):
            raise ControlError(f'{NAMES[kind]} isn’t installed on this agent')
        if kind in NPM and info.get(kind) and not newer(version, info[kind]):
            raise ControlError(f'{NAMES[kind]} is already {info[kind]}, so {version} would not be newer')
        with self._lock:
            if any(j['host'] == host and j['state'] == 'running' for j in self.jobs.values()):
                raise ControlError('an update is already running on this agent')
            job_id = uuid.uuid4().hex[:10]
            self.jobs[job_id] = {'id': job_id, 'host': host, 'kind': kind, 'version': version, 'state': 'running', 'steps': [],
                                 'started_at': self.clock(), 'ended_at': None, 'snapshot': None, 'error': None,
                                 'take_snapshot': self.settings['snapshot'] if snapshot is None else bool(snapshot),
                                 'prefix': info.get(f'{kind}_prefix'), 'home': info.get(f'{kind}_home')}
        threading.Thread(target=self._run, args=(job_id,), daemon=True).start()
        return job_id

    def _snapshot_name(self, host, kind):
        """hq-<kind>-<date>-<time to the second>, with a -2, -3 ... if the agent already has that name.
        Named to the minute, a retry in the same minute used to be refused by Proxmox as a duplicate."""
        base = f'hq-{kind}-{time.strftime("%Y%m%d-%H%M%S", time.localtime(self.clock()))}'
        taken = {s['name'] for s in self.snapshots(host)}
        name, n = base, 1
        while name in taken:
            n += 1
            name = f'{base}-{n}'
        return name

    def _step(self, job, text):
        job['steps'].append({'at': self.clock(), 'text': text})

    def _install(self, job):
        host, kind, version = job['host'], job['kind'], job['version']
        prefix = job.get('prefix') if job.get('prefix') in PREFIXES or USER_PREFIX.fullmatch(job.get('prefix') or '') else None
        if kind in NPM:
            self._step(job, f'Installing {NAMES[kind]} {version}')
            home = job.get('home') if HOME.fullmatch(job.get('home') or '') else None
            if kind == 'claude' and home:
                # Native install: Claude Code's own installer, as the user it belongs to.
                command = f'{home}/.local/bin/claude install {version} </dev/null >/tmp/hq-claude.log 2>&1 || (tail -n 20 /tmp/hq-claude.log; exit 1)'
                user = owner(home)
            else:
                prefix = prefix or '/usr'
                command = f'PATH={prefix}/bin:$PATH npm install -g --prefix {prefix} --no-fund --no-audit {NPM[kind]}@{version} >/tmp/hq-npm.log 2>&1 || (tail -n 20 /tmp/hq-npm.log; exit 1)'
                user = owner(prefix)
            self.pve.in_agent(host, as_user(user, command), as_root=True, timeout=600)
            found = parse(self.pve.in_agent(host, CHECK, as_root=True, timeout=180)).get(kind)
            if found != version:
                raise ControlError(f'{NAMES[kind]} reports {found or "nothing"} after installing {version}')
            self._step(job, f'{NAMES[kind]} is now {found}')
            if kind == 'claude':
                info = self.pve.session_info(host)
                if info['restartable']:
                    self._step(job, 'Restarting the Claude session so it uses the new version')
                    self.pve.restart_claude(host)
                else:
                    self._step(job, 'The running Claude session keeps the old version until the container restarts')
        elif kind == 'zylos':
            self._step(job, 'Running zylos upgrade --self')
            path = f'PATH={prefix}/bin:$PATH ' if prefix else ''
            command = f'{path}zylos upgrade --self --yes >/tmp/hq-zylos-upgrade.log 2>&1 || (tail -n 20 /tmp/hq-zylos-upgrade.log; exit 1)'
            self.pve.in_agent(host, as_user(owner(prefix or '/usr'), command), as_root=True, timeout=900)
            self._step(job, f'Zylos is now {parse(self.pve.in_agent(host, CHECK, as_root=True, timeout=180)).get("zylos") or "unknown"}')
        elif kind == 'hermes':
            self._step(job, 'Running hermes update')
            self.pve.in_agent(host, HERMES_UPDATE, as_root=True, timeout=2400)
            self._step(job, f'Hermes is now {parse(self.pve.in_agent(host, CHECK, as_root=True, timeout=180)).get("hermes") or "unknown"}')
        else:
            self._step(job, 'Refreshing package lists and installing updates')
            self.pve.in_agent(host, 'export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get -y -qq -o Dpkg::Options::=--force-confold upgrade >/tmp/hq-apt.log 2>&1 || (tail -n 20 /tmp/hq-apt.log; exit 1)', as_root=True, timeout=1800)
            after = parse(self.pve.in_agent(host, CHECK, as_root=True, timeout=180))
            self._step(job, f'{after.get("security") or 0} security updates left' + ('; a restart is needed to finish' if after.get('reboot') else ''))

    def _run(self, job_id):
        job = self.jobs[job_id]
        host, kind, version = job['host'], job['kind'], job['version']
        vmid = self.pve.agent(host)['vmid']
        self.log.log({'action': 'update_started', 'host': host, 'kind': kind, 'version': version, 'snapshot': job['take_snapshot']})
        try:
            if job['take_snapshot']:
                self._thin_ok(vmid)
                name = self._snapshot_name(host, kind)
                self._step(job, f'Taking snapshot {name}')
                self.pve.run(['pct', 'snapshot', vmid, name, '--description', f'Before HQ {kind} update'], timeout=300)
                job['snapshot'] = name
            else:
                self._step(job, 'No snapshot: switched off for this update')
            self._install(job)
            # Checks: the container must still be running and the audit must run inside it. This also
            # refreshes the agent's versions on the page.
            self._step(job, 'Checking the agent')
            ok, why = self._healthy(host)
            if not ok:
                raise ControlError(why + ' after the update')
            self._finish_snapshot(job)
            job['state'] = 'done'
            self.log.log({'action': 'update_done', 'host': host, 'kind': kind, 'version': version, 'snapshot': job['snapshot']})
        except Exception as exc:  # noqa: BLE001 - any failure must end the job, or the agent can't be updated again
            job['state'], job['error'] = 'failed', (str(exc) or type(exc).__name__)[:400]
            undo = (f'. Roll back to {job["snapshot"]} to undo any changes' if job['snapshot']
                    else '. Nothing was changed' if not any(s['text'].startswith(('Installing', 'Running', 'Refreshing')) for s in job['steps'])
                    else '. There is no snapshot to roll back to')
            self._step(job, 'Failed: ' + job['error'] + undo)
            self.log.log({'action': 'update_failed', 'host': host, 'kind': kind, 'error': job['error'], 'snapshot': job['snapshot']})
            try:
                self.check_one(host)  # show what the agent has now
            except Exception:  # noqa: BLE001
                pass
        finally:
            job['ended_at'] = self.clock()

    def _finish_snapshot(self, job):
        """After the checks pass: delete the snapshot now, schedule it, or keep it, as the settings say."""
        host, name = job['host'], job['snapshot']
        if not name:
            self._step(job, 'Done. Checks passed')
            return
        cleanup, hours = self.settings['cleanup'], self.settings['cleanup_hours']
        if cleanup == 'after_check':
            self.pve.run(['pct', 'delsnapshot', self.pve.agent(host)['vmid'], name], timeout=300)
            job['snapshot_deleted'] = True
            self.log.log({'action': 'snapshot_auto_deleted', 'host': host, 'snapshot': name})
            self._step(job, f'Done. Checks passed, so snapshot {name} was deleted')
        elif cleanup == 'after_hours':
            with self._lock:
                self.state['cleanups'].append({'host': host, 'name': name, 'due': self.clock() + hours * 3600})
            self._save()
            self._step(job, f'Done. Checks passed. Snapshot {name} is deleted in {hours} h if the agent is still fine')
        else:
            self._step(job, 'Done. Checks passed. The snapshot stays until you delete it')

    def job_list(self):
        with self._lock:
            return sorted((json.loads(json.dumps(j)) for j in self.jobs.values()), key=lambda j: j['started_at'], reverse=True)[:30]
