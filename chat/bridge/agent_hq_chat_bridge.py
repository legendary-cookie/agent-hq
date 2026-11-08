#!/usr/bin/env python3
"""Agent HQ Chat bridge: runs on an agent's container and connects the agent to Agent HQ Chat.

It only makes outgoing HTTPS requests to the chat server (long polling), so the server needs no
login on the container. Python 3.9+ standard library only.

  agent_hq_chat_bridge.py run            keep fetching messages and handing them to the agent
  agent_hq_chat_bridge.py check          test the token and connection
  agent_hq_chat_bridge.py send <endpoint> <text...>   post as the agent ("-" reads the text from stdin);
                                        used by the Zylos "webchat" channel script
  agent_hq_chat_bridge.py fetch <file-url> <path>     download a file someone attached
  agent_hq_chat_bridge.py activity                   print what the agent is doing (as sent to the chat every few seconds)
  agent_hq_chat_bridge.py tg-sync [--dry-run]         copy the agent's Telegram log to Agent HQ Chat now
                                        ("run" does this every minute on Zylos agents; c4.db is only read)

Kinds:
  zylos   hands each message to the Zylos comm bridge (c4-receive, channel "webchat"). The agent
          answers with c4-send webchat <endpoint> "...", which calls this script's "send".
  hermes  runs "hermes chat -q" per message (resuming one Hermes session per conversation), or
          calls the Hermes API server when hermes.mode is "api".

Config: $HQ_CHAT_CONFIG or /etc/agent-hq-chat-bridge.json, readable only by the agent's user:
  {"server": "https://192.0.2.81", "token": "acb_...", "kind": "zylos", "ca_file": "/etc/agent-hq-chat-ca.crt"}
"""
import html
import json
import os
import platform
import re
import shutil
import signal
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

VERSION = '0.3.0'
CHANNEL = 'webchat'
ANSI = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07')
SESSION_PATTERNS = [re.compile(p) for p in (r'--resume\s+([A-Za-z0-9_.:-]{6,})', r'[Ss]ession(?:\s+ID)?:\s*([A-Za-z0-9_.:-]{6,})')]
HERMES_NOISE = re.compile(r'^\s*(Resume this session|Session(\s+ID)?:|Title:|Duration:|Messages:|Tokens?:|Cost:|hermes\s|╭|╰|│\s*$)', re.I)


def log(*parts):
    print(time.strftime('%Y-%m-%d %H:%M:%S'), *parts, flush=True)


def load_config():
    path = os.environ.get('HQ_CHAT_CONFIG', '/etc/agent-hq-chat-bridge.json')
    try:
        cfg = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        sys.exit('cannot read %s: %s' % (path, exc))
    for key in ('server', 'token', 'kind'):
        if not cfg.get(key):
            sys.exit('%s is missing "%s"' % (path, key))
    cfg['server'] = cfg['server'].rstrip('/')
    return cfg


class Api:
    def __init__(self, cfg):
        self.base, self.token = cfg['server'], cfg['token']
        if cfg.get('insecure'):
            self.ctx = ssl._create_unverified_context()
        else:
            self.ctx = ssl.create_default_context(cafile=cfg.get('ca_file') or None)

    def download(self, url, path):
        if not url.startswith(self.base + '/bridge/v1/files/'):
            raise RuntimeError('only Agent HQ Chat file links can be fetched')
        req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + self.token})
        with urllib.request.urlopen(req, timeout=120, context=self.ctx if url.startswith('https') else None) as resp, open(path, 'wb') as out:
            shutil.copyfileobj(resp, out)

    def call(self, method, path, data=None, timeout=30):
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(self.base + path, data=body, method=method, headers={
            'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json', 'User-Agent': 'agent-hq-chat-bridge/' + VERSION})
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=self.ctx if self.base.startswith('https') else None) as resp:
                return json.loads(resp.read() or b'{}')
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode('utf-8', 'replace')
            raise RuntimeError('server said %s: %s' % (exc.code, detail)) from None


# ------------------------------------------------------------------ Zylos
def find_receive(cfg):
    """The Zylos c4-receive script. Set zylos.receive in the config if it lives somewhere else."""
    z = cfg.get('zylos') or {}
    if z.get('receive'):
        return z['receive']
    home = Path(z.get('home') or Path.home())
    for base in ([Path(z['dir'])] if z.get('dir') else []) + [home / 'zylos', Path('/opt/zylos')]:
        for rel in ('.claude/skills/comm-bridge/scripts/c4-receive.js', 'comm-bridge/scripts/c4-receive.js', 'comm-bridge/c4-receive.js'):
            if (base / rel).is_file():
                return str(base / rel)
    return None


class Zylos:
    def __init__(self, cfg, api):
        self.cfg, self.api = cfg, api
        self.receive = find_receive(cfg)
        self.node = shutil.which('node') or '/usr/bin/node'
        # Zylos picks its database from ZYLOS_DIR (staff agents: /opt/zylos, from /etc/zylos/agent.env).
        # It must match the dispatcher's, or messages land in a database nobody reads.
        self.env = dict(os.environ)
        if (cfg.get('zylos') or {}).get('dir'):
            self.env['ZYLOS_DIR'] = cfg['zylos']['dir']

    def info(self):
        return {'receive': self.receive or 'not found', 'zylos_dir': self.env.get('ZYLOS_DIR', '~/zylos')}

    def handle(self, job, cancelled):
        if not self.receive:
            raise RuntimeError('Zylos c4-receive was not found on this container')
        # The Zylos dispatcher adds its own "reply via c4-send webchat <endpoint>" line.
        content = job['prompt']
        args = (self.cfg.get('zylos') or {}).get('receive_args') or ['--channel', '{channel}', '--endpoint', '{endpoint}', '--content', '{content}']
        cmd = [self.node, self.receive] + [a.replace('{channel}', CHANNEL).replace('{endpoint}', job['endpoint']).replace('{content}', content) for a in args]
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=self.env)
        if done.returncode != 0:
            raise RuntimeError('c4-receive failed: ' + (done.stderr or done.stdout).strip()[-300:])
        # The reply arrives later through "send"; the server shows the agent as working until then.
        self.api.call('POST', '/bridge/v1/deliveries/%d/working' % job['delivery_id'], {})


# ------------------------------------------------------------------ Telegram history (read-only)
TG_HEAD = re.compile(r'^\[TG (DM|GROUP:(?P<group>[^\]]*))\]\s*(?P<who>.*?) said:\s*', re.S)
TG_TAGS = re.compile(r'</?(current-message|replying-to|group-context)>')
TG_FILE = re.compile(r'\s*---- file: (\S+)\s*$')
TG_REPLY_VIA = re.compile(r'\s*----\s*reply via:.*$', re.S)
TG_BATCH = 300


def tg_parse(direction, endpoint, content):
    """One c4 Telegram row -> (chat_id, sender, text, group), or None to skip it.

    Scheduled-task prompts and other internal rows also use the telegram channel; only real
    "[TG DM]" / "[TG GROUP:...]" messages and the agent's own replies are kept."""
    chat = (endpoint or '').split('|', 1)[0].strip()
    if not chat:
        return None
    if direction == 'out':
        return chat, None, html.unescape(content).strip(), None
    m = TG_HEAD.match(content)
    if not m:
        return None
    body = TG_REPLY_VIA.sub('', content[m.end():])
    g = re.search(r'<group-context>.*?</group-context>', body, re.S)
    if g:
        body = body.replace(g.group(0), '')  # earlier group lines, already synced as their own rows
    quoted = ''
    r = re.search(r'<replying-to>(.*?)</replying-to>', body, re.S)
    if r:
        quoted = r.group(1).strip()
        body = body.replace(r.group(0), '')
    f = TG_FILE.search(body)
    if f:
        body = body[:f.start()] + '\n\n_[attachment: %s]_' % os.path.basename(f.group(1))
    body = html.unescape(TG_TAGS.sub('', body)).strip()
    if quoted:
        body = '\n'.join('> ' + line for line in quoted.splitlines()[:6]) + '\n\n' + body
    return chat, m.group('who').strip(), body, (m.group('group') or None)


class TelegramSync:
    """Copies Telegram messages from the Zylos c4 log to Agent HQ Chat. Opens c4.db read-only."""

    def __init__(self, cfg, api):
        self.cfg, self.api = cfg, api
        z = cfg.get('zylos') or {}
        base = Path(z.get('dir') or Path.home() / 'zylos')
        self.db = Path((cfg.get('telegram') or {}).get('c4_db') or base / 'comm-bridge' / 'c4.db')
        state = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'agent-hq-chat-bridge'
        state.mkdir(parents=True, exist_ok=True)
        self.state_file = state / 'telegram-sync.json'
        try:
            self.names = json.loads((base / 'components' / 'telegram' / 'user-cache.json').read_text())
        except (OSError, ValueError):
            self.names = {}

    def enabled(self):
        return (self.cfg.get('telegram') or {}).get('sync', False) and self.db.is_file()

    def _last(self):
        try:
            return int(json.loads(self.state_file.read_text()).get('last_id', 0))
        except (OSError, ValueError):
            return 0

    def _save(self, last_id):
        tmp = self.state_file.with_suffix('.tmp')
        tmp.write_text(json.dumps({'last_id': last_id}))
        tmp.replace(self.state_file)

    def rows(self, after, limit):
        import sqlite3
        con = sqlite3.connect('file:%s?mode=ro' % self.db, uri=True, timeout=10)
        try:
            return con.execute("SELECT id, direction, endpoint_id, content, timestamp FROM conversations "
                               "WHERE channel='telegram' AND id>? ORDER BY id LIMIT ?", (after, limit)).fetchall()
        finally:
            con.close()

    def items(self, rows):
        out = []
        for rid, direction, endpoint, content, ts in rows:
            parsed = tg_parse(direction, endpoint, content or '')
            if not parsed or not parsed[2]:
                continue
            chat, who, text, group = parsed
            out.append({'id': rid, 'chat': chat, 'out': direction == 'out', 'from': who, 'group': group,
                        'chat_name': group or self.names.get(chat) or '', 'text': text[:20000], 'at': ts})
        return out

    def once(self, dry_run=False, max_batches=None):
        last, sent, batches = self._last(), 0, 0
        while True:
            rows = self.rows(last, TG_BATCH)
            if not rows:
                break
            items = self.items(rows)
            if dry_run:
                for it in items[:8]:
                    print(json.dumps(it, ensure_ascii=False)[:300])
                print('first batch: %d rows, %d messages, ids %d-%d' % (len(rows), len(items), rows[0][0], rows[-1][0]))
                return None
            if items:
                self.api.call('POST', '/bridge/v1/telegram', {'messages': items}, timeout=120)
                sent += len(items)
            last = rows[-1][0]
            self._save(last)
            batches += 1
            if max_batches and batches >= max_batches:
                break
        return sent

    def loop(self, stopping):
        while not stopping.is_set():
            try:
                n = self.once(max_batches=20)
                if n:
                    log('telegram history: copied', n, 'messages')
            except Exception as exc:
                log('telegram history sync failed:', exc)
            stopping.wait(60)


# ------------------------------------------------------------------ live activity (read-only)
ACT_KEEP = 40          # recent steps sent to the chat server
ACT_TEXT = 1500        # characters kept per step (thinking can be long)


def _short(s, n):
    s = ' '.join(str(s or '').split())
    return s if len(s) <= n else s[:n - 1] + '…'


def act_step(d):
    """One Claude Code session log line -> a list of short steps {at, kind, text}."""
    out = []
    at = d.get('timestamp') or ''
    msg = d.get('message') or {}
    content = msg.get('content')
    role = msg.get('role') or d.get('type')
    if isinstance(content, str):
        if role == 'user' and content.strip():
            out.append({'at': at, 'kind': 'input', 'text': _short(TG_REPLY_VIA.sub('', content), ACT_TEXT)})
        return out
    if not isinstance(content, list):
        return out
    for x in content:
        if not isinstance(x, dict):
            continue
        t = x.get('type')
        if t == 'thinking' and x.get('thinking'):
            out.append({'at': at, 'kind': 'thinking', 'text': _short(x['thinking'], ACT_TEXT)})
        elif t == 'text' and role == 'assistant' and x.get('text', '').strip():
            out.append({'at': at, 'kind': 'text', 'text': _short(x['text'], ACT_TEXT)})
        elif t == 'tool_use':
            inp = x.get('input') or {}
            what = inp.get('description') or inp.get('command') or inp.get('file_path') or inp.get('pattern') or inp.get('url') \
                or inp.get('prompt') or inp.get('query') or ''
            out.append({'at': at, 'kind': 'tool', 'tool': x.get('name', ''), 'text': _short(what, 300)})
        elif t == 'text' and role == 'user' and x.get('text', '').strip():
            out.append({'at': at, 'kind': 'input', 'text': _short(TG_REPLY_VIA.sub('', x['text']), ACT_TEXT)})
    return out


class Activity:
    """Sends what the agent is doing right now (its Claude session log: messages in, thinking, tool use)."""

    def __init__(self, cfg, api):
        self.cfg, self.api = cfg, api
        a = cfg.get('activity') or {}
        self.on = a.get('enabled', False)
        self.root = Path(a.get('projects') or (Path.home() / '.claude' / 'projects'))
        self.path, self.pos, self.steps, self.sent_sig = None, 0, [], None

    def _latest(self):
        try:
            files = [p for p in self.root.glob('*/*.jsonl')]
        except OSError:
            return None
        return max(files, key=lambda p: p.stat().st_mtime) if files else None

    def poll(self):
        f = self._latest()
        if not f:
            return
        if f != self.path:
            self.path, self.steps = f, []
            self.pos = max(0, f.stat().st_size - 400000)  # start near the end of a new log
        size = f.stat().st_size
        if size < self.pos:
            self.pos = 0
        if size > self.pos:
            with open(f, 'rb') as fh:
                fh.seek(self.pos)
                chunk = fh.read(size - self.pos)
            last_nl = chunk.rfind(b'\n')
            if last_nl < 0:
                return
            self.pos += last_nl + 1
            for line in chunk[:last_nl].splitlines():
                try:
                    self.steps.extend(act_step(json.loads(line)))
                except (ValueError, AttributeError):
                    continue
            self.steps = self.steps[-ACT_KEEP:]
        mtime = f.stat().st_mtime
        sig = (len(self.steps), self.steps[-1]['at'] if self.steps else '', int(mtime))
        if sig != self.sent_sig:
            self.api.call('POST', '/bridge/v1/activity', {'updated': mtime, 'steps': self.steps}, timeout=30)
            self.sent_sig = sig

    def loop(self, stopping):
        while not stopping.is_set():
            try:
                self.poll()
            except Exception as exc:
                log('activity update failed:', exc)
                stopping.wait(30)
            stopping.wait(3)


# ------------------------------------------------------------------ Hermes
class Hermes:
    def __init__(self, cfg, api):
        self.cfg, self.api = cfg, api
        self.h = cfg.get('hermes') or {}
        self.mode = self.h.get('mode', 'cli')
        self.bin = self.h.get('bin') or shutil.which('hermes') or '/usr/local/bin/hermes'
        self.timeout = int(self.h.get('timeout', 900))
        state = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'agent-hq-chat-bridge'
        state.mkdir(parents=True, exist_ok=True)
        self.state_file = state / 'hermes-sessions.json'
        self.lock = threading.Lock()
        self.quiet_ok = None

    def info(self):
        return {'hermes_mode': self.mode, 'hermes_bin': self.bin}

    def _sessions(self):
        try:
            return json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            return {}

    def _remember(self, conv_id, session):
        with self.lock:
            s = self._sessions()
            s[str(conv_id)] = session
            tmp = self.state_file.with_suffix('.tmp')
            tmp.write_text(json.dumps(s))
            tmp.replace(self.state_file)

    def handle(self, job, cancelled):
        self.api.call('POST', '/bridge/v1/deliveries/%d/working' % job['delivery_id'], {})
        text = self._api(job, cancelled) if self.mode == 'api' else self._cli(job, cancelled)
        if text is None:
            return  # cancelled
        if not text.strip():
            raise RuntimeError('Hermes gave an empty answer')
        self.api.call('POST', '/bridge/v1/deliveries/%d/reply' % job['delivery_id'], {'text': text.strip(), 'final': True})

    def _cli(self, job, cancelled, fresh=False):
        conv = str(job['conversation']['id'])
        session = None if fresh else self._sessions().get(conv)
        prompt = job['prompt']
        if not session and job['history']:
            past = '\n'.join('%s: %s' % ('You' if h['role'] == 'assistant' else h['name'], h['text'][:1500]) for h in job['history'][-12:])
            prompt = 'Earlier in this conversation:\n' + past + '\n\n' + prompt
        # The prompt goes in on stdin (--query-file -), so its length and quoting don't matter.
        cmd = [self.bin, 'chat', '--query-file', '-'] + (['--resume', session] if session else [])
        if self.quiet_ok is not False:
            cmd.append('-Q')
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE, text=True,
                                cwd=str(Path.home()), start_new_session=True)
        result = {}
        reader = threading.Thread(target=lambda: result.update(zip(('out', 'err'), proc.communicate(prompt))), daemon=True)
        reader.start()
        deadline = time.time() + self.timeout
        while reader.is_alive():
            if cancelled.is_set() or time.time() > deadline:
                os.killpg(proc.pid, signal.SIGTERM)
                reader.join(10)
                if cancelled.is_set():
                    return None
                raise RuntimeError('Hermes took longer than %d seconds' % self.timeout)
            reader.join(0.5)
        out, err = result.get('out', ''), result.get('err', '')
        if proc.returncode != 0 and '-Q' in cmd and ('unrecognized' in err or 'no such option' in err.lower()):
            self.quiet_ok = False
            return self._cli(job, cancelled, fresh)
        if proc.returncode != 0 and session:
            self._remember(conv, '')  # the old Hermes session may be gone; try once with a new one
            return self._cli(job, cancelled, fresh=True)
        if proc.returncode != 0:
            raise RuntimeError('hermes exited with %d: %s' % (proc.returncode, ANSI.sub('', err or out).strip()[-300:]))
        if '-Q' in cmd:
            self.quiet_ok = True
        both = ANSI.sub('', out + '\n' + err)
        for pattern in SESSION_PATTERNS:
            found = pattern.findall(both)
            if found:
                self._remember(conv, found[-1])
                break
        lines = [l for l in ANSI.sub('', out).splitlines() if not HERMES_NOISE.match(l)]
        return '\n'.join(lines).strip()

    def _api(self, job, cancelled):
        url = (self.h.get('api_url') or 'http://127.0.0.1:8642/v1').rstrip('/') + '/chat/completions'
        messages = []
        for h in job['history']:
            messages.append({'role': h['role'], 'content': h['text'] if h['role'] == 'assistant' else '%s: %s' % (h['name'], h['text'])})
        messages.append({'role': 'user', 'content': job['prompt']})
        body = json.dumps({'model': self.h.get('model') or 'hermes-agent', 'messages': messages, 'stream': True}).encode()
        headers = {'Content-Type': 'application/json'}
        if self.h.get('api_key'):
            headers['Authorization'] = 'Bearer ' + self.h['api_key']
        headers['X-Hermes-Session-Id'] = 'agent-hq-chat-%s' % job['conversation']['id']
        req = urllib.request.Request(url, data=body, method='POST', headers=headers)
        text, last = '', 0.0
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            for raw in resp:
                if cancelled.is_set():
                    return None
                line = raw.decode('utf-8', 'replace').strip()
                if not line.startswith('data:'):
                    continue
                data = line[5:].strip()
                if data == '[DONE]':
                    break
                try:
                    text += json.loads(data)['choices'][0].get('delta', {}).get('content') or ''
                except (ValueError, KeyError, IndexError):
                    continue
                if text.strip() and time.time() - last > 0.6:
                    last = time.time()
                    r = self.api.call('POST', '/bridge/v1/deliveries/%d/reply' % job['delivery_id'], {'text': text, 'final': False})
                    if r.get('cancelled'):
                        return None
        return text


# ------------------------------------------------------------------ main loop
def run(cfg):
    api = Api(cfg)
    runtime = {'zylos': Zylos, 'hermes': Hermes}.get(cfg['kind'])
    if not runtime:
        sys.exit('kind must be zylos or hermes')
    runtime = runtime(cfg, api)
    info = {'bridge': VERSION, 'kind': cfg['kind'], 'host': platform.node(), 'python': sys.version.split()[0], **runtime.info()}
    active, lock = {}, threading.Lock()
    slots = threading.Semaphore(int(cfg.get('parallel', 2)))
    backoff = 1
    hello_at = 0
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    log('agent-hq-chat-bridge', VERSION, 'kind', cfg['kind'], 'server', cfg['server'])
    if cfg['kind'] == 'zylos':
        tg = TelegramSync(cfg, api)
        if tg.enabled():
            threading.Thread(target=tg.loop, args=(stopping,), daemon=True).start()
        act = Activity(cfg, api)
        if act.on:
            threading.Thread(target=act.loop, args=(stopping,), daemon=True).start()

    def work(job):
        cancelled = threading.Event()
        with lock:
            active[job['delivery_id']] = cancelled
        try:
            log('delivery', job['delivery_id'], 'conversation', job['conversation']['id'], 'from', job['from']['name'])
            runtime.handle(job, cancelled)
        except Exception as exc:
            log('delivery', job['delivery_id'], 'failed:', exc)
            try:
                api.call('POST', '/bridge/v1/deliveries/%d/fail' % job['delivery_id'], {'error': str(exc)[:400]})
            except Exception as exc2:
                log('could not report the failure:', exc2)
        finally:
            with lock:
                active.pop(job['delivery_id'], None)
            slots.release()

    while not stopping.is_set():
        try:
            if time.time() - hello_at > 300:
                me = api.call('POST', '/bridge/v1/hello', {'info': info})
                if not hello_at:
                    log('connected as', me['agent']['name'], '@' + me['agent']['handle'])
                hello_at = time.time()
            got = api.call('GET', '/bridge/v1/poll?wait=25', timeout=45)
            backoff = 1
            with lock:
                for did in got.get('cancelled', []):
                    if did in active:
                        active[did].set()
            for job in got.get('deliveries', []):
                slots.acquire()
                threading.Thread(target=work, args=(job,), daemon=True).start()
        except Exception as exc:
            log('connection problem:', exc, '- retrying in %ds' % backoff)
            stopping.wait(backoff)
            backoff = min(60, backoff * 2)
    log('stopped')


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ('run', 'check', 'send', 'fetch', 'tg-sync', 'activity'):
        sys.exit(__doc__)
    cfg = load_config()
    if sys.argv[1] == 'run':
        run(cfg)
    elif sys.argv[1] == 'check':
        me = Api(cfg).call('POST', '/bridge/v1/hello', {'info': {'bridge': VERSION, 'check': 'yes'}})
        print('OK: connected as %s (@%s), server %s' % (me['agent']['name'], me['agent']['handle'], me['server']))
        if cfg['kind'] == 'zylos':
            print('c4-receive:', find_receive(cfg) or 'NOT FOUND')
    elif sys.argv[1] == 'activity':
        a = Activity(cfg, None)
        a.api = type('Print', (), {'call': staticmethod(lambda m, p, d, timeout=0: [print(x['kind'], x.get('tool', ''), x['text'][:150]) for x in d['steps'][-15:]])})()
        a.poll()
    elif sys.argv[1] == 'tg-sync':
        tg = TelegramSync(cfg, Api(cfg))
        if not tg.db.is_file():
            sys.exit('no Zylos log at %s' % tg.db)
        n = tg.once(dry_run='--dry-run' in sys.argv)
        if n is not None:
            print('copied %d messages' % n)
    elif sys.argv[1] == 'fetch':
        if len(sys.argv) != 4:
            sys.exit('usage: fetch <file-url> <path>')
        Api(cfg).download(sys.argv[2], sys.argv[3])
        print('saved', sys.argv[3])
    else:
        if len(sys.argv) < 4:
            sys.exit('usage: send <endpoint> <text...>  (or "-" to read stdin)')
        text = sys.stdin.read() if sys.argv[3] == '-' else ' '.join(sys.argv[3:])
        r = Api(cfg).call('POST', '/bridge/v1/send', {'endpoint': sys.argv[2], 'text': text})
        print('sent' if r.get('message_id') else 'not posted (cancelled or empty)')


if __name__ == '__main__':
    main()
