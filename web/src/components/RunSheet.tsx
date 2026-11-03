import { useEffect, useRef, useState } from 'react';
import { ApiError, prepareRun, startRun } from '../api/client';
import type { RunOptions, RunRequest, RunVerdict } from '../api/types';
import { spring } from '../lib/spring';
import './run-sheet.css';

const levels: Record<string, string> = { read_only: 'Read-only', edit: 'Edit project folder', full_auto: 'Full auto' };
const guards: Record<string, string> = { warn: 'Warn', block: 'Block and suggest', auto: 'Auto-route' };
export function RunSheet({ options, onClose, onStarted }: { options: RunOptions; onClose: () => void; onStarted?: () => void }) {
  const [form, setForm] = useState<RunRequest>(() => ({ tool: Object.keys(options.tools)[0] ?? '', model: null, project: options.projects[0] ?? '', prompt: '', level: options.tools[Object.keys(options.tools)[0]]?.levels.includes(options.defaults.level) ? options.defaults.level : (options.tools[Object.keys(options.tools)[0]]?.levels[0] ?? ''), guard_mode: options.defaults.guard_mode, profile: null, time_limit_min: options.defaults.time_limit_min }));
  const [verdict, setVerdict] = useState<RunVerdict | null>(null);
  const [error, setError] = useState('');
  const [starting, setStarting] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const sheet = useRef<HTMLDivElement>(null);
  const version = useRef(0);
  useEffect(() => {
    if (!options.run_mode) return;
    const generation = ++version.current;
    setVerdict(null);
    const timer = setTimeout(() => {
      prepareRun(form).then((result) => { if (version.current === generation) { setVerdict(result.verdict); setError(''); } }).catch((err: Error) => { if (version.current === generation) setError(err.message); });
    }, 300);
    return () => clearTimeout(timer);
  }, [form, options.run_mode]);
  useEffect(() => {
    if (!options.run_mode || !sheet.current || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
    const step = spring(0, 1, 0);
    let last = performance.now();
    let frame = 0;
    const animate = (now: number) => {
      const result = step(Math.min((now - last) / 1000, 0.05));
      last = now;
      if (sheet.current) { sheet.current.style.transform = `translateY(${(1 - result.value) * 24}px) scale(${0.96 + result.value * 0.04})`; sheet.current.style.opacity = String(result.value); }
      if (!result.done) frame = requestAnimationFrame(animate);
    };
    frame = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(frame);
  }, [options.run_mode]);
  const update = (changes: Partial<RunRequest>) => { setConfirming(false); setForm((current) => ({ ...current, ...changes })); };
  async function start() {
    setStarting(true); setError(''); ++version.current;
    try {
      const prepared = await prepareRun(form);
      setVerdict(prepared.verdict);
      if (!prepared.verdict.allowed || !prepared.nonce) return;
      if (form.level === 'full_auto' && !confirming) { setConfirming(true); return; }
      try { await startRun({ ...form, nonce: prepared.nonce }); } catch (err) {
        if (err instanceof ApiError && err.status === 403 && err.message === 'confirm again') {
          const fresh = await prepareRun(form); setVerdict(fresh.verdict); setConfirming(false); setError('Confirmation expired. Review the verdict and start again.'); return;
        }
        throw err;
      }
      onStarted?.(); onClose();
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); } finally { setStarting(false); }
  }
  if (!options.run_mode) return <div className="run-popover" role="status">Run mode is off. Turn it on in config/run.json. <button onClick={onClose} aria-label="Close">×</button></div>;
  const profile = options.profiles.find((item) => item.id === (form.profile ?? verdict?.profile));
  return <div className="run-scrim" onClick={onClose}><div className="run-sheet" ref={sheet} role="dialog" aria-modal="true" aria-label="Start run" onClick={(e) => e.stopPropagation()}>
    <header><h2>Start run</h2><button onClick={onClose} aria-label="Close">×</button></header>
    <label>Tool<select value={form.tool} onChange={(e) => { const tool = e.target.value; update({ tool, profile: null, level: options.tools[tool]?.levels[0] ?? '' }); }}>{Object.keys(options.tools).map((tool) => <option key={tool}>{tool}</option>)}</select></label>
    <label>Model (optional)<input value={form.model ?? ''} onChange={(e) => update({ model: e.target.value || null })} /></label>
    <label>Project<select value={form.project} onChange={(e) => update({ project: e.target.value })}>{options.projects.map((project) => <option key={project}>{project}</option>)}</select></label>
    <label>Task<textarea maxLength={100000} value={form.prompt} onChange={(e) => update({ prompt: e.target.value })} /></label>
    <label>Level<select value={form.level} onChange={(e) => update({ level: e.target.value })}>{(options.tools[form.tool]?.levels ?? []).map((level) => <option key={level} value={level}>{levels[level] ?? level}</option>)}</select></label>
    <label>Guard mode<select value={form.guard_mode} onChange={(e) => update({ guard_mode: e.target.value })}>{Object.entries(guards).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
    <label>Account<select value={form.profile ?? ''} onChange={(e) => update({ profile: e.target.value || null })}><option value="">Pool: best</option>{options.profiles.filter((p) => p.tool === form.tool).map((p) => <option value={p.id} key={p.id}>{p.id} · {p.account_id}</option>)}</select></label>
    <label>Time limit (minutes)<input type="number" min="1" max="240" value={form.time_limit_min} onChange={(e) => update({ time_limit_min: Number(e.target.value) })} /></label>
    <div className="run-quota">Quota: {profile?.max_used_percent == null ? 'Unknown' : `${profile.max_used_percent}% used`}{verdict?.warnings.map((warning) => <p key={warning}>{warning}</p>)}{verdict?.suggestion && <p>Suggestion: {Object.values(verdict.suggestion).join(' · ')}</p>}</div>
    {verdict?.reasons.map((reason) => <p role="alert" key={reason}>{reason}</p>)}{error && <p role="alert">{error}</p>}
    {confirming && <p>Full auto lets the agent run commands without asking. Start anyway?</p>}
    <button className="run-primary" disabled={!verdict?.allowed || starting} onClick={start}>{starting ? 'Starting…' : confirming ? 'Start anyway' : 'Start run'}</button>
  </div></div>;
}
