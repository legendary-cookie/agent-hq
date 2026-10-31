import importlib.util
import math
import sqlite3
import tempfile
import time
from contextlib import closing
from .config import is_forbidden
from pathlib import Path

WINDOW_SECONDS={'5h':18000,'week':604800,'day':86400,'month':2592000}

def forecast_seconds(points, now):
    points=[(t,v) for t,v in points if now-1800<=t<=now]
    if len(points)<2: return None
    mt=sum(t for t,v in points)/len(points); mv=sum(v for t,v in points)/len(points)
    den=sum((t-mt)**2 for t,v in points)
    if not den: return None
    slope=sum((t-mt)*(v-mv) for t,v in points)/den
    if slope<=0: return None
    return max(0.,(100-(mv+slope*(now-mt)))/slope)

def limit_cards(conn,cfg,now):
    cards=[]
    for account in cfg.accounts:
        cur=conn.execute('select scope,window,used_percent,resets_at,source,recorded_at from limit_snapshots where account_id=? and recorded_at<=? order by recorded_at desc,id desc',(account.id,now)); rows=cur.fetchall(); latest={}
        for row in rows:
            scope,window,pct,reset,source,ts=row
            if source=='blocked' and reset is not None and reset<=now: continue
            latest.setdefault((window,scope),row)
        windows=[]
        for (window,scope),(_,_,pct,reset,source,ts) in latest.items():
            age=max(0,now-ts); length=WINDOW_SECONDS.get(window); stale=bool(length and age>length)
            points=[(r[5],r[2]) for r in rows if r[0]==scope and r[1]==window and r[4]=='measured']
            seconds=forecast_seconds(points,now) if source=='measured' else None
            burn=conn.execute('select coalesce(sum(coalesce(e.tokens_in,0)+coalesce(e.tokens_out,0)+coalesce(e.tokens_cache_read,0)+coalesce(e.tokens_cache_write,0)),0) from events e join sessions s on s.id=e.session_id where s.account_id=? and e.ts>=? and e.ts<=? and (?="*" or coalesce(e.model,s.model)=?)',(account.id,now-3600,now,scope,scope)).fetchone()[0]
            if seconds is None and source!='measured' and account.cap_tokens.get(window) and burn>0: seconds=max(0,(100-pct)/100*account.cap_tokens[window]/burn*3600)
            windows.append({'window':window,'scope':scope,'used_percent':100.0 if source=='blocked' else pct,'resets_at':reset,'source':source,'stale':stale,'age_s':age,'forecast_s':seconds,'burn_tokens_per_h':burn})
        if not windows: windows=[{'window':None,'scope':'*','used_percent':None,'resets_at':None,'source':'unknown','stale':False,'age_s':None,'forecast_s':None,'burn_tokens_per_h':0}]
        cards.append({'account_id':account.id,'label':account.label,'windows':windows})
    return cards

def alerts(cards, thresholds):
    levels=sorted(thresholds); result=[]
    for card in cards:
        for window in card['windows']:
            pct=window['used_percent']; level=None
            if window['source']=='blocked': level='blocked'
            elif pct is not None and levels and pct>=levels[-1]: level='critical' if len(levels)>1 else 'warn'
            elif pct is not None and levels and pct>=levels[0]: level='warn'
            if level: result.append({'account_id':card['account_id'],'window':window.get('window'),'scope':window.get('scope'),'level':level,'used_percent':pct})
    return result

def add_manual(store,account_id,window,used_percent,resets_at,now):
    if window not in WINDOW_SECONDS or not isinstance(used_percent,(int,float)) or not math.isfinite(used_percent) or not 0<=used_percent<=100: raise ValueError('invalid manual limit')
    if not isinstance(account_id,str) or not account_id: raise ValueError('invalid account')
    with store._lock, store._writer as c:
        c.execute("insert into limit_snapshots(account_id,harness,provider,scope,window,used_percent,resets_at,source,recorded_at) values (?, '', '', '*', ?, ?, ?, 'manual', ?)",(account_id,window,used_percent,resets_at,now))

_recommend_cache = {}
recommend_error = None

def recommend_bridge(pu_dir,task_kind):
    global recommend_error
    path=Path(pu_dir)
    key=(str(path.resolve()),task_kind)
    now=time.monotonic()
    cached=_recommend_cache.get(key)
    if cached and now-cached[0]<30:
        recommend_error=cached[2]
        return cached[1]
    result=None
    try:
        if any(is_forbidden(p) for p in (path, path/'tracker.py', path/'usage.db', path/'providers.json')):
            raise ValueError('forbidden recommendation path')
        spec=importlib.util.spec_from_file_location('_hq_tracker',path/'tracker.py')
        if spec is None or spec.loader is None: raise ImportError('tracker unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            snapshot=Path(tmp)/'usage.db'
            with closing(sqlite3.connect((path/'usage.db').resolve().as_uri()+'?mode=ro',uri=True)) as source, closing(sqlite3.connect(snapshot)) as dest:
                source.backup(dest)
            module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            result=[dict(r) for r in module.recommend(task_kind,db_path=snapshot,providers_path=path/'providers.json')]
        recommend_error=None
    except Exception as exc:
        recommend_error=str(exc)
    _recommend_cache[key]=(now,result,recommend_error)
    return result
