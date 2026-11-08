import json
import hmac
import math
import queue
import re
import secrets
import threading
import time
from contextlib import closing
from dataclasses import asdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, unquote
from .config import Config, SourceCfg, Account, Price, _path, save_config
from .auth import Locked
from .accounts import Accounts, label_of, required_permission
from .control.audit import CURRENT as REQUEST_USER
from .fleet import fleet_overview, load_fleet, rolling_window
from .office import office_snapshot
from .insights import insights
from .control.pve import ControlError, Pve
from .control.create import Creator
from .control.settings import Settings
import os
from .control.audit import ControlLog
from .control.terminal import bridge
from .control.updates import Updates
from .control.memory import Memory
from .watch import AlertSettings, Watchman
from .infra import Infra
from .router import KeyAssignments, Router
from .mcp import SCOPES as MCP_SCOPES, AgentKeys, McpServer
from .remote.config import load_hosts
from .kpis import compute_kpis, resolve_range, session_value, _rows, series
from . import limits as limits_module
from .limits import limit_cards, alerts, add_manual, recommend_bridge
from .status import last_kinds, session_status
import sqlite3
from .run.service import RunError

def validate_settings(data, sources, relabel=None):
    if data['sources'] != {k:{'enabled':v.enabled,'paths':[str(p) for p in v.paths]} for k,v in sources.items()}:
        raise ValueError('sources require a restart')
    def number(x): return type(x) in (int,float) and math.isfinite(x) and x >= 0
    accounts=data['accounts']; prices=data['prices']; alert=data['alerts']
    if not isinstance(accounts,list) or not isinstance(prices,dict) or not isinstance(alert,dict): raise ValueError('invalid settings')
    for a in accounts:
        if not isinstance(a,dict) or not all(isinstance(a[k],str) for k in ('id','provider','label')) or not (a['plan'] is None or isinstance(a['plan'],str)) or not (a['monthly_price_usd'] is None or number(a['monthly_price_usd'])) or type(a['billing_day']) is not int or not 1<=a['billing_day']<=31: raise ValueError('invalid account')
        if not isinstance(a['match'],list) or not all(isinstance(r,dict) and all(isinstance(k,str) and isinstance(v,str) for k,v in r.items()) for r in a['match']): raise ValueError('invalid match')
        if not isinstance(a['cap_tokens'],dict) or not all(isinstance(k,str) and type(v) is int and v>=0 for k,v in a['cap_tokens'].items()): raise ValueError('invalid caps')
    for k,v in prices.items():
        if not isinstance(k,str) or not isinstance(v,dict) or set(v)!={'input','output','cache_read','cache_write'} or not all(number(x) for x in v.values()): raise ValueError('invalid price')
    if not isinstance(alert.get('thresholds'),list) or not all(number(x) and x<=100 for x in alert['thresholds']) or type(alert.get('toast')) is not bool: raise ValueError('invalid alerts')
    if data['default_skin'] not in ('isometric','topdown','tower') or not isinstance(data['recommend_task_kind'],str): raise ValueError('invalid settings')
    previews = data.get('zylos_previews', False)
    deepseek_alert = data.get('deepseek_alert', 5.0)
    if type(previews) is not bool or not number(deepseek_alert):
        raise ValueError('invalid source settings')
    return Config(sources,[Account(**a) for a in accounts],{k:Price(**v) for k,v in prices.items()},alert,data['default_skin'],data['recommend_task_kind'], relabel or [], previews, deepseek_alert)

def timeline_events(conn, start=None, end=None):
    end = time.time() if end is None else float(end)
    start = end - 86400 if start is None else float(start)
    if not math.isfinite(start) or not math.isfinite(end) or end < start or end - start > 7 * 86400:
        raise ValueError('invalid timeline range')
    events = _rows(conn, """select e.session_id, e.ts, e.kind, e.tool, s.harness
        from events e left join sessions s on s.id=e.session_id
        where e.ts>=? and e.ts<=? order by e.ts, e.id limit 20001""", (start, end))
    return {'events': events[:20000], 'truncated': len(events) > 20000}

FLEET_CACHE_S = 15
OFFICE_CACHE_S = 5
INSIGHTS_CACHE_S = 60

def make_server(store,collector,cfg,config_dir,port=7420,static_dir=None,token: str | None = None,run_service=None,remote_collector=None,public_url=None):
    run_key = secrets.token_urlsafe(32)
    login = Accounts(store.path.parent)  # users, roles and sessions; the old login.json becomes user "admin"
    login_log = store.path.parent / 'login-changes.jsonl'
    hosts, _ = load_hosts(Path(config_dir))
    pve = Pve(store.path.parent / '.ssh' / 'pve_ed25519', store.path.parent / '.ssh' / 'pve_known_hosts', hosts)
    control = ControlLog(store.path.parent / 'control-log.jsonl')
    updates = Updates(pve, store.path.parent / 'updates.json', control)
    def register_agent(info):
        """Add a newly created agent to hosts.json, fleet.json and HQ's trusted SSH keys, and start watching it."""
        from .remote.config import _host
        entry = {'name': info['name'], 'ssh': info['ssh'], 'os': 'linux', 'interval_s': 300, 'sources': ['zylos_metrics', 'claude_code', 'claude_limits']}
        new_host = _host(entry)
        def rewrite(path, change):
            data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            change(data)
            tmp = path.with_suffix('.tmp'); tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8'); os.replace(tmp, path)
        rewrite(Path(config_dir) / 'hosts.json', lambda d: d.setdefault('hosts', []).append(entry) if not any(h.get('name') == entry['name'] for h in d.get('hosts', [])) else None)
        rewrite(Path(config_dir) / 'fleet.json', lambda d: d.setdefault('agents', {}).__setitem__(info['name'], {'label': info['label'], 'owner': info['owner'], 'group': info['group']}))
        known = Path.home() / '.ssh' / 'known_hosts'
        known.parent.mkdir(parents=True, exist_ok=True)
        with open(known, 'a', encoding='utf-8') as stream: stream.write(f"{info['ip']} {info['host_key']}\n")
        pve.add_agent(info['name'], info['ssh'])
        pve.forget()
        if remote_collector is not None: remote_collector.add_host(new_host)
        with fleet_lock: fleet_cache.clear()
        changed()
    creator = Creator(pve, control, register_agent)
    agent_settings = Settings(pve, control)
    watchdog_stop = threading.Event()
    workers = []

    def start_worker(target):
        worker = threading.Thread(target=target, args=(watchdog_stop,), daemon=True)
        workers.append(worker)
        worker.start()

    start_worker(updates.watch)
    memory = Memory(pve, control)
    alert_settings = AlertSettings(store.path.parent / 'alerts.json')
    def office_now():
        with closing(store.conn()) as conn:
            return office_snapshot(conn, time.time(), remote_collector.health() if remote_collector else {}, load_fleet(config_dir))
    HANDS_ON = ('restart_claude', 'terminal_open', 'power_stop', 'power_shutdown', 'power_reboot', 'message_sent', 'rollback')
    def hands_off(host):
        """An update is running on it, or a person controlled it in the last 30 minutes."""
        with updates._lock:
            if any(j['host'] == host and j['state'] == 'running' for j in updates.jobs.values()): return True
        cutoff = time.time() - 1800
        return any(e.get('host') == host and e.get('at', 0) > cutoff and e.get('action') in HANDS_ON for e in control.recent(300))
    def infra_agents():
        labels = load_fleet(config_dir).get('agents', {})
        return {found['vmid']: {'host': name, 'label': (labels.get(name) or {}).get('label') or name} for name, found in pve.agents.items()}
    infra = Infra(pve, infra_agents)
    start_worker(infra.run)
    router = Router(pve)  # 9router on its own container, read-only
    router_keys = KeyAssignments(store.path.parent / 'router-keys.json')
    start_worker(router.run)
    watchman = Watchman(alert_settings, pve, control, office_now, brand=lambda: load_fleet(config_dir).get('brand') or 'Agent HQ', hands_off=hands_off,
                        infra=lambda: infra.snapshot()[0], router=lambda: router.snapshot()[0])
    start_worker(watchman.run)

    # AI agents use HQ through MCP at /mcp, each with its own key (see hq/mcp.py).
    agent_keys = AgentKeys(store.path.parent / 'agent-keys.json')
    def fleet_now(name):
        now = time.time()
        with fleet_lock:
            cached = fleet_cache.get(name)
            if cached and now - cached[0] < FLEET_CACHE_S: return with_plan(cached[1])
            with closing(store.conn()) as conn:
                answer = fleet_overview(conn, cfg, rolling_window(name, now), now, remote_collector.health() if remote_collector else {}, load_fleet(config_dir))
            fleet_cache[name] = (now, answer)
            return with_plan(answer)
    def mcp_send(host, text):
        pve.send_text(host, text)
        control.log({'action': 'message_sent', 'host': host, 'chars': len(text), 'by': 'mcp'})
    def mcp_restart(host):
        pve.restart_claude(host)
        control.log({'action': 'restart_claude', 'host': host, 'by': 'mcp'})
    mcp = McpServer(agent_keys, {'fleet': fleet_now, 'infra': lambda: infra.snapshot()[0], 'watch': watchman.summary,
                                 'updates': updates.summary, 'router': lambda: router.snapshot()[0], 'send': mcp_send, 'restart': mcp_restart}, control)

    def with_plan(answer):
        """Add this week's Claude plan use from 9router: the fleet's % of its weekly limits, and each agent's share.
        A copy, so the cached answer isn't changed."""
        snap = router.snapshot()[0] or {}
        plan = snap.get('plan')
        if not plan: return answer
        by_vmid = plan.get('by_vmid') or {}
        def vmid(host):
            m = re.match(r'ct(\d+)', host or '')
            return int(m.group(1)) if m else None
        agents = [{**a, **{k: (by_vmid.get(vmid(a.get('host'))) or {}).get(k) for k in ('plan_pct', 'cost_week', 'api_paid')}} for a in answer.get('agents', [])]
        return {**answer, 'agents': agents, 'plan': {k: plan.get(k) for k in ('billing', 'used_pct', 'unattributed_pct', 'plan_accounts', 'api_cost_week')}
                | {'accounts': [{k: a.get(k) for k in ('name', 'plan', 'used_pct', 'resets_at', 'week_start', 'cost_week')} for a in plan.get('accounts') or []]}}
    fleet_cache = {}; fleet_lock = threading.Lock()
    public_url = public_url.rstrip('/') if public_url else None
    public_host = urlsplit(public_url).netloc if public_url else None
    token=secrets.token_urlsafe(32) if token is None else token; listeners=set(); lock=threading.Lock(); install_lock=threading.Lock()
    source=cfg.sources.get('provider_usage')
    tracker_path=Path(source.paths[0]) if source and source.enabled and source.paths else None
    if tracker_path and tracker_path.name == 'usage.db': tracker_path=tracker_path.parent
    def recommend(kind): return recommend_bridge(tracker_path,kind) if tracker_path else None
    def changed(event=None):
        with lock:
            for q in listeners: q.put(event if event is not None else time.time())
    if run_service is not None: run_service.on_event = changed
    collector.on_change(changed)
    from .adapters.importer import ImporterAdapter, install
    if not any(a.name == 'importer' for a in collector.adapters):
        collector.adapters.append(ImporterAdapter(store.path.parent / 'tools'))
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def send_json(self,status,data):
            body=json.dumps(data,allow_nan=False).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        def is_public(self): return public_host is not None and self.headers.get('Host')==public_host
        def host_ok(self): return self.is_public() or self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}')
        def origin_ok(self): return self.headers.get('Origin')==(public_url if self.is_public() else f'http://{self.headers.get("Host")}')
        def token_ok(self, value): return isinstance(value,str) and (hmac.compare_digest(value.encode(),token.encode()) or login.valid(value))
        def who(self, value):
            """The username behind a token. The start-up token (this machine only) counts as admin."""
            if isinstance(value,str) and hmac.compare_digest(value.encode(),token.encode()): return 'admin'
            return login.user_for(value)
        def allowed(self, method, path, value, query=None):
            """None when the signed-in user may make this request, else the 403 answer to send."""
            user=self.who(value); REQUEST_USER.user=user
            need=required_permission(method,path,None,query)
            if need is None or (isinstance(value,str) and hmac.compare_digest(value.encode(),token.encode())) or need in login.permissions_of(user): return None
            return {'error':f'You don’t have permission to use {label_of(need)}. Ask an admin to change your role.','permission':need}
        def write_ok(self):
            return self.token_ok(self.headers.get('X-HQ-Token')) and self.origin_ok()
        def read_raw(self):
            # Read once, before any answer: replying while the body is still unread makes some clients
            # (Windows in particular) see a reset connection instead of the reply.
            if getattr(self,'_raw',None) is None:
                try: length=int(self.headers.get('Content-Length','0'))
                except ValueError: length=-1
                if length<0 or length>1048576: self.close_connection=True; raise ValueError('invalid body size')
                self._raw=self.rfile.read(length)
            return self._raw
        def parse_body(self):
            data=json.loads(self.read_raw());
            if not isinstance(data,dict): raise ValueError('expected object')
            return data
        def run_key_ok(self):
            value = self.headers.get('X-HQ-Run-Key', '')
            return hmac.compare_digest(value.encode(), run_key.encode())

        def handle_mcp(self):
            """MCP for AI agents: JSON-RPC in a POST, an agent key in Authorization, never the browser's token."""
            if not self.host_ok(): return self.send_json(403,{'error':'forbidden host'})
            if self.is_public() and self.headers.get('X-Forwarded-Proto')!='https': return self.send_json(403,{'error':'use HTTPS'})
            origin=self.headers.get('Origin')  # browsers send one; agents don't. A browser page elsewhere must not drive HQ.
            if origin is not None and origin!=(public_url if self.is_public() else f'http://{self.headers.get("Host")}'): return self.send_json(403,{'error':'forbidden origin'})
            key=agent_keys.check(self.headers.get('Authorization'))
            if not key:
                body=json.dumps({'error':'an HQ agent key is needed: Authorization: Bearer hqa_...'}).encode()
                self.send_response(401); self.send_header('Content-Type','application/json'); self.send_header('WWW-Authenticate','Bearer'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body); return
            if key.get('limited'): return self.send_json(429,{'error':'too many calls; at most 120 a minute per key'})
            try: message=json.loads(self.read_raw())
            except (ValueError,json.JSONDecodeError): return self.send_json(400,{'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':'not valid JSON'}})
            status,answer=mcp.handle(message,key)
            if answer is None:
                self.send_response(status); self.send_header('Content-Length','0'); self.end_headers(); return
            return self.send_json(status,answer)

        def do_GET(self):
            if not self.host_ok(): return self.send_json(403,{'error':'forbidden host'})
            parsed=urlsplit(self.path); path=parsed.path; query=parse_qs(parsed.query,keep_blank_values=True); now=time.time()
            # MCP over HTTP: HQ answers with JSON and has no event stream to open.
            if path=='/mcp':
                body=b'{"error":"POST MCP requests to this address"}'; self.send_response(405); self.send_header('Allow','POST'); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body); return
            # The app's name is shown on the sign-in page, before anyone has signed in.
            if path=='/api/brand': return self.send_json(200,{'brand':load_fleet(config_dir)['brand']})
            if path.startswith('/api/') and not (self.token_ok(self.headers.get('X-HQ-Token')) or (path in ('/api/stream','/api/terminal') and self.token_ok(query.get('token',[None])[0]))): return self.send_json(403,{'error':'forbidden'})
            REQUEST_USER.user=None
            if path.startswith('/api/'):
                caller_token=query.get('token',[None])[0] if path in ('/api/stream','/api/terminal') and not self.token_ok(self.headers.get('X-HQ-Token')) else self.headers.get('X-HQ-Token')
                refused=self.allowed('GET',path,caller_token,query)
                if refused: return self.send_json(403,refused)
            def param(k,default=None): return query.get(k,[default])[0]
            if path=='/api/me': return self.send_json(200,login.me(self.who(self.headers.get('X-HQ-Token'))))
            if path=='/api/accounts': return self.send_json(200,login.listing(self.who(self.headers.get('X-HQ-Token'))))
            if path=='/api/terminal':
                # Browsers can't add headers to a WebSocket, so the token comes in the address, and the
                # page it was opened from must be HQ itself.
                if not self.origin_ok(): return self.send_json(403,{'error':'forbidden'})
                host,mode,caller=param('host'),param('mode','watch'),param('token')
                if mode not in ('watch','control','root'): return self.send_json(400,{'error':'unknown terminal mode'})
                if mode!='watch' and not control.elevated(caller): return self.send_json(403,{'error':'confirm your password first'})
                try: command=pve.terminal_command(host,mode)
                except ControlError as exc: return self.send_json(409,{'error':str(exc)})
                control.log({'action':'terminal_open','host':host,'mode':mode})
                # Re-checked while it's open: signing out, a password change or the password check running out closes it.
                still=(lambda: self.token_ok(caller)) if mode=='watch' else (lambda: self.token_ok(caller) and control.elevated(caller))
                bridge(self,command,readonly=mode=='watch',allowed=still,on_end=lambda read,written: control.log({'action':'terminal_closed','host':host,'mode':mode,'typed_bytes':written}))
                return
            try:
                if path=='/api/stream':
                    q=queue.Queue()
                    with lock: listeners.add(q)
                    try:
                        self.send_response(200); self.send_header('Content-Type','text/event-stream'); self.send_header('Cache-Control','no-cache'); self.end_headers(); self.wfile.flush()
                        while True:
                            try:
                                event=q.get(timeout=15)
                                msg=f'event: changed\ndata: {json.dumps(event if isinstance(event, dict) else {"ts":event})}\n\n'
                            except queue.Empty: msg=': ping\n\n'
                            self.wfile.write(msg.encode()); self.wfile.flush()
                    except (BrokenPipeError,ConnectionResetError,TimeoutError): pass
                    finally:
                        with lock: listeners.discard(q)
                    return
                if path=='/':
                    index=Path(static_dir)/'index.html' if static_dir else None
                    body=index.read_text(encoding='utf-8') if index and index.is_file() else '<html><head></head><body>UI not built yet</body></html>'
                    data=body.encode(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(data))); self.send_header('Cache-Control','no-cache'); self.end_headers(); self.wfile.write(data); return
                if not path.startswith('/api/') and static_dir:
                    root=Path(static_dir).resolve(); target=(root/unquote(path).lstrip('/')).resolve()
                    if target.is_relative_to(root) and target.is_file():
                        import mimetypes
                        data=target.read_bytes(); self.send_response(200); self.send_header('Content-Type',mimetypes.guess_type(target.name)[0] or 'application/octet-stream'); self.send_header('Content-Length',str(len(data))); self.send_header('Cache-Control','public, max-age=31536000, immutable' if target.parent.name=='assets' else 'no-cache'); self.end_headers(); self.wfile.write(data); return
                if path == '/api/goals' or path.startswith('/api/goals/'):
                    if not self.run_key_ok(): return self.send_json(403, {'error':'forbidden'})
                    if run_service is None: raise RunError(403, 'run mode is off')
                    parts = path.strip('/').split('/')
                    if len(parts) == 2: return self.send_json(200, run_service.goal_list())
                    if len(parts) == 3: return self.send_json(200, run_service.goal_get(unquote(parts[2])))
                    if len(parts) == 4 and parts[3] == 'diff': return self.send_json(200, run_service.goal_diff(unquote(parts[2])))
                if path == '/api/approvals':
                    # Reads need the token (checked above) and the run key; browsers send no Origin
                    # on same-origin GETs. Answers (POST) still require a same-origin Origin.
                    if not self.run_key_ok(): return self.send_json(403, {'error':'reopen Agent HQ from its launch link to use run mode'})
                    if run_service is None: raise RunError(403, 'run mode is off')
                    return self.send_json(200, run_service.approvals_list(param('state', 'waiting')))
                if path == '/api/runs/options': return self.send_json(200, (run_service.options() if run_service else {'run_mode': False, 'tools': {}, 'projects': [], 'profiles': [], 'defaults': {'level':'edit', 'guard_mode':'block', 'time_limit_min':30}, 'max_concurrent':3}) | {'run_key_ok': self.run_key_ok()})
                if path == '/api/runs' or path.startswith('/api/runs/'):
                    if not self.run_key_ok(): return self.send_json(403, {'error':'reopen Agent HQ from its launch link to use run mode'})
                    if run_service is None: raise RunError(403, 'run mode is off')
                    parts = path.strip('/').split('/')
                    if len(parts) == 2: return self.send_json(200, run_service.list())
                    if len(parts) == 3: return self.send_json(200, run_service.get(unquote(parts[2])))
                    if len(parts) == 4 and parts[3] in ('output', 'diff'): return self.send_json(200, getattr(run_service, parts[3])(unquote(parts[2])))
                    run_service._enabled()
                if path=='/api/health': return self.send_json(200,collector.health() | {'recommend': limits_module.recommend_error, 'pu_failures': run_service.pu_failures if run_service else 0, 'hosts': remote_collector.health() if remote_collector else {}})
                if path=='/api/settings':
                    with closing(store.conn()) as conn:
                        models = compute_kpis(conn,cfg,resolve_range('month',datetime.now().astimezone()),None,now)['top_models']
                    return self.send_json(200,{'sources':{k:{'enabled':v.enabled,'paths':[str(p) for p in v.paths]} for k,v in cfg.sources.items()},'accounts':[asdict(a) for a in cfg.accounts],'prices':{k:asdict(v) for k,v in cfg.prices.items()},'alerts':cfg.alerts,'default_skin':cfg.default_skin,'recommend_task_kind':cfg.recommend_task_kind,'zylos_previews':cfg.zylos_previews,'deepseek_alert':cfg.deepseek_alert,'price_sources':{row['model']:cfg.pricebook.source(row['model']) for row in models}})
                if path=='/api/recommend':
                    result=recommend(param('task_kind',cfg.recommend_task_kind))
                    return self.send_json(200,result)
                with closing(store.conn()) as conn:
                    if path=='/api/timeline': return self.send_json(200, timeline_events(conn, param('from'), param('to')))
                    if path=='/api/delegations': return self.send_json(200,_rows(conn,'select ext_id as id, ts, provider, model, task_kind, outcome from delegations order by ts desc, id desc limit ?', (min(200,max(1,int(param('limit','20')))),)))
                    if path=='/api/fleet':
                        name=param('range','today')
                        # Updates arrive every few seconds, so one answer serves them all for a short while.
                        with fleet_lock:
                            cached=fleet_cache.get(name)
                            if cached and now-cached[0]<FLEET_CACHE_S: return self.send_json(200,with_plan(cached[1]))
                            answer=fleet_overview(conn,cfg,rolling_window(name,now),now,remote_collector.health() if remote_collector else {},load_fleet(config_dir))
                            fleet_cache[name]=(now,answer)
                        return self.send_json(200,with_plan(answer))
                    if path=='/api/insights':
                        name=param('range','today')
                        with fleet_lock:
                            cached=fleet_cache.get('insights:'+name)
                            if cached and now-cached[0]<INSIGHTS_CACHE_S: return self.send_json(200,cached[1])
                            answer=insights(conn,rolling_window(name,now),now)
                            fleet_cache['insights:'+name]=(now,answer)
                        return self.send_json(200,answer)
                    if path=='/api/office':
                        with fleet_lock:
                            cached=fleet_cache.get('office')
                            if cached and now-cached[0]<OFFICE_CACHE_S: return self.send_json(200,cached[1])
                            answer=office_snapshot(conn,now,remote_collector.health() if remote_collector else {},load_fleet(config_dir))
                            fleet_cache['office']=(now,answer)
                        return self.send_json(200,answer)
                    if path=='/api/control':
                        # The password check is reported even without Proxmox, since other pages (Router keys) use it too.
                        if not pve.configured(): return self.send_json(200,{'configured':False,'containers':{},'managed':sorted(pve.agents),'elevated_until':control.elevated_until(self.headers.get('X-HQ-Token'))})
                        try: return self.send_json(200,{'configured':True,'containers':pve.containers(),'managed':sorted(pve.agents),'elevated_until':control.elevated_until(self.headers.get('X-HQ-Token'))})
                        except ControlError as exc: return self.send_json(200,{'configured':True,'error':str(exc),'containers':{},'managed':sorted(pve.agents)})
                    if path=='/api/control/session':
                        try: return self.send_json(200,pve.session_info(param('host')))
                        except ControlError as exc: return self.send_json(409,{'error':str(exc)})
                    if path=='/api/control/log': return self.send_json(200,{'entries':control.recent(int(param('limit','50')))})
                    if path=='/api/activity':
                        # Everything done to the fleet, newest first: control actions, alerts, and past login moves
                        # (kept as history from before the login pool was retired).
                        entries=control.recent(500)
                        try: entries+=[{**json.loads(line),'action':'login_'+json.loads(line).get('action','change')} for line in login_log.read_text(encoding='utf-8').splitlines()[-500:] if line.strip()]
                        except (OSError,ValueError): pass
                        entries.sort(key=lambda e: e.get('at',0), reverse=True)
                        return self.send_json(200,{'entries':entries[:max(1,min(1000,int(param('limit','300'))))]})
                    if path=='/api/watch': return self.send_json(200,{**watchman.summary(),'settings':alert_settings.public()})
                    if path=='/api/agent-keys': return self.send_json(200,{'keys':agent_keys.public(),'scopes':MCP_SCOPES})
                    if path=='/api/router':
                        snapshot,problem=router.snapshot()
                        return self.send_json(200,{'configured':pve.configured(),'snapshot':snapshot,'error':problem,'assignments':router_keys.all()})
                    if path=='/api/infra':
                        snapshot,problem=infra.snapshot()
                        return self.send_json(200,{'configured':pve.configured(),'snapshot':snapshot,'error':problem})
                    if path=='/api/memory':
                        try: return self.send_json(200,memory.files(param('host'),param('root','memory')))
                        except (ControlError,ValueError) as exc: return self.send_json(409,{'error':str(exc)})
                    if path=='/api/memory/file':
                        try: return self.send_json(200,memory.read(param('host'),param('root','memory'),param('path')))
                        except (ControlError,ValueError) as exc: return self.send_json(409,{'error':str(exc)})
                    if path=='/api/memory/version':
                        try: return self.send_json(200,memory.version(param('host'),param('root','memory'),param('path'),param('id')))
                        except (ControlError,ValueError) as exc: return self.send_json(409,{'error':str(exc)})
                    if path=='/api/agents/settings':
                        try: return self.send_json(200,agent_settings.read(param('host')))
                        except (ControlError,ValueError) as exc: return self.send_json(409,{'error':str(exc)})
                    if path=='/api/agents/new':
                        try: return self.send_json(200,{**creator.suggest(),'jobs':creator.job_list()})
                        except ControlError as exc: return self.send_json(409,{'error':str(exc),'jobs':creator.job_list()})
                    if path=='/api/updates': return self.send_json(200,{**updates.summary(),'jobs':updates.job_list(),'configured':pve.configured()})
                    if path=='/api/updates/snapshots':
                        try: return self.send_json(200,{'snapshots':updates.snapshots(param('host'))})
                        except ControlError as exc: return self.send_json(409,{'error':str(exc)})
                    if path=='/api/alerts': return self.send_json(200,{'alerts':alerts(limit_cards(conn,cfg,now),cfg.alerts.get('thresholds',[80,95]),cfg.deepseek_alert)})
                    if path=='/api/limits': return self.send_json(200,limit_cards(conn,cfg,now))
                    if path in ('/api/series','/api/kpis'):
                        # Both read every event in the range, so one runs at a time and its answer is kept for a
                        # minute: several open pages refreshing together used to run out the container's memory.
                        key=path+'?'+urlsplit(self.path).query
                        with fleet_lock:
                            cached=fleet_cache.get(key)
                            if cached and now-cached[0]<INSIGHTS_CACHE_S: return self.send_json(200,cached[1])
                            for old in [k for k,(at,_) in fleet_cache.items() if k.startswith(('/api/series?','/api/kpis?')) and now-at>=INSIGHTS_CACHE_S]: del fleet_cache[old]
                            rng=resolve_range(param('range','today'),datetime.now().astimezone(),param('from'),param('to'))
                            answer=series(conn,cfg,rng,param('bucket','day'),param('by','provider')) if path=='/api/series' else compute_kpis(conn,cfg,rng,param('group_by'),now)
                            fleet_cache[key]=(now,answer)
                        return self.send_json(200,answer)
                    if path in ('/api/sessions','/api/overview'):
                        sessions=_rows(conn,'select sessions.*, r.id as launched_run_id, r.goal_id as goal_id from sessions left join runs r on r.rowid=(select rowid from runs where session_id=sessions.id order by started_at desc, rowid desc limit 1) order by sessions.last_event_at desc'); last=last_kinds(conn,since=now-86400)
                        for s in sessions: s['status']=session_status(s['last_event_at'],last.get(s['id']),s['ended_at'],now,imported=bool(s['imported']))
                        if path=='/api/sessions':
                            for key,col in [('status','status'),('harness','harness'),('account','account_id')]:
                                if param(key): sessions=[s for s in sessions if s[col]==param(key)]
                            if param('host'):
                                sessions=[s for s in sessions if s['host']==(None if param('host') == 'local' else param('host'))]
                            if param('from'): sessions=[s for s in sessions if s['started_at']>=float(param('from'))]
                            if param('to'): sessions=[s for s in sessions if s['started_at']<float(param('to'))]
                            sessions=sessions[:min(2000,max(1,int(param('limit','500'))))]
                            ids=[s['id'] for s in sessions]; extra={}
                            for offset in range(0,len(ids),900):
                                chunk=ids[offset:offset+900]; placeholders=','.join('?' for _ in chunk)
                                enriched=_rows(conn, f'''SELECT session_id, max(CASE WHEN rn=1 THEN kind END) last_kind, max(CASE WHEN tool_rank=1 AND kind='tool_call' THEN tool END) last_tool, max(CASE WHEN preview_rank=1 THEN preview END) last_preview, sum(coalesce(tokens_in,0)+coalesce(tokens_out,0)+coalesce(tokens_cache_read,0)+coalesce(tokens_cache_write,0)) tokens FROM (SELECT *, row_number() over (partition by session_id order by ts desc,id desc) rn, row_number() over (partition by session_id order by (kind='tool_call') desc,ts desc,id desc) tool_rank, row_number() over (partition by session_id order by (preview is not null) desc,ts desc,id desc) preview_rank FROM events WHERE session_id IN ({placeholders})) GROUP BY session_id''', chunk)
                                extra.update({e['session_id']:e for e in enriched})
                            for s in sessions:
                                e=extra.get(s['id'], {}); s.update(last_kind=e.get('last_kind'),last_tool=e.get('last_tool'),last_preview=e.get('last_preview'),tokens=e.get('tokens',0))
                            return self.send_json(200,sessions)
                        counts={k:sum(s['status']==k for s in sessions) for k in ('working','active','waiting','idle','done','error')}; cards=limit_cards(conn,cfg,now); k=compute_kpis(conn,cfg,resolve_range('today',datetime.now().astimezone()),None,now)
                        return self.send_json(200,{'status_counts':counts,'best_now':recommend(cfg.recommend_task_kind),'value':k['value'],'tokens':k['tokens'],'alerts':alerts(cards,cfg.alerts.get('thresholds',[80,95]),cfg.deepseek_alert)})
                    if path.startswith('/api/sessions/'):
                        sid=unquote(path[len('/api/sessions/'):]); rows=_rows(conn,'select sessions.*, r.id as launched_run_id, r.goal_id as goal_id from sessions left join runs r on r.rowid=(select rowid from runs where session_id=sessions.id order by started_at desc, rowid desc limit 1) where sessions.id=?',(sid,))
                        if not rows: return self.send_json(404,{'error':'session not found'})
                        order=param('order','asc')
                        if order not in ('asc','desc'): raise ValueError('invalid order')
                        s=rows[0]; kind=conn.execute('select kind from events where session_id=? order by ts desc, id desc limit 1',(sid,)).fetchone(); s['status']=session_status(s['last_event_at'],kind[0] if kind else None,s['ended_at'],now,imported=bool(s['imported'])); events=_rows(conn,f'select * from events where session_id=? order by ts {order}, id {order} limit ? offset ?',(sid,min(1000,max(1,int(param('limit','100')))),max(0,int(param('offset','0')))))
                        cards=limit_cards(conn,cfg,now)
                        return self.send_json(200,{'session':s,'events':events,'tasks':_rows(conn,'select * from tasks where session_id=? order by started_at',(sid,)),'children':_rows(conn,'select * from sessions where parent_id=?',(sid,)),'value':session_value(conn,cfg,sid),'account_limit':next((c for c in cards if c['account_id']==s['account_id']),None)})
                return self.send_json(404,{'error':'not found'})
            except RunError as exc: return self.send_json(exc.status, {'error':str(exc)})
            except (ValueError,TypeError,KeyError,AttributeError,OverflowError,json.JSONDecodeError) as exc: return self.send_json(400,{'error':str(exc)})
            except sqlite3.Error: return self.send_json(500,{'error':'database error'})
        def other_request(self):
            if not self.host_ok() or (urlsplit(self.path).path.startswith('/api/') and not self.token_ok(self.headers.get('X-HQ-Token'))): return self.send_json(403,{'error':'forbidden'})
            return self.send_json(405,{'error':'method not allowed'})
        do_HEAD = do_DELETE = do_PATCH = do_OPTIONS = other_request
        def do_POST(self): self.write_request('POST')
        def do_PUT(self): self.write_request('PUT')
        def write_request(self,method):
            path=urlsplit(self.path).path
            # The start-up code hands over the permanent token, so it only works on this machine.
            exchange=method=='POST' and path=='/api/exchange' and not self.is_public()
            sign_in=method=='POST' and path=='/api/login'
            self._raw=None
            try: self.read_raw()
            except ValueError: pass  # refused below, once it's clear what kind of request it is
            if method=='POST' and path=='/mcp': return self.handle_mcp()
            if not self.host_ok() or not self.origin_ok() or not (exchange or sign_in or self.write_ok()): return self.send_json(403,{'error':'forbidden'})
            if sign_in and self.is_public() and self.headers.get('X-Forwarded-Proto')!='https': return self.send_json(403,{'error':'sign-in needs HTTPS'})
            REQUEST_USER.user=None
            if not (exchange or sign_in):
                refused=self.allowed(method,path,self.headers.get('X-HQ-Token'))
                if refused: return self.send_json(403,refused)
            try:
                data=self.parse_body()
                if sign_in:
                    if not login.configured(): return self.send_json(403,{'error':'Password sign-in is not set up yet.'})
                    username=data.get('username') or 'admin'
                    try: session=login.sign_in(username,data.get('password'))
                    except Locked as exc: return self.send_json(429,{'error':'Too many attempts.','retry_after':exc.retry_after})
                    if session: REQUEST_USER.user=login.user_for(session); control.log({'action':'signed_in'})
                    return self.send_json(200,{'token':session,'run_key':run_key}) if session else self.send_json(401,{'error':'That username or password is not right.'})
                if method=='POST' and path=='/api/account/password':
                    try: login.change_own_password(self.headers.get('X-HQ-Token'),data.get('current'),data.get('new'))
                    except Locked as exc: return self.send_json(429,{'error':'Too many attempts.','retry_after':exc.retry_after})
                    except ValueError as exc: return self.send_json(409,{'error':str(exc)})
                    control.log({'action':'password_changed'})
                    return self.send_json(200,{'ok':True})
                if method=='POST' and path=='/api/accounts':
                    if not control.elevated(self.headers.get('X-HQ-Token')): return self.send_json(403,{'error':'confirm your password first'})
                    try: answer=login.act(data,self.who(self.headers.get('X-HQ-Token')))
                    except ValueError as exc: return self.send_json(409,{'error':str(exc)})
                    control.log({'action':'account_'+str(data.get('action'))[:20],'target':str(data.get('username') or data.get('id') or data.get('label') or '')[:40]})
                    return self.send_json(200,answer)
                if method=='POST' and path=='/api/logout':
                    login.sign_out(self.headers.get('X-HQ-Token'))
                    return self.send_json(200,{'ok':True})
                if exchange:
                    code=data.get('code')
                    with lock:
                        valid=(isinstance(code,str) and self.server.bootstrap_code is not None and time.monotonic()<self.server.bootstrap_expires and hmac.compare_digest(code.encode(),self.server.bootstrap_code.encode()))
                        if valid: self.server.bootstrap_code=None
                    return self.send_json(200,{'token':token, 'run_key':run_key}) if valid else self.send_json(403,{'error':'forbidden'})
                if path == '/api/goals' or path.startswith('/api/goals/'):
                    if not self.run_key_ok(): return self.send_json(403, {'error':'forbidden'})
                    if run_service is None: raise RunError(403, 'run mode is off')
                    parts = path.strip('/').split('/')
                    if method == 'POST' and path == '/api/goals/prepare': return self.send_json(200, run_service.goal_prepare(data))
                    if method == 'POST' and path == '/api/goals': return self.send_json(201, run_service.goal_create(data))
                    if len(parts) >= 3:
                        gid = unquote(parts[2])
                        if method == 'PUT' and len(parts) == 4 and parts[3] == 'plan': return self.send_json(200, run_service.goal_plan(gid, data))
                        if method == 'POST' and parts[3:] == ['start', 'prepare']: return self.send_json(200, run_service.goal_start_prepare(gid))
                        if method == 'POST' and len(parts) == 4 and parts[3] == 'start': return self.send_json(200, run_service.goal_start(gid, data))
                        if method == 'POST' and len(parts) == 6 and parts[3] == 'steps' and parts[5] in ('retry', 'skip'): return self.send_json(200, run_service.goal_step(gid, unquote(parts[4]), parts[5]))
                        if method == 'POST' and len(parts) == 4 and parts[3] in ('stop', 'discard'): return self.send_json(200, getattr(run_service, 'goal_' + parts[3])(gid))
                        if method == 'POST' and len(parts) == 4 and parts[3] == 'merge': return self.send_json(200, run_service.goal_merge(gid, data))
                if path.startswith('/api/approvals/'):
                    if not self.run_key_ok(): return self.send_json(403, {'error':'forbidden'})
                    if run_service is None: raise RunError(403, 'run mode is off')
                    parts = path.strip('/').split('/')
                    if method == 'POST' and len(parts) == 3: return self.send_json(200, run_service.answer(unquote(parts[2]), data))
                if path == '/api/runs' or path.startswith('/api/runs/'):
                    if not self.run_key_ok(): return self.send_json(403, {'error':'reopen Agent HQ from its launch link to use run mode'})
                    if run_service is None: raise RunError(403, 'run mode is off')
                    parts = path.strip('/').split('/')
                    if method == 'POST' and len(parts) == 5 and parts[3:] == ['message', 'prepare']: return self.send_json(200, run_service.message_prepare(unquote(parts[2]), data))
                    if method == 'POST' and len(parts) == 4 and parts[3] == 'message': return self.send_json(202, run_service.message(unquote(parts[2]), data))
                    if method == 'POST' and path == '/api/runs/prepare': return self.send_json(200, run_service.prepare(data))
                    if method == 'POST' and path == '/api/runs': return self.send_json(201, run_service.start(data))
                    if method == 'POST' and len(parts) == 4 and parts[3] in ('stop', 'merge', 'discard'): return self.send_json(200, run_service.merge(unquote(parts[2]), data) if parts[3] == 'merge' else getattr(run_service, parts[3])(unquote(parts[2])))
                    run_service._enabled()
                if method=='POST' and path=='/api/importer/install':
                    if not install_lock.acquire(blocking=False):
                        return self.send_json(409, {'error': 'install already running'})
                    with collector._lock:
                        collector._health['importer'].update(state='installing', error=None)
                    def run_install():
                        try:
                            install(store.path.parent / 'tools')
                            # In memory only: the importer is tied to this data folder and is
                            # re-detected from its executable at startup; config/ is not written.
                            cfg.sources['importer'].enabled = True
                            with collector._lock:
                                collector._health['importer'].update(state='ok', error=None)
                        except (OSError, RuntimeError, ValueError, KeyError) as exc:
                            with collector._lock:
                                collector._health['importer'].update(state='partial', error=str(exc), last_error_at=time.time())
                        finally:
                            install_lock.release()
                    threading.Thread(target=run_install, daemon=True).start()
                    return self.send_json(200, {'started': True})
                if method=='POST' and path=='/api/control/elevate':
                    caller=self.headers.get('X-HQ-Token')
                    try: ok=login._verify(self.who(caller),data.get('password')) if self.who(caller) else False
                    except Locked as exc: return self.send_json(429,{'error':'Too many attempts.','retry_after':exc.retry_after})
                    if not ok: return self.send_json(401,{'error':'That password is not right.'})
                    until=control.elevate(self.headers.get('X-HQ-Token')); control.log({'action':'elevated'})
                    return self.send_json(200,{'elevated_until':until})
                if method=='POST' and path=='/api/updates/check':
                    # Read-only, so no password check: it runs the audit now instead of waiting for the watchdog.
                    def run_check():
                        try: updates.check()
                        except Exception as exc: control.log({'action':'update_check_failed','error':(str(exc) or type(exc).__name__)[:200]})
                    if pve.configured() and not updates.checking(): threading.Thread(target=run_check, daemon=True).start()
                    return self.send_json(202,{'checking':True})
                if method=='POST' and path=='/api/agents/settings':
                    if not control.elevated(self.headers.get('X-HQ-Token')): return self.send_json(403,{'error':'confirm your password first'})
                    try: return self.send_json(200,{'done':agent_settings.apply(data.get('host'),data.get('section'),data.get('values') or {})})
                    except ControlError as exc: return self.send_json(409,{'error':str(exc)})
                if method=='POST' and path in ('/api/agent-keys','/api/agent-keys/revoke'):
                    if not control.elevated(self.headers.get('X-HQ-Token')): return self.send_json(403,{'error':'confirm your password first'})
                    try:
                        if path=='/api/agent-keys/revoke':
                            agent_keys.revoke(data.get('id')); control.log({'action':'agent_key_revoked','key_id':str(data.get('id'))[:20]})
                            return self.send_json(200,{'ok':True})
                        secret,key=agent_keys.create(data.get('name'),data.get('scopes') or [])
                        control.log({'action':'agent_key_created','name':key['name'],'scopes':key['scopes']})
                        return self.send_json(200,{'token':secret,'key':key})
                    except ValueError as exc: return self.send_json(409,{'error':str(exc)})
                if method=='POST' and path in ('/api/watch/settings','/api/watch/test'):
                    if not control.elevated(self.headers.get('X-HQ-Token')): return self.send_json(403,{'error':'confirm your password first'})
                    try:
                        if path=='/api/watch/test': watchman.test(); return self.send_json(200,{'sent':True})
                        answer=alert_settings.update(data); control.log({'action':'alert_settings_changed','token_changed':bool(data.get('bot_token'))})
                        return self.send_json(200,answer)
                    except ValueError as exc: return self.send_json(409,{'error':str(exc)})
                if method=='POST' and path=='/api/memory/file':
                    if not control.elevated(self.headers.get('X-HQ-Token')): return self.send_json(403,{'error':'confirm your password first'})
                    try: return self.send_json(200,memory.write(data.get('host'),data.get('root'),data.get('path'),data.get('text'),data.get('expect'),bool(data.get('create'))))
                    except ControlError as exc: return self.send_json(409,{'error':str(exc)})
                if method=='POST' and path=='/api/agents/create':
                    if not control.elevated(self.headers.get('X-HQ-Token')): return self.send_json(403,{'error':'confirm your password first'})
                    try: return self.send_json(202,{'job':creator.start(data)})
                    except ControlError as exc: return self.send_json(409,{'error':str(exc)})
                if method=='POST' and path in ('/api/updates/start','/api/updates/rollback','/api/updates/delete-snapshot'):
                    if not control.elevated(self.headers.get('X-HQ-Token')): return self.send_json(403,{'error':'confirm your password first'})
                    host=data.get('host')
                    try:
                        if path=='/api/updates/start':
                            snap=data.get('snapshot'); snap=None if snap is None else bool(snap)
                            job=updates.start(host,data.get('kind'),data.get('version'),snap); return self.send_json(202,{'job':job})
                        if path=='/api/updates/rollback':
                            control.log({'action':'rollback','host':host,'snapshot':data.get('name')}); updates.rollback(host,data.get('name'))
                        else:
                            control.log({'action':'snapshot_deleted','host':host,'snapshot':data.get('name')}); updates.delete_snapshot(host,data.get('name'))
                    except (ControlError, ValueError) as exc: return self.send_json(409,{'error':str(exc)})
                    return self.send_json(200,{'ok':True})
                if method=='POST' and path=='/api/router/keys':
                    if not control.elevated(self.headers.get('X-HQ-Token')): return self.send_json(403,{'error':'confirm your password first'})
                    try: saved=router_keys.set(data.get('id'),data.get('agents'),data.get('tools'),data.get('note'))
                    except ValueError as exc: return self.send_json(409,{'error':str(exc)})
                    control.log({'action':'router_key_assigned','key_id':str(data.get('id'))[:64],'agents':(saved or {}).get('agents',[]),'tools':(saved or {}).get('tools',[])})
                    return self.send_json(200,{'saved':saved})
                if method=='POST' and path=='/api/router/key-action':
                    # Create, pause, resume, delete or copy a 9router key. The value is in the answer only, never logged.
                    if not control.elevated(self.headers.get('X-HQ-Token')): return self.send_json(403,{'error':'confirm your password first'})
                    action=data.get('action')
                    try: answer=router.key_action(action,data.get('id'),data.get('name'),data.get('access'))
                    except (ControlError, ValueError) as exc: return self.send_json(409,{'error':str(exc)})
                    control.log({'action':f'router_key_{action}','key_id':str(answer.get('id') or data.get('id'))[:64],'name':answer.get('name') or None})
                    return self.send_json(200,answer)
                if method=='POST' and path=='/api/updates/settings':
                    if not control.elevated(self.headers.get('X-HQ-Token')): return self.send_json(403,{'error':'confirm your password first'})
                    try: settings=updates.set_settings(data)
                    except ValueError as exc: return self.send_json(409,{'error':str(exc)})
                    control.log({'action':'update_settings_changed','settings':settings})
                    return self.send_json(200,settings)
                if method=='POST' and path.startswith('/api/control/'):
                    host=data.get('host'); action=path.rsplit('/',1)[1]
                    if action not in ('power','restart-claude','send'): return self.send_json(404,{'error':'not found'})
                    # Changing an agent's state needs a recent password check, like the terminal.
                    if not control.elevated(self.headers.get('X-HQ-Token')): return self.send_json(403,{'error':'confirm your password first'})
                    try:
                        if action=='power': pve.power(host,data.get('action')); entry={'action':'power_'+str(data.get('action')),'host':host}
                        elif action=='restart-claude': pve.restart_claude(host); entry={'action':'restart_claude','host':host}
                        else: pve.send_text(host,data.get('text')); entry={'action':'message_sent','host':host,'chars':len(data.get('text') or '')}
                    except ControlError as exc:
                        control.log({'action':action+'_failed','host':host,'error':str(exc)[:200]})
                        return self.send_json(409,{'error':str(exc)})
                    control.log(entry)
                    return self.send_json(200,{'ok':True})
                if method=='POST' and path=='/api/limits/manual':
                    if data['account_id'] not in {a.id for a in cfg.accounts}: raise ValueError('unknown account')
                    add_manual(store,data['account_id'],data['window'],data['used_percent'],data.get('resets_at'),time.time()); changed(); return self.send_json(200,{'ok':True})
                if method=='PUT' and path=='/api/settings':
                    new=validate_settings(data,cfg.sources,cfg.relabel)
                    save_config(Path(config_dir), new)
                    cfg.sources = new.sources
                    profiles = {a.id: a.profiles for a in cfg.accounts}
                    for account in new.accounts: account.profiles = profiles.get(account.id, [])
                    cfg.accounts = new.accounts
                    cfg.prices = new.prices
                    cfg.pricebook.user = cfg.prices
                    cfg.alerts = new.alerts
                    cfg.default_skin = new.default_skin
                    cfg.recommend_task_kind = new.recommend_task_kind
                    cfg.zylos_previews = new.zylos_previews
                    cfg.deepseek_alert = new.deepseek_alert
                    changed()
                    return self.send_json(200, {'ok': True})
                return self.send_json(404,{'error':'not found'})
            except RunError as exc: return self.send_json(exc.status, {'error':str(exc)})
            except (ValueError,TypeError,KeyError,AttributeError,OverflowError,OSError) as exc:
                return self.send_json(403,{'error':'forbidden'}) if exchange else self.send_json(400,{'error':str(exc)})
    if remote_collector: remote_collector.on_change(changed)
    class HqHTTPServer(ThreadingHTTPServer):
        def server_close(self):
            watchdog_stop.set()
            super().server_close()
            for worker in workers:
                worker.join(timeout=5)

    server=HqHTTPServer(('127.0.0.1',port),Handler); server.daemon_threads=True
    server.bootstrap_code=secrets.token_urlsafe(24); server.bootstrap_expires=time.monotonic()+120
    return server,token
