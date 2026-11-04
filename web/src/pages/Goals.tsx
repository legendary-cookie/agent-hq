import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { GoalSheet } from '../components/GoalSheet';
import { PlanEditor, validatePlan, type Step } from '../components/PlanEditor';
import { DiffView } from '../components/DiffView';

type Goal = { id:string; text_preview:string; project:string; status:string; branch:string|null; plan_json:string|null; steps:Record<string,{status:string;run_id:string|null}> };
type Diff = {diff:string; truncated:boolean; commit:string|null};
const call = <T,>(path:string, method='GET', body?:unknown) => api<T>(path, {method, headers:{'X-HQ-Run-Key':sessionStorage.getItem('hq-run-key') ?? ''}, ...(body === undefined ? {} : {body:JSON.stringify(body)})});
export function Goals() {
  const [goals,setGoals] = useState<Goal[]>([]), [goal,setGoal] = useState<Goal|null>(null), [steps,setSteps] = useState<Step[]>([]), [diff,setDiff] = useState<Diff|null>(null), [open,setOpen] = useState(false), [error,setError] = useState(''), [options,setOptions] = useState<{projects:string[];tools:Record<string,unknown>}>({projects:[],tools:{}});
  const load = () => call<Goal[]>('/api/goals').then(setGoals).catch(e => setError(e.message));
  const select = async (id:string) => { const g = await call<Goal>(`/api/goals/${encodeURIComponent(id)}`); setGoal(g); setSteps(g.plan_json ? JSON.parse(g.plan_json).steps : []); setDiff(null); if (g.branch && ['done','failed','stopped','needs_you'].includes(g.status)) setDiff(await call<Diff>(`/api/goals/${encodeURIComponent(id)}/diff`)); };
  useEffect(() => { void load(); void api<typeof options>('/api/runs/options').then(setOptions); }, []);
  useEffect(() => {
    const timer = window.setInterval(() => { void load(); if (goal && ['planning', 'running'].includes(goal.status)) void select(goal.id).catch(e => setError(e.message)); }, 3000);
    return () => window.clearInterval(timer);
  }, [goal?.id, goal?.status]);
  const action = async (fn:()=>Promise<unknown>) => { try { setError(''); await fn(); await load(); if (goal) await select(goal.id); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } };
  const path = goal ? `/api/goals/${encodeURIComponent(goal.id)}` : '';
  return <div><h1>Goals</h1><button onClick={() => setOpen(true)}>New goal</button>{error && <p role="alert">{error}</p>}{open && <GoalSheet projects={options.projects} tools={Object.keys(options.tools)} onClose={() => setOpen(false)} onCreate={data => void action(async () => { const prepared = await call<{nonce:string}>('/api/goals/prepare','POST',data); const g = await call<Goal>('/api/goals','POST',{...data,nonce:prepared.nonce}); setOpen(false); await select(g.id); })} />}
    <ul>{goals.map(g => <li key={g.id}><button onClick={() => { void select(g.id).catch(e => setError(e.message)); }}>{g.text_preview}</button> {g.status}</li>)}</ul>
    {goal && <section><h2>{goal.text_preview}</h2><p>{goal.status}</p>{goal.status === 'review' ? <><PlanEditor steps={steps} onChange={setSteps} onStart={() => void action(async () => { await call(`${path}/plan`,'PUT',{steps}); const p = await call<{nonce:string}>(`${path}/start/prepare`,'POST',{}); await call(`${path}/start`,'POST',{nonce:p.nonce}); })} /><button disabled={!!validatePlan(steps)} onClick={() => void action(() => call(`${path}/plan`,'PUT',{steps}))}>Save plan</button></> : <ul>{steps.map(s => <li key={s.id}>{s.title} · {goal.steps?.[s.id]?.status} {goal.steps?.[s.id]?.run_id && <a href={`#/runs?run=${encodeURIComponent(goal.steps[s.id].run_id!)}`}>View run</a>} {goal.steps?.[s.id]?.status === 'failed' && ['retry','skip'].map(a => <button key={a} onClick={() => void action(() => call(`${path}/steps/${s.id}/${a}`,'POST',{}))}>{a}</button>)}</li>)}</ul>}
    {['planning','review','running','needs_you'].includes(goal.status) && <button onClick={() => void action(() => call(`${path}/stop`,'POST',{}))}>Stop goal</button>}{diff && <><h3>Diff</h3><DiffView diff={diff.diff} truncated={diff.truncated} /><button disabled={!diff.commit || goal.status !== 'done'} onClick={() => { if (window.confirm('Merge into project?')) void action(() => call(`${path}/merge`,'POST',{commit:diff.commit})); }}>Merge into project</button><button onClick={() => { if (window.confirm('Discard goal?')) void action(() => call(`${path}/discard`,'POST',{})); }}>Discard goal</button></>}</section>}</div>;
}
