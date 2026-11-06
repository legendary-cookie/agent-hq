from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
import calendar
from typing import Literal

@dataclass(frozen=True)
class Range:
    start: float
    end: float
    prev_start: float
    prev_end: float

def resolve_range(name, now, start=None, end=None):
    day = now.replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    if name == 'today': a, b = day, day + timedelta(days=1); prev = day - timedelta(days=1)
    elif name == 'week':
        a = day - timedelta(days=day.weekday()); b = a + timedelta(days=7); prev = a - timedelta(days=7)
    elif name == 'month':
        a = day.replace(day=1); b = a.replace(year=a.year + (a.month == 12), month=a.month % 12 + 1); prev = (a - timedelta(days=1)).replace(day=1)
    elif name == 'custom':
        if start is None or end is None: raise ValueError('custom range requires start and end')
        def stamp(x): return x.timestamp() if isinstance(x, datetime) else float(x)
        x, y = stamp(start), stamp(end)
        if x >= y: raise ValueError('invalid range')
        return Range(x,y,x-(y-x),x)
    else: raise ValueError('invalid range')
    return Range(a.astimezone().timestamp(),b.astimezone().timestamp(),prev.astimezone().timestamp(),a.astimezone().timestamp())

def value_usd(tokens_in, tokens_out, cache_read, cache_write, price):
    if price is None: return None
    return sum((n or 0)*p for n,p in zip((tokens_in,tokens_out,cache_read,cache_write),(price.input,price.output,price.cache_read,price.cache_write)))/1000000

def _billing(now, day):
    d=datetime.fromtimestamp(now); day=max(1,min(31,day))
    def at(y,m): return d.replace(year=y,month=m,day=min(day,calendar.monthrange(y,m)[1]),hour=0,minute=0,second=0,microsecond=0)
    start=at(d.year,d.month)
    if d < start:
        y,m=(d.year-1,12) if d.month==1 else (d.year,d.month-1); start=at(y,m)
    y,m=(start.year+1,1) if start.month==12 else (start.year,start.month+1)
    return start.astimezone().timestamp(),at(y,m).astimezone().timestamp()

def _rows(conn, sql, args=()):
    cur=conn.execute(sql,args); return [dict(zip([d[0] for d in cur.description], row)) for row in cur.fetchall()]

# Events are read one row at a time with only the columns used. A month can hold over a million events,
# and loading them all as dicts took HQ to 1.5 GB per request.
EVENT_COLS = 'session_id, ts, model, kind, tokens_in, tokens_out, tokens_cache_read, tokens_cache_write, tokens_estimated'

def session_value(conn,cfg,session_id):
    session=_rows(conn,'select * from sessions where id=?',(session_id,))
    if not session: return {'tokens': 0,'value_usd':0.0,'unpriced_tokens':0,'effective_cost_usd':None,'estimated':False}
    s=session[0]; total=0; val=0.; unpriced=0; est=False
    for model,*nums,estimated in conn.execute('select model, tokens_in, tokens_out, tokens_cache_read, tokens_cache_write, tokens_estimated from events where session_id=?',(session_id,)):
        nums=[x or 0 for x in nums]; n=sum(nums); total+=n
        v=value_usd(*nums,cfg.pricebook.get(model or s['model']))
        if v is None: unpriced+=n
        else: val+=v
        est |= bool(n and estimated)
    cost=None; account=next((a for a in cfg.accounts if a.id==s['account_id']),None)
    if account and account.monthly_price_usd is not None and s['started_at'] is not None:
        start,end=_billing(s['started_at'],account.billing_day)
        sums='coalesce(e.tokens_in,0)+coalesce(e.tokens_out,0)+coalesce(e.tokens_cache_read,0)+coalesce(e.tokens_cache_write,0)'
        denom=conn.execute(f'select coalesce(sum({sums}),0) from events e join sessions s on s.id=e.session_id where s.account_id=? and e.ts>=? and e.ts<?',(account.id,start,end)).fetchone()[0]
        numer=conn.execute(f'select coalesce(sum({sums}),0) from events e where e.session_id=? and e.ts>=? and e.ts<?',(session_id,start,end)).fetchone()[0]
        if denom: cost=account.monthly_price_usd*numer/denom
    return {'tokens':total,'value_usd':val,'unpriced_tokens':unpriced,'effective_cost_usd':cost,'estimated':est}

def series(conn, cfg, rng: Range, bucket: Literal["hour", "day"], by: Literal["harness", "provider", "model", "account"]) -> list[dict]:
    if bucket not in ('hour', 'day') or by not in ('harness', 'provider', 'model', 'account'):
        raise ValueError('invalid series grouping')
    sessions = {s['id']: s for s in _rows(conn, 'select * from sessions')}
    rows = {}
    def row(ts, session, model=None):
        dt = datetime.fromtimestamp(ts)
        dt = dt.replace(minute=0, second=0, microsecond=0, **({'hour': 0} if bucket == 'day' else {}))
        key = (model or session.get('model')) if by == 'model' else session.get('account_id' if by == 'account' else by)
        k = (dt.timestamp(), key or 'unknown')
        return rows.setdefault(k, {'t': k[0], 'key': k[1], 'tokens': 0, 'value_usd': None, 'unpriced_tokens': 0, 'tasks': 0, 'errors': 0})
    for sid, ts, model, kind, *nums in conn.execute('select session_id, ts, model, kind, tokens_in, tokens_out, tokens_cache_read, tokens_cache_write from events where ts>=? and ts<?', (rng.start, rng.end)):
        s = sessions.get(sid, {})
        r = row(ts, s, model); nums = [x or 0 for x in nums]
        r['tokens'] += sum(nums); r['errors'] += kind == 'error'
        v = value_usd(*nums, cfg.pricebook.get(model or s.get('model')))
        if v is None: r['unpriced_tokens'] += sum(nums)
        if v is not None and sum(nums): r['value_usd'] = (r['value_usd'] or 0) + v
    for task in _rows(conn, 'select * from tasks where started_at>=? and started_at<?', (rng.start, rng.end)):
        row(task['started_at'], sessions.get(task['session_id'], {}))['tasks'] += 1
    return [rows[k] for k in sorted(rows)]

def compute_kpis(conn,cfg,rng,group_by,now):
    if group_by not in (None,'harness','provider','model','account'): raise ValueError('invalid group_by')
    def calculate(start,end,lists=True, filter_group=None):
        sessions=_rows(conn,'select * from sessions'); byid={s['id']:s for s in sessions}
        tasks=_rows(conn,'select * from tasks where started_at>=? and started_at<?',(start,end))
        keep=lambda sid: True
        if filter_group:
            key,value=filter_group; col='account_id' if key=='account' else key
            keep=lambda sid: byid.get(sid,{}).get(col)==value
            tasks=[t for t in tasks if keep(t['session_id'])]; sessions=[s for s in sessions if s.get(col)==value]
        totals=[0]*4; priced=0.; unpriced=0; estimated=False; values=defaultdict(float); session_tokens=defaultdict(int); priced_sessions=set(); models=defaultdict(int)
        seconds=0.; tools=0; errors=0; last_sid=None; last_ts=None
        # Ordered by session then time, so active time is summed from each event's gap to the one before.
        for sid,ts,model,kind,*nums,est in conn.execute(f'select {EVENT_COLS} from events where ts>=? and ts<? order by session_id,ts,id',(start,end)):
            if not keep(sid): continue
            s=byid.get(sid,{}); nums=[x or 0 for x in nums]; n=sum(nums)
            session_tokens[sid]+=n; totals=[a+b for a,b in zip(totals,nums)]; model=model or s.get('model'); models[model or 'unknown']+=n
            v=value_usd(*nums,cfg.pricebook.get(model)); unpriced+=n if v is None else 0
            if v is not None: priced+=v; values[sid]+=v; priced_sessions.add(sid) if n else None
            estimated |= bool(n and est)
            if sid==last_sid and ts-last_ts<=600: seconds+=ts-last_ts
            last_sid,last_ts=sid,ts
            tools+=kind=='tool_call'; errors+=kind=='error'
        hours=seconds/3600
        ok=sum(t['outcome']=='ok' for t in tasks); err=sum(t['outcome']=='error' for t in tasks)
        durations=[t['ended_at']-t['started_at'] for t in tasks if t['ended_at'] is not None]
        from .status import session_status
        from .status import last_kinds
        last=last_kinds(conn)
        active=sum(session_status(s['last_event_at'],last.get(s['id']),s['ended_at'],now,imported=bool(s['imported'])) in ('working', 'active') for s in sessions if s['last_event_at'] is not None)
        roi={}
        for a in cfg.accounts:
            b0,b1=_billing(now,a.billing_day); v=0
            for model,session_model,*nums in conn.execute('select e.model,s.model,e.tokens_in,e.tokens_out,e.tokens_cache_read,e.tokens_cache_write from events e join sessions s on s.id=e.session_id where s.account_id=? and e.ts>=? and e.ts<?',(a.id,b0,b1)):
                v+=value_usd(*nums,cfg.pricebook.get(model or session_model)) or 0
            roi[a.id]={'value_usd':v,'monthly_price_usd':a.monthly_price_usd,'roi':v/a.monthly_price_usd if a.monthly_price_usd else None}
        snaps=_rows(conn,'select * from limit_snapshots where recorded_at>=? and recorded_at<?',(start,end)); peaks={}
        for s in snaps:
            if s['source']=='measured':
                k=(s['account_id'],s['window'],s['scope'],s['resets_at']); peaks[k]=max(peaks.get(k,0),s['used_percent'])
        quota=defaultdict(dict); grouped=defaultdict(list)
        for (a,w,scope,reset),v in peaks.items(): grouped[a,w].append(v)
        for (a,w),vs in grouped.items(): quota[a][w]=sum(vs)/len(vs)
        blocks=[s for s in snaps if s['source']=='blocked']; blocked=defaultdict(float)
        for s in blocks: blocked[s['scope']]+=max(0,(s['resets_at'] or s['recorded_at'])-s['recorded_at'])/3600
        delegations=_rows(conn,'select * from delegations where ts>=? and ts<?',(start,end)); providers=defaultdict(int); outcomes=defaultdict(lambda:defaultdict(int))
        for d in delegations: providers[d['provider']]+=1; outcomes[d['task_kind']][d['outcome']]+=1
        result={'activity':{'active_agents':active,'sessions_started':sum(start<=s['started_at']<end for s in sessions if s['started_at'] is not None),'tasks':len(tasks),'tool_calls':tools,'agent_hours':hours,'average_task_duration':sum(durations)/len(durations) if durations else None},'quality':{'success_rate':ok/(ok+err) if ok+err else None,'errors_per_100_tool_calls':errors*100/tools if tools else None,'delegation_outcomes':dict(outcomes)},'tokens':dict(zip(('input','output','cache_read','cache_write'),totals)) | {'cache_hit_ratio':totals[2]/(totals[0]+totals[2]) if totals[0]+totals[2] else None},'value':{'total_usd':priced,'unpriced_tokens':unpriced,'estimated':estimated},'plan_roi':roi,'quota_use':dict(quota),'routing':{'delegations_per_provider':dict(providers),'blocked_count':len(blocks),'hours_blocked':dict(blocked)}}
        if lists:
            def ranked(items, name):
                return sorted(items, key=lambda x: (x['value_usd'] is None, -(x['value_usd'] or 0), -x['tokens'], x[name]))[:20]
            session_rows = [{'session_id': sid, 'tokens': n, 'value_usd': values[sid] if sid in priced_sessions else None} for sid,n in session_tokens.items()]
            projects = {}
            for item in session_rows:
                key = byid.get(item['session_id'], {}).get('project') or 'unknown'
                p = projects.setdefault(key, {'project': key, 'tokens': 0, 'value_usd': None})
                p['tokens'] += item['tokens']
                if item['value_usd'] is not None: p['value_usd'] = (p['value_usd'] or 0) + item['value_usd']
            result.update(top_sessions=ranked(session_rows, 'session_id'), top_projects=ranked(list(projects.values()), 'project'), top_models=[{'model':k,'tokens':v} for k,v in sorted(models.items(),key=lambda x:-x[1]) if not k.startswith('<')])
        return result
    result=calculate(rng.start,rng.end); result['previous']=calculate(rng.prev_start,rng.prev_end,False)
    if group_by:
        col='account_id' if group_by=='account' else group_by
        vals={s[col] for s in _rows(conn,'select * from sessions') if s[col] is not None}
        result['groups']={v:calculate(rng.start,rng.end,False,(group_by,v)) for v in sorted(vals)}
    return result
