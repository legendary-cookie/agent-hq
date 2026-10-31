import json
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
from .kpis import compute_kpis, resolve_range, session_value, _rows
from .limits import limit_cards, alerts, add_manual, recommend_bridge
from .status import last_kinds, session_status
import sqlite3

def make_server(store,collector,cfg,config_dir,port=7420,static_dir=None):
    token=secrets.token_urlsafe(32); listeners=set(); lock=threading.Lock()
    def changed():
        with lock:
            for q in listeners: q.put(time.time())
    collector.on_change(changed)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def send_json(self,status,data):
            body=json.dumps(data,allow_nan=False).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        def host_ok(self): return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}')
        def write_ok(self):
            return self.headers.get('X-HQ-Token')==token and self.headers.get('Origin')==f'http://{self.headers.get("Host")}'
        def parse_body(self):
            length=int(self.headers.get('Content-Length','0'))
            if length<0 or length>1048576: raise ValueError('invalid body size')
            data=json.loads(self.rfile.read(length));
            if not isinstance(data,dict): raise ValueError('expected object')
            return data
        def do_GET(self):
            if not self.host_ok(): return self.send_json(403,{'error':'forbidden host'})
            parsed=urlsplit(self.path); path=parsed.path; query=parse_qs(parsed.query); now=time.time()
            def param(k,default=None): return query.get(k,[default])[0]
            try:
                if path=='/api/stream':
                    q=queue.Queue()
                    with lock: listeners.add(q)
                    try:
                        self.send_response(200); self.send_header('Content-Type','text/event-stream'); self.send_header('Cache-Control','no-cache'); self.end_headers(); self.wfile.flush()
                        while True:
                            try: msg=f'event: changed\ndata: {json.dumps({"ts":q.get(timeout=15)})}\n\n'
                            except queue.Empty: msg=': ping\n\n'
                            self.wfile.write(msg.encode()); self.wfile.flush()
                    except (BrokenPipeError,ConnectionResetError,TimeoutError): pass
                    finally:
                        with lock: listeners.discard(q)
                    return
                if path=='/':
                    index=Path(static_dir)/'index.html' if static_dir else None
                    body=index.read_text(encoding='utf-8') if index and index.is_file() else '<html><head></head><body>UI not built yet</body></html>'
                    meta=f'<meta name="hq-token" content="{token}">'; body=body.replace('</head>',meta+'</head>',1) if '</head>' in body else meta+body
                    data=body.encode(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
                if not path.startswith('/api/') and static_dir:
                    root=Path(static_dir).resolve(); target=(root/unquote(path).lstrip('/')).resolve()
                    if target.is_relative_to(root) and target.is_file():
                        import mimetypes
                        data=target.read_bytes(); self.send_response(200); self.send_header('Content-Type',mimetypes.guess_type(target.name)[0] or 'application/octet-stream'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
                if path=='/api/health': return self.send_json(200,collector.health())
                if path=='/api/settings': return self.send_json(200,{'sources':{k:{'enabled':v.enabled,'paths':[str(p) for p in v.paths]} for k,v in cfg.sources.items()},'accounts':[asdict(a) for a in cfg.accounts],'prices':{k:asdict(v) for k,v in cfg.prices.items()},'alerts':cfg.alerts,'default_skin':cfg.default_skin,'recommend_task_kind':cfg.recommend_task_kind})
                if path=='/api/recommend':
                    source=cfg.sources.get('provider_usage'); result=recommend_bridge(source.paths[0],param('task_kind',cfg.recommend_task_kind)) if source and source.enabled and source.paths else None
                    return self.send_json(200,result)
                with closing(store.conn()) as conn:
                    if path=='/api/limits': return self.send_json(200,limit_cards(conn,cfg,now))
                    if path=='/api/kpis':
                        name=param('range','today'); start=param('from'); end=param('to'); rng=resolve_range(name,datetime.now().astimezone(),start,end)
                        return self.send_json(200,compute_kpis(conn,cfg,rng,param('group_by'),now))
                    if path in ('/api/sessions','/api/overview'):
                        sessions=_rows(conn,'select * from sessions order by last_event_at desc'); last=last_kinds(conn)
                        for s in sessions: s['status']=session_status(s['last_event_at'],last.get(s['id']),s['ended_at'],now)
                        if path=='/api/sessions':
                            for key,col in [('status','status'),('harness','harness'),('account','account_id')]:
                                if param(key): sessions=[s for s in sessions if s[col]==param(key)]
                            if param('from'): sessions=[s for s in sessions if s['started_at']>=float(param('from'))]
                            if param('to'): sessions=[s for s in sessions if s['started_at']<float(param('to'))]
                            return self.send_json(200,sessions)
                        counts={k:sum(s['status']==k for s in sessions) for k in ('working','waiting','idle','done','error')}; cards=limit_cards(conn,cfg,now); k=compute_kpis(conn,cfg,resolve_range('today',datetime.now().astimezone()),None,now); source=cfg.sources.get('provider_usage')
                        return self.send_json(200,{'status_counts':counts,'best_now':recommend_bridge(source.paths[0],cfg.recommend_task_kind) if source and source.enabled and source.paths else None,'value':k['value'],'tokens':k['tokens'],'alerts':alerts(cards,cfg.alerts.get('thresholds',[80,95]))})
                    if path.startswith('/api/sessions/'):
                        sid=unquote(path[len('/api/sessions/'):]); rows=_rows(conn,'select * from sessions where id=?',(sid,))
                        if not rows: return self.send_json(404,{'error':'session not found'})
                        s=rows[0]; events=_rows(conn,'select * from events where session_id=? order by ts,id limit ? offset ?',(sid,min(1000,max(1,int(param('limit','100')))),max(0,int(param('offset','0')))))
                        cards=limit_cards(conn,cfg,now)
                        return self.send_json(200,{'session':s,'events':events,'tasks':_rows(conn,'select * from tasks where session_id=? order by started_at',(sid,)),'children':_rows(conn,'select * from sessions where parent_id=?',(sid,)),'value':session_value(conn,cfg,sid),'account_limit':next((c for c in cards if c['account_id']==s['account_id']),None)})
                return self.send_json(404,{'error':'not found'})
            except (ValueError,TypeError,KeyError,OverflowError,json.JSONDecodeError) as exc: return self.send_json(400,{'error':str(exc)})
            except sqlite3.Error: return self.send_json(500,{'error':'database error'})
        def do_POST(self): self.write_request('POST')
        def do_PUT(self): self.write_request('PUT')
        def write_request(self,method):
            if not self.host_ok() or not self.write_ok(): return self.send_json(403,{'error':'forbidden'})
            try:
                data=self.parse_body(); path=urlsplit(self.path).path
                if method=='POST' and path=='/api/limits/manual':
                    if data['account_id'] not in {a.id for a in cfg.accounts}: raise ValueError('unknown account')
                    add_manual(store,data['account_id'],data['window'],data['used_percent'],data.get('resets_at'),time.time()); changed(); return self.send_json(200,{'ok':True})
                if method=='PUT' and path=='/api/settings':
                    sources={k:SourceCfg(v['enabled'],[_path(p) for p in v['paths']]) for k,v in data['sources'].items()}
                    new=Config(sources,[Account(**a) for a in data['accounts']],{k:Price(**v) for k,v in data['prices'].items()},data['alerts'],data['default_skin'],data['recommend_task_kind'])
                    save_config(Path(config_dir),new); cfg.sources=new.sources; cfg.accounts=new.accounts; cfg.prices=new.prices; cfg.alerts=new.alerts; cfg.default_skin=new.default_skin; cfg.recommend_task_kind=new.recommend_task_kind; changed(); return self.send_json(200,{'ok':True})
                return self.send_json(404,{'error':'not found'})
            except (ValueError,TypeError,KeyError,OverflowError,OSError) as exc: return self.send_json(400,{'error':str(exc)})
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler); server.daemon_threads=True
    return server,token
