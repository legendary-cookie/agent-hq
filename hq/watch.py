"""Watching the fleet: health warnings, Telegram alerts, and restarting Claude when its session has gone.

Everything here uses what HQ already knows: the office snapshot (who is unreachable, held up by a usage
limit or hitting API errors), 9router's accounts, and the Proxmox container list (status, disk and
memory). Every minute the conditions are worked out again. A condition that is new, or still there after
the cooldown, is sent to Telegram once, and an agent that comes back is reported once too.

Restarting Claude is the only thing this module does to an agent. It only happens for agents started by the
Zylos runtime service, only after two checks five minutes apart both found no Claude session, and at most
three times in six hours per agent. Every restart and every alert is written to the control log.
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .control.pve import ControlError

KINDS = {
    'down': 'Agent unreachable or stopped',
    'limit': 'Usage limit hit',
    'errors': 'API errors',
    'disk': 'Disk nearly full',
    'memory': 'Memory nearly full',
    'crash': 'Claude session stopped',
    'host': 'Proxmox node, storage or cluster problem',
    'backup': 'Backups missing or failing',
    'router': '9router down, or an account failing or near its limit',
}
BOT_TOKEN = re.compile(r'\d{6,12}:[A-Za-z0-9_-]{30,50}')
CHAT_ID = re.compile(r'-?\d{4,16}')
TICK_S = 60
HEALTH_EVERY_S = 300
RESTART_LIMIT = 3
RESTART_WINDOW_S = 6 * 3600
DEFAULTS = {'enabled': False, 'chat_id': '', 'bot_token': '', 'kinds': {kind: True for kind in KINDS}, 'auto_restart': True,
            'cooldown_min': 60, 'disk_pct': 90, 'memory_pct': 90, 'backup_hours': 48}


def send_telegram(token, chat_id, text, opener=urllib.request.urlopen):
    """Send one message. Raises ValueError with a readable reason if Telegram refuses it."""
    body = json.dumps({'chat_id': chat_id, 'text': text, 'disable_web_page_preview': True}).encode()
    request = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage', data=body, headers={'Content-Type': 'application/json'})
    try:
        with opener(request, timeout=15) as response:
            answer = json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as exc:
        try:
            reason = json.loads(exc.read() or b'{}').get('description')
        except ValueError:
            reason = None
        raise ValueError(f'Telegram refused the message: {reason or exc.code}') from None
    except (urllib.error.URLError, OSError) as exc:
        raise ValueError(f'couldn’t reach Telegram: {getattr(exc, "reason", exc)}') from None
    if not answer.get('ok'):
        raise ValueError(f'Telegram refused the message: {answer.get("description") or "no reason given"}')


class AlertSettings:
    """alerts.json in HQ's data folder, readable only by HQ. The bot token is never sent back to the browser."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def load(self):
        try:
            saved = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            saved = {}
        data = {**DEFAULTS, **(saved if isinstance(saved, dict) else {})}
        saved_kinds = data.get('kinds') if isinstance(data.get('kinds'), dict) else {}
        # Kinds HQ no longer has (such as the retired login pool's "login") are dropped.
        data['kinds'] = {**DEFAULTS['kinds'], **{k: v for k, v in saved_kinds.items() if k in KINDS}}
        return data

    def public(self):
        data = self.load()
        token = data.pop('bot_token', '')
        return {**data, 'has_token': bool(token), 'ready': bool(token and data.get('chat_id')), 'kind_names': KINDS}

    def update(self, values):
        if not isinstance(values, dict):
            raise ValueError('expected settings')
        with self._lock:
            data = self.load()
            if 'enabled' in values:
                data['enabled'] = bool(values['enabled'])
            if 'auto_restart' in values:
                data['auto_restart'] = bool(values['auto_restart'])
            if 'kinds' in values:
                if not isinstance(values['kinds'], dict) or any(k not in KINDS for k in values['kinds']):
                    raise ValueError('unknown alert kind')
                data['kinds'] = {**data['kinds'], **{k: bool(v) for k, v in values['kinds'].items()}}
            for key, low, high in (('cooldown_min', 5, 1440), ('disk_pct', 50, 99), ('memory_pct', 50, 99), ('backup_hours', 6, 2160)):
                if key in values:
                    value = values[key]
                    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                        raise ValueError(f'{key} must be a whole number from {low} to {high}')
                    data[key] = value
            if 'chat_id' in values:
                chat = str(values['chat_id'] or '').strip()
                if chat and not CHAT_ID.fullmatch(chat):
                    raise ValueError('the chat ID is a number, such as 123456789 or -1001234567890 for a group')
                data['chat_id'] = chat
            if values.get('bot_token'):
                token = str(values['bot_token']).strip()
                if not BOT_TOKEN.fullmatch(token):
                    raise ValueError('that doesn’t look like a bot token from BotFather (123456789:ABC…)')
                data['bot_token'] = token
            self._save(data)
        return self.public()

    def _save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + '.tmp')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream)
        os.replace(tmp, self.path)


def prompt_for_alerts(path: Path) -> int:
    """Save the Telegram bot token and chat from a terminal. The token is never echoed or logged."""
    import getpass
    token = getpass.getpass('Telegram bot token (from BotFather): ').strip()
    chat = input('Chat ID to send alerts to (your user ID, or a group ID starting with -100): ').strip()
    settings = AlertSettings(path)
    try:
        settings.update({'bot_token': token, 'chat_id': chat})
        send_telegram(token, chat, 'Alerts from HQ will arrive here.')
    except ValueError as exc:
        print(f'{exc}. The token and chat were saved anyway; fix them in Settings or run this again.' if settings.public()['has_token'] else f'{exc}. Nothing was changed.')
        return 1
    settings.update({'enabled': True})
    print('Saved, and a test message was sent. Alerts are on; choose which ones in Settings > Alerts.')
    return 0


def limited(agent):
    """Same rule as the Office: a limit message with no work after it (a person's message isn't work)."""
    limit_at = agent.get('limit_at')
    if not limit_at:
        return False
    act = agent.get('activity')
    if not act or act.get('at', 0) <= limit_at + 60:
        return True
    person = agent.get('last_from_person')
    return act.get('kind') == 'message' and bool(person) and abs(act.get('at', 0) - person) < 1


class Watchman:
    def __init__(self, settings: AlertSettings, pve, log, office, brand=lambda: 'Agent HQ',
                 clock=time.time, sender=send_telegram, hands_off=lambda host: False, infra=lambda: None, router=lambda: None):
        self.settings, self.pve, self.log = settings, pve, log
        self.infra = infra  # the latest Proxmox snapshot (hq.infra), or None before the first one
        self.router = router  # the latest 9router snapshot (hq.router), or None
        self.office, self.brand = office, brand
        self.clock, self.sender = clock, sender
        # True while someone is working on an agent (an update running, or a person controlling it lately),
        # so HQ doesn't restart a Claude that was stopped on purpose.
        self.hands_off = hands_off
        self._lock = threading.Lock()
        self.warnings = {}       # (kind, host) -> {'kind', 'host', 'label', 'text', 'since'}
        self._sent = {}          # message key -> when it was last tried, sent or not
        self._containers = {}    # from the last health check
        self._health_at = 0.0
        self._health_found = []
        self._missing = {}       # host -> consecutive checks with no Claude session
        self._restarts = {}      # host -> [times HQ restarted Claude]
        self._last_error = None
        self.last_tick = None

    # Gathering
    def _health(self, labels):
        """Containers' status, disk and memory, and Claude sessions on agents the runtime service starts."""
        found = []
        try:
            containers = self.pve.containers() if self.pve.configured() else {}
        except ControlError as exc:
            # Keep what was known, and say Proxmox can't be reached, rather than pretending all is well.
            kept = [item for item in self._health_found if item[0] != 'proxmox']
            return kept + [('down', 'proxmox', f'HQ can’t reach the Proxmox host, so container health isn’t being checked: {str(exc)[:120]}')]
        with self._lock:
            self._containers = containers
        cfg = self.settings.load()
        for host, info in containers.items():
            label = labels.get(host, host)
            if info.get('status') != 'running':
                with self._lock:
                    self._missing.pop(host, None)
                found.append(('down', host, f'{label}: its container is {info.get("status") or "not running"}'))
                continue
            for kind, used, total in (('disk', info.get('disk'), info.get('maxdisk')), ('memory', info.get('mem'), info.get('maxmem'))):
                if used and total and used / total * 100 >= cfg[f'{kind}_pct']:
                    found.append((kind, host, f'{label}: {kind} {round(used / total * 100)}% full'))
            found += self._claude(host, label, cfg)
        return found + self._infra(cfg) + self._router()

    def _infra(self, cfg):
        """Nodes, cluster, storage, guests that aren't agents (such as HQ itself), and backups."""
        snap = self.infra()
        if not snap:
            return []
        found, now = [], self.clock()
        cluster = snap.get('cluster') or {}
        if cluster.get('is_cluster') and not cluster.get('quorate'):
            found.append(('host', 'cluster', f'The Proxmox cluster {cluster.get("name") or ""} has lost quorum, so guests can’t be started or changed'.replace('  ', ' ')))
        for node in snap.get('nodes') or []:
            name = node.get('name')
            if node.get('status') != 'online':
                found.append(('host', f'node:{name}', f'Proxmox node {name} is offline'))
                continue
            if node.get('maxmem') and node.get('mem', 0) / node['maxmem'] * 100 >= cfg['memory_pct']:
                found.append(('host', f'node-mem:{name}', f'Proxmox node {name}: memory {round(node["mem"] / node["maxmem"] * 100)}% used'))
            if node.get('maxrootfs') and node.get('rootfs', 0) / node['maxrootfs'] * 100 >= cfg['disk_pct']:
                found.append(('host', f'node-disk:{name}', f'Proxmox node {name}: system disk {round(node["rootfs"] / node["maxrootfs"] * 100)}% full'))
        for store in snap.get('storage') or []:
            if store.get('status') == 'available' and store.get('total') and store.get('used', 0) / store['total'] * 100 >= cfg['disk_pct']:
                where = f' on {store["node"]}' if store.get('node') else ''
                found.append(('host', f'storage:{store.get("node") or ""}:{store.get("id")}', f'Storage {store.get("id")}{where} is {round(store["used"] / store["total"] * 100)}% full'))
        guests = [g for g in snap.get('guests') or [] if not g.get('template')]
        # Agents' own containers are checked above; this covers everything else, HQ's container included.
        for guest in guests:
            if guest.get('host') or guest.get('status') != 'running' or guest.get('type') != 'lxc':
                continue
            for kind, used, total in (('memory', guest.get('mem'), guest.get('maxmem')), ('disk', guest.get('disk'), guest.get('maxdisk'))):
                if used and total and used / total * 100 >= cfg[f'{kind}_pct']:
                    found.append((kind, f'ct{guest["vmid"]}', f'{guest.get("name")} (CT {guest["vmid"]}): {kind} {round(used / total * 100)}% full'))
        backups = snap.get('backups') or {}
        if backups.get('read'):
            limit = cfg['backup_hours'] * 3600
            stale = [g for g in guests if g.get('host') and (not g.get('last_backup') or now - g['last_backup'] > limit)]
            if stale:
                names = ', '.join(g['label'] for g in stale[:6]) + (f' and {len(stale) - 6} more' if len(stale) > 6 else '')
                found.append(('backup', 'backups', f'{len(stale)} {"agent has" if len(stale) == 1 else "agents have"} no backup in the last {cfg["backup_hours"]} hours: {names}'))
            for task in backups.get('failed') or []:
                if now - task.get('at', 0) < 86400:
                    found.append(('backup', f'backup-task:{task.get("vmid") or task.get("node")}', f'A backup failed on {task.get("node")}{" for CT " + str(task["vmid"]) if task.get("vmid") else ""}: {task.get("status")}'))
        return found

    def _router(self):
        """Every 9router: the service, and each account's errors, cooldown and weekly limit."""
        snap = self.router()
        if not snap or not snap.get('found'):
            return []
        found = []
        for r in snap.get('routers') or [snap]:
            found += self._one_router(r, len(snap.get('routers') or [snap]) > 1)
        return found

    def _one_router(self, snap, several):
        where = f'{snap.get("label")} (CT {snap.get("vmid")})' if several else '9router'
        key0 = f'9router:{snap.get("vmid")}' if several else '9router'
        if not snap.get('up'):
            return [('router', key0, f'{where if several else "9router (CT " + str(snap.get("vmid")) + ")"} isn’t answering: container {snap.get("status")}, service {(snap.get("services") or {}).get("9router")}')]
        found, now = [], self.clock()
        for c in snap.get('connections') or []:
            if c.get('link'):
                continue  # the fallback to another router: that router's own accounts are checked there
            if not c.get('active'):
                continue
            name, key = c.get('name') or c.get('id'), f'router:{c.get("id")}'
            if c.get('test_status') not in (None, 'active', 'ok', 'success'):
                found.append(('router', key, f'{where} account {name}: {c.get("test_status")}{" (" + c["last_error"][:100] + ")" if c.get("last_error") else ""}'))
            elif c.get('rate_limited_until'):
                found.append(('router', key, f'{where} account {name} is cooling down after a limit, until {time.strftime("%H:%M", time.localtime(c["rate_limited_until"]))}'))
            elif (c.get('limit') or {}).get('left_pct') is not None and c['limit']['left_pct'] <= 10:
                found.append(('router', key, f'{where} account {name}: {c["limit"]["window"]} limit {c["limit"]["left_pct"]:.0f}% left'))
        return found

    def _claude(self, host, label, cfg):
        try:
            info = self.pve.session_info(host)
        except ControlError:
            with self._lock:
                self._missing.pop(host, None)
            return []
        # Only a session named like Claude's counts; another tmux session doesn't mean Claude is running.
        running = any(name.startswith('claude') for name in info.get('sessions') or ([info['session']] if info.get('session') else []))
        if not info.get('restartable') or running or self.hands_off(host):
            with self._lock:
                self._missing.pop(host, None)
            return []
        with self._lock:
            self._missing[host] = self._missing.get(host, 0) + 1
            missing = self._missing[host]
        if missing < 2:
            return []  # might be starting up; look again next time
        now = self.clock()
        with self._lock:
            recent = [t for t in self._restarts.get(host, []) if now - t < RESTART_WINDOW_S]
            self._restarts[host] = recent
        if not cfg.get('auto_restart'):
            return [('crash', host, f'{label}: no Claude session is running (automatic restart is off)')]
        if len(recent) >= RESTART_LIMIT:
            return [('crash', host, f'{label}: Claude keeps stopping. HQ restarted it {len(recent)} times in 6 hours and has stopped trying')]
        try:
            self.pve.restart_claude(host)
        except ControlError as exc:
            self.log.log({'action': 'auto_restart_failed', 'host': host, 'error': str(exc)[:200]})
            return [('crash', host, f'{label}: Claude had stopped and HQ couldn’t restart it: {str(exc)[:120]}')]
        with self._lock:
            recent.append(now)
            self._missing.pop(host, None)
        self.log.log({'action': 'auto_restart_claude', 'host': host})
        # Its own message key, so it never holds back a later "keeps stopping" warning.
        self._send_now('crash', host, f'{label}: Claude had stopped, so HQ restarted it', key=('restarted', host))
        return []

    def _conditions(self):
        found = []
        snapshot = self.office() or {}
        agents = [a for a in snapshot.get('agents', []) if isinstance(a, dict) and a.get('host')]
        labels = {a['host']: a.get('label') or a['host'] for a in agents}
        now = self.clock()
        for agent in agents:
            host, label = agent['host'], labels[agent['host']]
            if agent.get('status') == 'attention':
                seen = agent.get('last_seen')
                ago = f', last heard from {round((now - seen) / 60)} min ago' if seen else ''
                found.append(('down', host, f'{label} isn’t reporting{ago}'))
            if limited(agent):
                found.append(('limit', host, f'{label} hit a usage limit and is waiting'))
            if agent.get('api_error_at') and now - agent['api_error_at'] < 15 * 60:
                found.append(('errors', host, f'{label} is getting API errors from the model'))
        if now - self._health_at >= HEALTH_EVERY_S:
            self._health_at = now
            self._health_found = self._health(labels)
        found += self._health_found
        # One "down" per agent is enough.
        seen, unique = set(), []
        for item in found:
            if item[:2] not in seen:
                seen.add(item[:2])
                unique.append(item)
        return unique, labels

    # Sending
    def _send_now(self, kind, host, text, key=None):
        """Send one alert. The attempt is recorded whether or not it works, so a broken bot token or
        an unreachable Telegram means one try (and one log line) per cooldown, not one a minute."""
        cfg = self.settings.load()
        if not (cfg['enabled'] and cfg['bot_token'] and cfg['chat_id'] and cfg['kinds'].get(kind, True)):
            return False
        with self._lock:
            self._sent[key or (kind, host)] = self.clock()
        try:
            self.sender(cfg['bot_token'], cfg['chat_id'], f'{self.brand()}: {text}')
        except ValueError as exc:
            self.log.log({'action': 'alert_failed', 'kind': kind, 'host': host, 'error': str(exc)[:200]})
            return False
        self.log.log({'action': 'alert_sent', 'kind': kind, 'host': host, 'text': text[:300]})
        return True

    def _due(self, key, now, cooldown):
        with self._lock:
            last = self._sent.get(key)
        return last is None or now - last >= cooldown

    def tick(self):
        found, labels = self._conditions()
        now = self.clock()
        cooldown = self.settings.load()['cooldown_min'] * 60
        current = {}
        with self._lock:
            before = dict(self.warnings)
        for kind, host, text in found:
            old = before.get((kind, host))
            current[(kind, host)] = {'kind': kind, 'host': host, 'label': labels.get(host, host), 'text': text, 'since': old['since'] if old else now}
            # The cooldown is kept even after a problem clears, so a flapping one isn't sent every minute.
            if self._due((kind, host), now, cooldown):
                self._send_now(kind, host, text)
        with self._lock:
            self.warnings = current
        for (kind, host), old in before.items():
            # "Back" only for a real outage (3 minutes or more) that was alerted, and at most once per cooldown.
            if kind == 'down' and (kind, host) not in current and now - old['since'] >= 180 and self._due(('back', host), now, cooldown):
                with self._lock:
                    alerted = (kind, host) in self._sent
                if alerted:
                    self._send_now('down', host, f'{labels.get(host, old["label"])} is back', key=('back', host))
        self.last_tick = now

    def run(self, stop: threading.Event):
        while not stop.wait(TICK_S):
            try:
                self.tick()
                self._last_error = None
            except Exception as exc:  # noqa: BLE001 - the watcher must keep going
                message = (str(exc) or type(exc).__name__)[:200]
                if message != self._last_error:  # the same error every minute is logged once
                    self.log.log({'action': 'watch_failed', 'error': message})
                self._last_error = message

    def summary(self):
        with self._lock:
            warnings = sorted(self.warnings.values(), key=lambda w: (w['since'], w['host']))
            containers = {host: {k: info.get(k) for k in ('status', 'disk', 'maxdisk', 'mem', 'maxmem', 'cpu')} for host, info in self._containers.items()}
            restarts = {host: list(times) for host, times in self._restarts.items() if times}
        return {'warnings': warnings, 'checked_at': self.last_tick, 'containers': containers, 'restarts': restarts}
    def test(self):
        cfg = self.settings.load()
        if not (cfg['bot_token'] and cfg['chat_id']):
            raise ValueError('add the bot token and chat ID first')
        self.sender(cfg['bot_token'], cfg['chat_id'], f'{self.brand()}: test alert. Alerts reach this chat.')
        self.log.log({'action': 'alert_test'})
