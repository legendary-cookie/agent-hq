import { useEffect, useId, useRef, useState } from 'react';
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
  const id = useId();
  const [closing, setClosing] = useState(false);
  const motion = useRef({ value: 0, velocity: 0 });
  const closeCallback = useRef(onClose);
  closeCallback.current = onClose;
  const hasTools = Object.keys(options.tools).length > 0;
  const version = useRef(0);
  useEffect(() => {
    const generation = ++version.current;
    setVerdict(null);
    setError('');
    if (!options.run_mode || !hasTools || !form.prompt.trim() || closing) return;
    const timer = setTimeout(() => {
      prepareRun(form).then((result) => { if (version.current === generation) { setVerdict(result.verdict); setError(''); } }).catch((err: Error) => { if (version.current === generation) setError(err.message); });
    }, 300);
    return () => clearTimeout(timer);
  }, [form, options.run_mode, hasTools, closing]);
  useEffect(() => {
    if (!options.run_mode || !sheet.current) return;
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    const target = closing ? 0 : 1;
    const step = spring(motion.current.value, target, motion.current.velocity);
    let last: number | null = null;
    let frame = 0;
    const animate = (now: number) => {
      const dt = last === null ? 0 : Math.max(0, Math.min((now - last) / 1000, 0.05));
      last = now;
      const result = reduced
        ? { value: Math.max(0, Math.min(1, motion.current.value + (closing ? -1 : 1) * dt / 0.15)), velocity: 0, done: false }
        : step(dt);
      motion.current = { value: result.value, velocity: result.velocity };
      if (sheet.current) {
        sheet.current.style.transform = reduced ? '' : `translateY(${(1 - result.value) * 24}px) scale(${0.96 + result.value * 0.04})`;
        sheet.current.style.opacity = String(result.value);
      }
      if (result.done || (reduced && result.value === target)) {
        if (closing) closeCallback.current();
      } else frame = requestAnimationFrame(animate);
    };
    frame = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(frame);
  }, [options.run_mode, closing]);
  const close = () => { if (options.run_mode) setClosing(true); else onClose(); };
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
      onStarted?.(); close();
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); } finally { setStarting(false); }
  }
  if (!options.run_mode) return <div className="run-popover" role="status">Run mode is off. Turn it on in config/run.json. <button onClick={onClose} aria-label="Close">×</button></div>;
  const profile = options.profiles.find((item) => item.id === (form.profile ?? verdict?.profile));
  return <div className="run-scrim" onClick={close}><div className="run-sheet" style={{ opacity: 0 }} ref={sheet} role="dialog" aria-modal="true" aria-label="Start run" onClick={(e) => e.stopPropagation()}>
    <header><h2>Start run</h2><button onClick={close} aria-label="Close">×</button></header>
    {!hasTools ? <p>No agent tools are ready on this PC yet. Tools appear after a real test run (see Settings).</p> : <>
    <label htmlFor={`${id}-tool`}>Tool<select id={`${id}-tool`} value={form.tool} onChange={(e) => { const tool = e.target.value; update({ tool, profile: null, level: options.tools[tool]?.levels[0] ?? '' }); }}>{Object.keys(options.tools).map((tool) => <option key={tool}>{tool}</option>)}</select></label>
    <label htmlFor={`${id}-model`}>Model (optional)<input id={`${id}-model`} value={form.model ?? ''} onChange={(e) => update({ model: e.target.value || null })} /></label>
    <label htmlFor={`${id}-project`}>Project<select id={`${id}-project`} value={form.project} onChange={(e) => update({ project: e.target.value })}>{options.projects.map((project) => <option key={project}>{project}</option>)}</select></label>
    <label htmlFor={`${id}-task`}>Task<textarea id={`${id}-task`} maxLength={100000} value={form.prompt} onChange={(e) => update({ prompt: e.target.value })} /></label>
    <label htmlFor={`${id}-level`}>Level<select id={`${id}-level`} value={form.level} onChange={(e) => update({ level: e.target.value })}>{(options.tools[form.tool]?.levels ?? []).map((level) => <option key={level} value={level}>{levels[level] ?? level}</option>)}</select></label>
    <label htmlFor={`${id}-guard`}>Guard mode<select id={`${id}-guard`} value={form.guard_mode} onChange={(e) => update({ guard_mode: e.target.value })}>{Object.entries(guards).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
    <label htmlFor={`${id}-account`}>Account<select id={`${id}-account`} value={form.profile ?? ''} onChange={(e) => update({ profile: e.target.value || null })}><option value="">Pool: best</option>{options.profiles.filter((p) => p.tool === form.tool).map((p) => <option value={p.id} key={p.id}>{p.id} · {p.account_id}</option>)}</select></label>
    <label htmlFor={`${id}-time`}>Time limit (minutes)<input id={`${id}-time`} type="number" min="1" max="240" value={form.time_limit_min} onChange={(e) => update({ time_limit_min: Number(e.target.value) })} /></label>
    <div className="run-quota">Quota: {profile?.max_used_percent == null ? 'Unknown' : `${profile.max_used_percent}% used`}{verdict?.warnings.map((warning) => <p key={warning}>{warning}</p>)}{verdict?.suggestion && <p>Suggested: {String(verdict.suggestion.tool ?? '')} · {String(verdict.suggestion.model ?? '')}{verdict.suggestion.profile ? ` (profile ${String(verdict.suggestion.profile)})` : ''}</p>}</div>
    {!form.prompt.trim() && <p className="run-hint">Describe the task to start</p>}
    {verdict?.reasons.map((reason) => <p role="alert" key={reason}>{reason}</p>)}{error && <p role="alert">{error}</p>}
    {confirming && <p>Full auto lets the agent run commands without asking. Start anyway?</p>}
    <button className="run-primary" disabled={!form.prompt.trim() || !verdict?.allowed || starting || closing} onClick={start}>{starting ? 'Starting…' : confirming ? 'Start anyway' : 'Start run'}</button>
    </>}
  </div></div>;
}
