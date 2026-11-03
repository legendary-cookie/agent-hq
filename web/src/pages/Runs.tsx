import { useEffect, useState } from 'react';
import { ApiError, discardRun, mergeRun, runDiff, runOutput, stopRun, useApi } from '../api/client';
import type { Approval, Run } from '../api/types';
import { Conversation } from '../components/Conversation';
import { DiffView } from '../components/DiffView';
import { fmtDuration } from '../lib/format';
import './runs.css';

export const isLiveRun = (status: string) => ['queued', 'starting', 'running', 'needs_you', 'idle'].includes(status);
export function Runs() {
  const { data: runs, error, reload } = useApi<Run[]>('/api/runs');
  const { data: approvals } = useApi<Approval[]>('/api/approvals?state=waiting');
  const [selected, setSelected] = useState<string | null>(null);
  const { data: run, reload: reloadRun } = useApi<Run>(selected ? `/api/runs/${encodeURIComponent(selected)}` : null);
  const [output, setOutput] = useState<string[]>([]);
  const [outputError, setOutputError] = useState('');
  const [diff, setDiff] = useState<{ diff: string; truncated: boolean; branch: string | null; commit: string | null } | null>(null);
  const [message, setMessage] = useState('');
  useEffect(() => {
    if (!selected) return;
    let active = true;
    setOutput([]); setOutputError(''); setDiff(null); setMessage('');
    if (run && isLiveRun(run.status) && run.status !== 'idle') return () => { active = false; };
    runDiff(selected).then((data) => { if (active) setDiff(data); }).catch((err: Error) => { if (active) setMessage(err.message); });
    return () => { active = false; };
  }, [selected, run?.status, run?.merge_state]);
  useEffect(() => {
    if (!selected) return;
    let active = true;
    const load = () => runOutput(selected).then((lines) => { if (active) { setOutput(lines); setOutputError(''); } }).catch((err: unknown) => { if (active) setOutputError(err instanceof ApiError && err.status === 410 ? "Output from before the last restart isn't kept" : err instanceof Error ? err.message : String(err)); });
    void load();
    const timer = run && isLiveRun(run.status) ? setInterval(load, 2000) : undefined;
    return () => { active = false; clearInterval(timer); };
  }, [selected, run?.status]);
  async function action(fn: () => Promise<unknown>) {
    setMessage('');
    try { await fn(); reload(); reloadRun(); } catch (err) { setMessage(err instanceof Error ? err.message : String(err)); }
  }
  return <div className="runs-layout"><section className="runs-page"><h1>Runs</h1>{error && <p role="alert">{error.message}</p>}
    <table><thead><tr><th>Status</th><th>Tool</th><th>Account</th><th>Project</th><th>Duration</th><th /></tr></thead><tbody>{(runs ?? []).map((item) => <tr key={item.id} onClick={() => setSelected(item.id)}><td><button className={`run-status status-run-${item.status}`} onClick={() => setSelected(item.id)}>{item.status}</button></td><td>{item.tool}</td><td>{item.account ?? '—'}</td><td>{item.project}</td><td>{fmtDuration(item.started_at == null ? null : (item.ended_at ?? Date.now() / 1000) - item.started_at)}</td><td>{isLiveRun(item.status) && <button onClick={(e) => { e.stopPropagation(); void action(() => stopRun(item.id)); }}>Stop run</button>}</td></tr>)}</tbody></table>
    {runs?.length === 0 && <p>No runs yet.</p>}</section>
    {selected && <aside className="run-detail"><button onClick={() => setSelected(null)} aria-label="Close run detail">×</button>{run && <><h2>{run.tool} · {run.status}</h2><p>{run.project} · {run.branch ?? 'No branch'}</p>{isLiveRun(run.status) && <button onClick={() => void action(() => stopRun(run.id))}>Stop run</button>}{run.session_id && <p><a href={`#/agents?session=${encodeURIComponent(run.session_id)}`}>View session</a></p>}{outputError && <p>{outputError}</p>}<Conversation run={run} output={output} approvals={Array.isArray(approvals) ? approvals : []} onSent={reloadRun} /><h3>Diff</h3>{diff ? <DiffView diff={diff.diff} truncated={diff.truncated} /> : <p>Loading diff…</p>}<div className="run-actions"><button disabled={!diff?.commit || !run.branch || (isLiveRun(run.status) && run.status !== 'idle')} onClick={() => { if (window.confirm('Merge into project?')) void action(async () => { const result = await mergeRun(run.id, diff!.commit!); if (result.merge_state === 'conflict') setMessage('Merge stopped: conflicts. Your branch is unchanged; resolve it in a terminal.'); else if (result.merge_state === 'dirty') setMessage('Your project has uncommitted changes; commit or stash them first.'); else setMessage('Merged into project.'); }); }}>Merge into project</button><button disabled={!run.branch || (isLiveRun(run.status) && run.status !== 'idle')} onClick={() => { if (window.confirm('Discard branch?')) void action(() => discardRun(run.id)); }}>Discard branch</button></div></>}{message && <p role="status">{message}</p>}</aside>}
  </div>;
}
