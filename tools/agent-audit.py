#!/usr/bin/env python3
# Read-only model/routing/usage audit, run as root inside one agent container.
# Secret-looking values are masked. Nothing is written.
import os, re, json, glob, time, sqlite3, subprocess, collections

NOW = time.time()
DAYS = 14
SINCE = NOW - DAYS * 86400
SECRET = re.compile(r'(key|token|secret|password|passwd|auth|cookie|credential|bearer)', re.I)
SHOWOK = re.compile(r'(model|base_?url|provider|endpoint|host|url)$', re.I)

def mask_val(k, v):
    if isinstance(v, (dict, list)):
        return mask(v)
    if SECRET.search(str(k)) and not SHOWOK.search(str(k)):
        return '<masked>' if v not in (None, '', False) else v
    if isinstance(v, str) and re.search(r'(sk-[A-Za-z0-9_-]{8,}|eyJ[A-Za-z0-9_-]{20,}|[A-Za-z0-9]{40,})', v):
        return '<masked>'
    return v

def mask(o):
    if isinstance(o, dict):
        return {k: mask_val(k, v) for k, v in o.items()}
    if isinstance(o, list):
        return [mask(x) for x in o][:40]
    return o

def sh(cmd, user=None, t=20):
    if user and user != 'root':
        cmd = ['runuser', '-u', user, '--', 'bash', '-lc', cmd]
    else:
        cmd = ['bash', '-lc', cmd]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=t, stdin=subprocess.DEVNULL)
        return (r.stdout + r.stderr).strip()
    except Exception as e:
        return f'ERR {e}'

def mask_text(s):
    s = re.sub(r'((?:KEY|TOKEN|SECRET|PASSWORD)[A-Z_]*\s*[=:]\s*)["\']?[^\s"\']+', r'\1<masked>', s, flags=re.I)
    s = re.sub(r'(sk-[A-Za-z0-9_-]{8,}|eyJ[A-Za-z0-9_.-]{20,})', '<masked>', s)
    return s

def head(path, n=60, maxc=4000):
    try:
        with open(path, errors='replace') as f:
            return mask_text(''.join(f.readlines()[:n]))[:maxc]
    except Exception as e:
        return f'ERR {e}'

out = {}
out['hostname'] = sh('hostname')
out['services'] = sh("systemctl list-units --type=service --state=running --no-pager --no-legend | awk '{print $1}' | grep -v -E '^(systemd|dbus|cron|ssh|getty|console|postfix|rsyslog|networking|ifupdown|user@|chrony|avahi|wpa|tailscale|container-getty|serial)' | head -40")

homes = ['/root'] + sorted(glob.glob('/home/*'))
users = {}
for h in homes:
    if not os.path.isdir(h):
        continue
    u = 'root' if h == '/root' else os.path.basename(h)
    info = {}
    # Claude Code settings
    for p in ['.claude/settings.json', '.claude/settings.local.json', 'zylos/.claude/settings.json']:
        fp = os.path.join(h, p)
        if os.path.exists(fp):
            try:
                d = json.load(open(fp))
                info[p] = mask({k: d[k] for k in d if k in ('model', 'env', 'apiKeyHelper', 'permissions', 'hooks', 'enabledPlugins', 'alwaysThinkingEnabled', 'statusLine')})
                if 'permissions' in info[p]:
                    info[p]['permissions'] = '<present>'
                if 'hooks' in info[p]:
                    info[p]['hooks'] = sorted(d['hooks'].keys()) if isinstance(d['hooks'], dict) else '<present>'
            except Exception as e:
                info[p] = f'ERR {e}'
    cj = os.path.join(h, '.claude.json')
    if os.path.exists(cj):
        try:
            d = json.load(open(cj))
            oa = d.get('oauthAccount') or {}
            info['claude.json'] = {'account': oa.get('emailAddress'), 'org': oa.get('organizationName'),
                                   'billing': oa.get('billingType'), 'mcpServers_user': sorted((d.get('mcpServers') or {}).keys())}
        except Exception as e:
            info['claude.json'] = f'ERR {e}'
    info['has_credentials'] = os.path.exists(os.path.join(h, '.claude/.credentials.json'))
    # launch scripts
    for p in ['start-claude.sh', 'zylos/start-claude.sh', '.bashrc', '.profile', '.config/9router.env']:
        fp = os.path.join(h, p)
        if os.path.exists(fp):
            txt = open(fp, errors='replace').read()
            lines = [l for l in txt.splitlines() if re.search(r'(ANTHROPIC|CLAUDE_|MODEL|--model|claude |claude-9r|OPENAI|OPENROUTER|BASE_URL|exec )', l)]
            if lines:
                info['launch:' + p] = mask_text('\n'.join(lines[:25]))
    # zylos model routing
    for mr in glob.glob(os.path.join(h, 'zylos', 'model-routing')) + glob.glob(os.path.join(h, '*', 'model-routing')):
        files = []
        for root, dirs, fs in os.walk(mr):
            dirs[:] = [x for x in dirs if x not in ('node_modules', '.git')]
            for f in fs:
                files.append(os.path.relpath(os.path.join(root, f), mr))
        info['model-routing:' + mr] = {'files': files[:60]}
        for f in files:
            if re.search(r'\.(json|ya?ml|md|toml|conf|env|txt)$', f) and os.path.getsize(os.path.join(mr, f)) < 20000:
                info['model-routing:' + mr][f] = head(os.path.join(mr, f), 80, 3500)
    # openclaw / hermes / codex / other harness configs
    for p in ['.openclaw/openclaw.json', '.clawdbot/clawdbot.json', '.moltbot/moltbot.json']:
        fp = os.path.join(h, p)
        if os.path.exists(fp):
            try:
                d = json.load(open(fp))
                keep = {k: d.get(k) for k in ('agents', 'models', 'agent', 'routing', 'channels') if k in d}
                if 'channels' in keep and isinstance(keep['channels'], dict):
                    keep['channels'] = sorted(keep['channels'].keys())
                info[p] = mask(keep)
            except Exception as e:
                info[p] = head(fp, 80)
    hc = os.path.join(h, '.hermes/config.yaml')
    if os.path.exists(hc):
        txt = open(hc, errors='replace').read()
        m = re.search(r'^model:.*?(?=^\S)', txt, re.S | re.M)
        info['hermes.model'] = mask_text(m.group(0) if m else txt[:800])[:1500]
        m2 = re.search(r'^(smart_model_routing|delegation|auxiliary|fallback_model|fallback_providers).*?(?=^\S)', txt, re.S | re.M)
        info['hermes.other'] = mask_text('\n'.join(x.group(0) for x in re.finditer(r'^(smart_model_routing|delegation|auxiliary|fallback_model|fallback_providers|compression):.*?(?=^\S)', txt, re.S | re.M)))[:2500]
    for p in ['.codex/config.toml', '.config/opencode/opencode.json', '.gemini/settings.json']:
        fp = os.path.join(h, p)
        if os.path.exists(fp):
            info[p] = head(fp, 40, 1500)
    # Claude Code usage from transcripts, last 14 days
    usage = collections.defaultdict(lambda: [0, 0, 0, 0, 0])  # msgs, in, out, cache_read, cache_write
    tools = collections.Counter()
    projects = collections.Counter()
    sessions = set()
    side = 0
    days = collections.Counter()
    for fp in glob.glob(os.path.join(h, '.claude/projects/*/*.jsonl')) + glob.glob(os.path.join(h, '.claude/projects/*/*/*.jsonl')) + glob.glob(os.path.join(h, '.claude/projects/*/*/*/*.jsonl')):
        try:
            if os.path.getmtime(fp) < SINCE:
                continue
        except OSError:
            continue
        proj = fp.split('/.claude/projects/')[1].split('/')[0]
        try:
            with open(fp, errors='replace') as f:
                for line in f:
                    if '"assistant"' not in line:
                        continue
                    try:
                        e = json.loads(line)
                    except Exception:
                        continue
                    if e.get('type') != 'assistant':
                        continue
                    ts = e.get('timestamp')
                    try:
                        t = time.mktime(time.strptime(ts[:19], '%Y-%m-%dT%H:%M:%S'))
                    except Exception:
                        t = NOW
                    if t < SINCE:
                        continue
                    msg = e.get('message') or {}
                    u2 = msg.get('usage') or {}
                    mdl = msg.get('model') or '?'
                    r = usage[mdl]
                    r[0] += 1
                    r[1] += u2.get('input_tokens', 0) or 0
                    r[2] += u2.get('output_tokens', 0) or 0
                    r[3] += u2.get('cache_read_input_tokens', 0) or 0
                    r[4] += u2.get('cache_creation_input_tokens', 0) or 0
                    if e.get('isSidechain'):
                        side += 1
                    sessions.add(e.get('sessionId'))
                    projects[proj] += 1
                    days[ts[:10]] += 1
                    for c in msg.get('content') or []:
                        if isinstance(c, dict) and c.get('type') == 'tool_use':
                            tools[c.get('name', '?')] += 1
        except Exception:
            continue
    if usage:
        info['cc_usage_14d'] = {m: dict(zip(['msgs', 'in', 'out', 'cache_read', 'cache_write'], v)) for m, v in usage.items()}
        info['cc_sessions_14d'] = len(sessions)
        info['cc_subagent_msgs'] = side
        info['cc_top_tools'] = tools.most_common(25)
        info['cc_projects'] = projects.most_common(8)
        info['cc_days'] = sorted(days.items())[-14:]
    # Zylos scheduler tasks
    for db in glob.glob(os.path.join(h, 'zylos/scheduler/**/*.db'), recursive=True) + glob.glob(os.path.join(h, 'zylos/**/scheduler*.db'), recursive=True):
        try:
            con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
            tabs = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
            rep = {'tables': tabs}
            for t in tabs:
                cols = [r[1] for r in con.execute(f'pragma table_info("{t}")')]
                n = con.execute(f'select count(*) from "{t}"').fetchone()[0]
                rep[t] = {'cols': cols, 'rows': n}
                if re.search(r'task|job|schedule', t, re.I) and n:
                    want = [c for c in cols if re.search(r'name|title|cron|schedule|interval|prompt|desc|status|enabled|model|next|last|type|priority', c, re.I)][:10]
                    if want:
                        rows = con.execute(f'select {",".join(chr(34)+c+chr(34) for c in want)} from "{t}" limit 60').fetchall()
                        rep[t]['sample'] = [[mask_text(str(x))[:220] for x in r] for r in rows]
            info['scheduler:' + db] = rep
        except Exception as e:
            info['scheduler:' + db] = f'ERR {e}'
    # c4 comm-bridge channel mix 14d
    for db in glob.glob(os.path.join(h, 'zylos/comm-bridge/*.db')):
        try:
            con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
            tabs = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
            rep = {}
            for t in tabs:
                cols = [r[1] for r in con.execute(f'pragma table_info("{t}")')]
                if 'channel' in cols:
                    tcol = next((c for c in cols if c in ('timestamp', 'created_at', 'ts', 'time')), None)
                    q = f'select channel, {"direction," if "direction" in cols else ""} count(*) from "{t}"'
                    if tcol:
                        q += f" where {tcol} >= {int(SINCE)} or {tcol} >= '{time.strftime('%Y-%m-%d', time.gmtime(SINCE))}'"
                    q += ' group by 1' + (',2' if 'direction' in cols else '')
                    rep[t] = con.execute(q).fetchall()
            info['c4:' + os.path.basename(db)] = rep
        except Exception as e:
            info['c4'] = f'ERR {e}'
    # pm2 apps
    if os.path.isdir(os.path.join(h, '.pm2')):
        j = sh('pm2 jlist 2>/dev/null', u, 25)
        try:
            apps = json.loads(j[j.index('['):])
            info['pm2'] = [{'name': a.get('name'), 'status': (a.get('pm2_env') or {}).get('status'),
                            'script': (a.get('pm2_env') or {}).get('pm_exec_path'), 'args': mask_text(str((a.get('pm2_env') or {}).get('args')))[:200],
                            'cwd': (a.get('pm2_env') or {}).get('pm_cwd')} for a in apps]
        except Exception:
            info['pm2'] = j[:600]
    # tmux / running claude processes
    if info:
        users[u] = info
out['users'] = users
out['claude_procs'] = mask_text(sh("ps -eo user,etimes,args --no-headers | grep -E '(claude|hermes|openclaw|codex|node .*bot)' | grep -v grep | cut -c1-260 | head -15"))
print(json.dumps(out, default=str, indent=1))
