import json
import hmac
import math
import queue
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
from .auth import Locked, Login
from .fleet import fleet_overview, load_fleet, rolling_window
from .insights import insights
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
INSIGHTS_CACHE_S = 60

def make_server(store,collector,cfg,config_dir,port=7420,static_dir=None,token: str | None = None,run_service=None,remote_collector=None,public_url=None):
    run_key = secrets.token_urlsafe(32)
    login = Login(store.path.parent / 'login.json')
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

        def do_GET(self):
            if not self.host_ok(): return self.send_json(403,{'error':'forbidden host'})
            parsed=urlsplit(self.path); path=parsed.path; query=parse_qs(parsed.query,keep_blank_values=True); now=time.time()
            # The app's name is shown on the sign-in page, before anyone has signed in.
            if path=='/api/brand': return self.send_json(200,{'brand':load_fleet(config_dir)['brand']})
            if path.startswith('/api/') and not (self.token_ok(self.headers.get('X-HQ-Token')) or (path=='/api/stream' and self.token_ok(query.get('token',[None])[0]))): return self.send_json(403,{'error':'forbidden'})
            def param(k,default=None): return query.get(k,[default])[0]
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
                            if cached and now-cached[0]<FLEET_CACHE_S: return self.send_json(200,cached[1])
                            answer=fleet_overview(conn,cfg,rolling_window(name,now),now,remote_collector.health() if remote_collector else {},load_fleet(config_dir))
                            fleet_cache[name]=(now,answer)
                        return self.send_json(200,answer)
                    if path=='/api/insights':
                        name=param('range','today')
                        with fleet_lock:
                            cached=fleet_cache.get('insights:'+name)
                            if cached and now-cached[0]<INSIGHTS_CACHE_S: return self.send_json(200,cached[1])
                            answer=insights(conn,rolling_window(name,now),now)
                            fleet_cache['insights:'+name]=(now,answer)
                        return self.send_json(200,answer)
                    if path=='/api/alerts': return self.send_json(200,{'alerts':alerts(limit_cards(conn,cfg,now),cfg.alerts.get('thresholds',[80,95]),cfg.deepseek_alert)})
                    if path=='/api/limits': return self.send_json(200,limit_cards(conn,cfg,now))
                    if path=='/api/series':
                        rng=resolve_range(param('range','today'),datetime.now().astimezone(),param('from'),param('to'))
                        return self.send_json(200,series(conn,cfg,rng,param('bucket','day'),param('by','provider')))
                    if path=='/api/kpis':
                        name=param('range','today'); start=param('from'); end=param('to'); rng=resolve_range(name,datetime.now().astimezone(),start,end)
                        return self.send_json(200,compute_kpis(conn,cfg,rng,param('group_by'),now))
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
            if not self.host_ok() or not self.origin_ok() or not (exchange or sign_in or self.write_ok()): return self.send_json(403,{'error':'forbidden'})
            if sign_in and self.is_public() and self.headers.get('X-Forwarded-Proto')!='https': return self.send_json(403,{'error':'sign-in needs HTTPS'})
            try:
                data=self.parse_body()
                if sign_in:
                    if not login.configured(): return self.send_json(403,{'error':'Password sign-in is not set up yet.'})
                    try: session=login.sign_in(data.get('password'))
                    except Locked as exc: return self.send_json(429,{'error':'Too many attempts.','retry_after':exc.retry_after})
                    return self.send_json(200,{'token':session,'run_key':run_key}) if session else self.send_json(401,{'error':'That password is not right.'})
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
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler); server.daemon_threads=True
    server.bootstrap_code=secrets.token_urlsafe(24); server.bootstrap_expires=time.monotonic()+120
    return server,token
