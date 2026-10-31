from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
import calendar

@dataclass(frozen=True)
class Range:
    start: float
    end: float
    prev_start: float
    prev_end: float

def resolve_range(name, now, start=None, end=None):
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
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
    return Range(a.timestamp(),b.timestamp(),prev.timestamp(),a.timestamp())

def value_usd(tokens_in, tokens_out, cache_read, cache_write, price):
    if price is None: return None
    return sum((n or 0)*p for n,p in zip((tokens_in,tokens_out,cache_read,cache_write),(price.input,price.output,price.cache_read,price.cache_write)))/1000000

def _billing(now, day):
    d=datetime.fromtimestamp(now).astimezone(); day=max(1,min(31,day))
    def at(y,m): return d.replace(year=y,month=m,day=min(day,calendar.monthrange(y,m)[1]),hour=0,minute=0,second=0,microsecond=0)
    start=at(d.year,d.month)
    if d < start:
        y,m=(d.year-1,12) if d.month==1 else (d.year,d.month-1); start=at(y,m)
    y,m=(start.year+1,1) if start.month==12 else (start.year,start.month+1)
    return start.timestamp(),at(y,m).timestamp()

def _rows(conn, sql, args=()):
    cur=conn.execute(sql,args); return [dict(zip([d[0] for d in cur.description], row)) for row in cur.fetchall()]

def session_value(conn,cfg,session_id):
    session=_rows(conn,'select * from sessions where id=?',(session_id,))
    if not session: return {'tokens': 0,'value_usd':0.0,'unpriced_tokens':0,'effective_cost_usd':None,'estimated':False}
    s=session[0]; events=_rows(conn,'select * from events where session_id=?',(session_id,)); total=0; val=0.; unpriced=0; est=False
    for e in events:
        nums=[e[k] or 0 for k in ('tokens_in','tokens_out','tokens_cache_read','tokens_cache_write')]; n=sum(nums); total+=n
        v=value_usd(*nums,cfg.prices.get(e['model'] or s['model']))
        if v is None: unpriced+=n
        else: val+=v
        est |= bool(n and e['tokens_estimated'])
    cost=None; account=next((a for a in cfg.accounts if a.id==s['account_id']),None)
    if account and account.monthly_price_usd is not None and s['started_at'] is not None:
        start,end=_billing(s['started_at'],account.billing_day)
        sums='coalesce(e.tokens_in,0)+coalesce(e.tokens_out,0)+coalesce(e.tokens_cache_read,0)+coalesce(e.tokens_cache_write,0)'
        denom=conn.execute(f'select coalesce(sum({sums}),0) from events e join sessions s on s.id=e.session_id where s.account_id=? and e.ts>=? and e.ts<?',(account.id,start,end)).fetchone()[0]
        numer=conn.execute(f'select coalesce(sum({sums}),0) from events e where e.session_id=? and e.ts>=? and e.ts<?',(session_id,start,end)).fetchone()[0]
        if denom: cost=account.monthly_price_usd*numer/denom
    return {'tokens':total,'value_usd':val,'unpriced_tokens':unpriced,'effective_cost_usd':cost,'estimated':est}

def compute_kpis(conn,cfg,rng,group_by,now):
    if group_by not in (None,'harness','provider','model','account'): raise ValueError('invalid group_by')
    def calculate(start,end,lists=True, filter_group=None):
        sessions=_rows(conn,'select * from sessions'); byid={s['id']:s for s in sessions}
        events=_rows(conn,'select * from events where ts>=? and ts<? order by session_id,ts,id',(start,end))
        tasks=_rows(conn,'select * from tasks where started_at>=? and started_at<?',(start,end))
        if filter_group:
            key,value=filter_group; col='account_id' if key=='account' else key
            events=[e for e in events if byid.get(e['session_id'],{}).get(col)==value]; tasks=[t for t in tasks if byid.get(t['session_id'],{}).get(col)==value]; sessions=[s for s in sessions if s.get(col)==value]
        totals=[0]*4; priced=0.; unpriced=0; estimated=False; values=defaultdict(float); models=defaultdict(int); times=defaultdict(list)
        for e in events:
            s=byid.get(e['session_id'],{}); nums=[e[k] or 0 for k in ('tokens_in','tokens_out','tokens_cache_read','tokens_cache_write')]; n=sum(nums)
            totals=[a+b for a,b in zip(totals,nums)]; model=e['model'] or s.get('model'); models[model or 'unknown']+=n
            v=value_usd(*nums,cfg.prices.get(model)); unpriced+=n if v is None else 0
            if v is not None: priced+=v; values[e['session_id']]+=v
            estimated |= bool(n and e['tokens_estimated']); times[e['session_id']].append(e['ts'])
        hours=sum(sum(b-a for a,b in zip(ts,ts[1:]) if b-a<=600) for ts in times.values())/3600
        ok=sum(t['outcome']=='ok' for t in tasks); err=sum(t['outcome']=='error' for t in tasks); tools=sum(e['kind']=='tool_call' for e in events); errors=sum(e['kind']=='error' for e in events)
        durations=[t['ended_at']-t['started_at'] for t in tasks if t['ended_at'] is not None]
        from .status import session_status
        from .status import last_kinds
        last=last_kinds(conn)
        active=sum(session_status(s['last_event_at'],last.get(s['id']),s['ended_at'],now)=='working' for s in sessions if s['last_event_at'] is not None)
        roi={}
        for a in cfg.accounts:
            b0,b1=_billing(now,a.billing_day); v=0
            for e in _rows(conn,'select e.*,s.model as session_model from events e join sessions s on s.id=e.session_id where s.account_id=? and e.ts>=? and e.ts<?',(a.id,b0,b1)):
                v+=value_usd(e['tokens_in'],e['tokens_out'],e['tokens_cache_read'],e['tokens_cache_write'],cfg.prices.get(e['model'] or e['session_model'])) or 0
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
            projects=defaultdict(float)
            for sid,v in values.items(): projects[byid.get(sid,{}).get('project') or 'unknown']+=v
            result.update(top_sessions=[{'session_id':k,'value_usd':v} for k,v in sorted(values.items(),key=lambda x:-x[1])],top_projects=[{'project':k,'value_usd':v} for k,v in sorted(projects.items(),key=lambda x:-x[1])],top_models=[{'model':k,'tokens':v} for k,v in sorted(models.items(),key=lambda x:-x[1])])
        return result
    result=calculate(rng.start,rng.end); result['previous']=calculate(rng.prev_start,rng.prev_end,False)
    if group_by:
        col='account_id' if group_by=='account' else group_by
        vals={s[col] for s in _rows(conn,'select * from sessions') if s[col] is not None}
        result['groups']={v:calculate(rng.start,rng.end,False,(group_by,v)) for v in sorted(vals)}
    return result
