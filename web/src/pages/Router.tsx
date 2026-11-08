/** 9router, the AI router: health, accounts and their rotation, limits and reset times, usage, models, settings,
 *  and its API keys (create, copy, pause, resume, delete through 9router's own API). */
import { useState } from 'react';
import { api, useApi } from '../api/client';
import { useMe } from '../api/me';
import type { Fleet } from '../api/types';
import { usePasswordCheck } from './AgentControl';
import type { FleetPlan } from '../api/types';
import { UsageFigure } from '../components/UsageFigure';
import { fmtRelative } from '../lib/format';
import { PLAN_NOTE, accountLines, resetsIn, unattributedLine, usageLabel, usageView } from '../lib/usage';
import { SortHeader, useSort } from '../lib/useSort';
import { GraphsToggle, TrendChart, useGraphs } from './Trends';
import './fleet.css';
import './control.css';
import './proxmox.css';
import './router.css';

export type RouterAccount = {
  id: string; provider: string; auth: string; name: string; email: string | null; priority: number | null; active: boolean;
  test_status: string | null; token_expires_at: number | null; last_error: string | null; last_error_at: number | null; error_code: string | null;
  backoff_level: number; rate_limited_until: number | null; limit: { window: string; left_pct: number; resets_at: number | null } | null;
  rotation_position: number | null; requests_24h: number; tokens_24h: number; last_used_at: number | null; auto_ping: boolean;
  auto_ping_how?: string | null; last_ping_at?: number | null;
};
type Row = { name: string; requests: number; tokens: number; cost: number };
export type RouterSnapshot = {
  found: boolean; vmid?: string; status?: string; up?: boolean; version?: string | null; checked_at?: number;
  services?: Record<string, string>; health?: number | string; headroom_health?: number | string;
  connections?: RouterAccount[]; in_use?: string | null; rotation_ran_at?: number | null;
  settings?: Record<string, unknown> & { autoPing?: { enabled: boolean; connections: Record<string, boolean>; schedules: Record<string, boolean> } };
  combos?: { name: string; kind: string | null; models: string }[]; api_keys?: RouterKey[];
  models?: Record<string, string[]>; catalog?: { models: number; providers: number; synced: number | null }; disabled_models?: Record<string, string>;
  usage?: { total: { requests: number; prompt_tokens: number; completion_tokens: number; cost: number; errors: number }; daily_tokens: number[]; days: number; by_model: Row[]; by_account: Row[]; by_key: Row[] };
  /** This week's Claude plan use, the same as /api/fleet's plan. */
  plan?: FleetPlan;
  errors?: string[];
};
export type RouterKey = {
  id: string; name: string; active: boolean; created: string; restricted: boolean; allow: string[]; vmid: number | null;
  requests_24h: number; requests_30d: number; tokens_30d: number; cost_30d: number; errors_30d: number; last_used_at: number | null;
  /** This week: share of the plans' weekly capacity, value at API prices, and dollars paid through API-key accounts. */
  plan_pct?: number | null; cost_week?: number | null; api_paid?: number | null;
};
export type KeyAssignment = { agents: string[]; tools: string[]; note: string };
export type RouterData = { configured: boolean; snapshot: RouterSnapshot | null; error: string | null; assignments?: Record<string, KeyAssignment> };
type AgentRef = { host: string; label: string };

/** The agent a key is for when nothing is saved: keys named ct<id>-... belong to the agent on that container. */
export function autoAgent(key: RouterKey, agents: AgentRef[]) {
  if (key.vmid === null) return null;
  return agents.find((a) => a.host === `ct${key.vmid}` || a.host.startsWith(`ct${key.vmid}-`)) ?? null;
}

function KeyEditor({ k, saved, agents, ensure, onDone }: { k: RouterKey; saved: KeyAssignment | undefined; agents: AgentRef[]; ensure: () => Promise<boolean>; onDone: (text: string, bad?: boolean) => void }) {
  const auto = autoAgent(k, agents);
  const [picked, setPicked] = useState<Set<string>>(new Set(saved?.agents ?? (auto ? [auto.host] : [])));
  const [tools, setTools] = useState((saved?.tools ?? []).join(', '));
  const [note, setNote] = useState(saved?.note ?? '');
  const [find, setFind] = useState('');
  const [busy, setBusy] = useState(false);
  const save = async (clear = false) => {
    if (!(await ensure())) return;
    setBusy(true);
    try {
      const body = clear ? { id: k.id } : { id: k.id, agents: [...picked], tools: tools.split(',').map((x) => x.trim()).filter(Boolean), note };
      await api('/api/router/keys', { method: 'POST', body: JSON.stringify(body) });
      onDone(clear ? `${k.name}: back to matching by its name.` : `${k.name}: saved.`);
    } catch (error) {
      onDone((error as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  const toggle = (host: string) => setPicked((old) => { const next = new Set(old); if (next.has(host)) next.delete(host); else next.add(host); return next; });
  const shown = agents.filter((a) => !find || `${a.label} ${a.host}`.toLowerCase().includes(find.toLowerCase()));
  return (
    <div className="router-key-editor" role="group" aria-label={`Assign ${k.name}`}>
      <label>Agents <input type="search" placeholder="Find an agent" value={find} onChange={(e) => setFind(e.target.value)} /></label>
      <div className="router-key-agents">
        {shown.map((a) => <label key={a.host} className="watch-check"><input type="checkbox" checked={picked.has(a.host)} onChange={() => toggle(a.host)} /> {a.label}</label>)}
      </div>
      <label>Other tools <input value={tools} placeholder="n8n, a script… separated by commas" onChange={(e) => setTools(e.target.value)} /></label>
      <label>Note <input value={note} maxLength={200} onChange={(e) => setNote(e.target.value)} /></label>
      <div className="control-buttons">
        <button type="button" className="plain-button" disabled={busy} onClick={() => save()}>{busy ? 'Saving…' : 'Save'}</button>
        {saved && <button type="button" className="plain-button" disabled={busy} onClick={() => save(true)}>Use the name match</button>}
      </div>
    </div>
  );
}

/** Put text on the clipboard; falls back to a hidden text box where the Clipboard API isn't allowed (plain http). */
export async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const box = document.createElement('textarea');
    box.value = text;
    box.setAttribute('readonly', '');
    box.style.position = 'fixed';
    box.style.opacity = '0';
    document.body.appendChild(box);
    box.select();
    const done = document.execCommand?.('copy') ?? false;
    box.remove();
    return done;
  }
}

/** The model list most restricted keys share (the agent keys), offered as the default for a new key. */
export function usualModels(keys: RouterKey[]) {
  const seen = new Map<string, number>();
  for (const k of keys) if (k.restricted && k.allow?.length) seen.set(JSON.stringify(k.allow), (seen.get(JSON.stringify(k.allow)) ?? 0) + 1);
  const best = [...seen.entries()].sort((a, b) => b[1] - a[1])[0];
  return best ? (JSON.parse(best[0]) as string[]) : [];
}

type KeyAnswer = { id?: string; name?: string; key?: string };
const keyAction = (body: Record<string, unknown>) => api<KeyAnswer>('/api/router/key-action', { method: 'POST', body: JSON.stringify(body) });

function NewKey({ keys, ensure, onDone }: { keys: RouterKey[]; ensure: () => Promise<boolean>; onDone: (text: string, bad?: boolean, made?: KeyAnswer) => void }) {
  const usual = usualModels(keys);
  const [name, setName] = useState('');
  const [limit, setLimit] = useState(usual.length > 0);
  const [busy, setBusy] = useState(false);
  const create = async () => {
    if (!name.trim() || !(await ensure())) return;
    setBusy(true);
    try {
      const made = await keyAction({ action: 'create', name: name.trim(), ...(limit && usual.length ? { access: { restricted: true, allow: usual } } : {}) });
      onDone(`${made.name ?? name} created.`, false, made);
    } catch (error) {
      onDone((error as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="router-key-editor" role="group" aria-label="New key">
      <label>Name <input value={name} maxLength={60} placeholder="ct310-intern, n8n-main…" onChange={(e) => setName(e.target.value)} /></label>
      {usual.length > 0 && <label className="watch-check router-inline"><input type="checkbox" checked={limit} onChange={(e) => setLimit(e.target.checked)} /> Only the agent keys’ models: {usual.join(', ')}</label>}
      <p className="muted router-small">Name it ct&lt;number&gt;-… and HQ links it to that agent.</p>
      <div className="control-buttons"><button type="button" className="plain-button" disabled={busy || !name.trim()} onClick={create}>{busy ? 'Creating…' : 'Create key'}</button></div>
    </div>
  );
}

function ApiKeys({ keys, plan, assignments, onSaved }: { keys: RouterKey[]; plan?: FleetPlan; assignments: Record<string, KeyAssignment>; onSaved: () => void }) {
  const { data: fleet } = useApi<Fleet>('/api/fleet?range=today', { live: false });
  const { data: control } = useApi<{ elevated_until?: number | null }>('/api/control', { live: false });
  const { ensure, dialog } = usePasswordCheck(control?.elevated_until);
  const manage = useMe().can('router.manage');
  const [open, setOpen] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [made, setMade] = useState<KeyAnswer | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [find, setFind] = useState('');
  const [note, setNote] = useState<{ text: string; bad?: boolean } | null>(null);
  const now = Date.now() / 1000;
  const agents: AgentRef[] = (fleet?.agents ?? []).map((a) => ({ host: a.host, label: a.label ?? a.host })).sort((a, b) => a.label.localeCompare(b.label));
  const label = (host: string) => agents.find((a) => a.host === host)?.label ?? host;
  const owners = (k: RouterKey) => {
    const saved = assignments[k.id];
    if (saved) return { list: [...saved.agents.map(label), ...saved.tools], note: saved.note };
    const auto = autoAgent(k, agents);
    return { list: auto ? [auto.label] : k.vmid !== null ? [`CT ${k.vmid} (not in HQ)`] : [], note: '' };
  };
  const filtered = keys.filter((k) => !find || `${k.name} ${owners(k).list.join(' ')}`.toLowerCase().includes(find.toLowerCase()));
  const week = (k: RouterKey) => usageView({ plan, planPct: k.plan_pct, apiPaid: k.api_paid, value: k.cost_week ?? 0 });
  const sorter = useSort(filtered, {
    columns: {
      key: (k) => k.name, for: (k) => owners(k).list.join(', ') || null, models: (k) => (k.restricted ? (k.allow ?? []).length : null),
      requests: (k) => k.requests_30d ?? 0, week: (k) => week(k).sort, last: (k) => k.last_used_at,
    },
    storageKey: 'router-keys',
  });
  const shown = sorter.rows;
  const before = unattributedLine(plan);
  const unused = keys.filter((k) => k.active && !k.requests_30d).length;
  const act = async (k: RouterKey, action: 'pause' | 'resume' | 'delete' | 'copy') => {
    if (!(await ensure())) return;
    setBusy(`${k.id}:${action}`);
    try {
      if (action === 'copy') {
        const { key } = await keyAction({ action: 'reveal', id: k.id });
        setNote(key && (await copyText(key)) ? { text: `${k.name}: copied.` } : { text: `${k.name}: the browser didn’t allow copying.`, bad: true });
      } else {
        await keyAction({ action, id: k.id });
        setNote({ text: `${k.name}: ${action === 'pause' ? 'paused. Agents using it get errors until it’s resumed' : action === 'resume' ? 'resumed' : 'deleted'}.` });
        setConfirm(null);
        onSaved();
      }
    } catch (error) {
      setNote({ text: (error as Error).message, bad: true });
    } finally {
      setBusy(null);
    }
  };
  return (
    <section aria-label="API keys">
      <div className="section-head"><h2>API keys</h2><span className="muted">{keys.length} keys · {keys.filter((k) => k.active).length} active · {unused} unused in 30 days</span></div>
      {note && <p className={note.bad ? 'control-error' : 'control-ok'} role="status">{note.text}</p>}
      {made?.key && (
        <div className="router-made" role="status">
          <span><b>{made.name}</b> is ready. Copy it now or later from its row.</span>
          <button type="button" className="plain-button" onClick={async () => setNote((await copyText(made.key!)) ? { text: `${made.name}: copied.` } : { text: 'The browser didn’t allow copying.', bad: true })}>Copy key</button>
          <button type="button" className="plain-button" onClick={() => setMade(null)}>Done</button>
        </div>
      )}
      <div className="updates-select">
        <input type="search" className="router-find" placeholder="Find a key, agent or tool" aria-label="Find a key" value={find} onChange={(e) => setFind(e.target.value)} />
        {manage && <button type="button" className="plain-button router-new" onClick={() => setAdding(!adding)}>{adding ? 'Close' : 'New key'}</button>}
      </div>
      {adding && <NewKey keys={keys} ensure={ensure} onDone={(text, bad, answer) => { setNote({ text, bad }); if (!bad) { setAdding(false); setMade(answer ?? null); onSaved(); } }} />}
      <div className="mini-table router-keys" role="table" aria-label="API keys">
        <div className="mini-row mini-headrow" role="row"><SortHeader sorter={sorter} col="key">Key</SortHeader><SortHeader sorter={sorter} col="for">For</SortHeader><SortHeader sorter={sorter} col="models">Models</SortHeader><SortHeader sorter={sorter} col="requests">Last 30 days</SortHeader><SortHeader sorter={sorter} col="week">{plan ? `${usageLabel(plan, 'Value')}, this week` : 'This week'}</SortHeader><SortHeader sorter={sorter} col="last">Last used</SortHeader><span role="columnheader" /></div>
        {shown.map((k) => {
          const who = owners(k);
          const doing = (a: string) => busy === `${k.id}:${a}`;
          return (
            <div key={k.id}>
              <div className="mini-row" role="row" data-off={!k.active || undefined}>
                <span role="cell"><b>{k.name}</b>{!k.active && <span className="router-paused"> paused</span>}</span>
                <span role="cell">{who.list.length ? who.list.join(', ') : <span className="muted">not assigned</span>}{who.note && <span className="muted" title={who.note}> · {who.note.slice(0, 40)}</span>}</span>
                <span role="cell" title={(k.allow ?? []).join(', ')}>{k.restricted ? ((k.allow ?? []).length ? `${(k.allow ?? []).length} allowed` : 'none allowed') : 'all'}</span>
                <span role="cell" className="num" title={`${num(k.tokens_30d ?? 0)} tokens`}>{num(k.requests_30d ?? 0)} req · {compact(k.tokens_30d ?? 0)} tok{k.errors_30d ? <span className="control-error"> · {k.errors_30d} errors</span> : null}</span>
                <span role="cell"><UsageFigure view={week(k)} compact /></span>
                <span role="cell" className="muted">{k.last_used_at ? fmtRelative(k.last_used_at, now) : 'never'}</span>
                <span role="cell" className="router-key-actions">{manage && <>
                  <button type="button" className="plain-button" aria-label={`Copy ${k.name}`} disabled={!!busy} onClick={() => act(k, 'copy')}>{doing('copy') ? '…' : 'Copy'}</button>
                  <button type="button" className="plain-button" aria-label={`${k.active ? 'Pause' : 'Resume'} ${k.name}`} disabled={!!busy} onClick={() => act(k, k.active ? 'pause' : 'resume')}>{doing('pause') || doing('resume') ? '…' : k.active ? 'Pause' : 'Resume'}</button>
                  <button type="button" className="plain-button" aria-label={`Assign ${k.name}`} onClick={() => setOpen(open === k.id ? null : k.id)}>{open === k.id ? 'Close' : 'Assign'}</button>
                  <button type="button" className="plain-button danger" aria-label={`Delete ${k.name}`} disabled={!!busy} onClick={() => setConfirm(confirm === k.id ? null : k.id)}>Delete</button>
                </>}</span>
              </div>
              {confirm === k.id && (
                <div className="router-confirm" role="alertdialog" aria-label={`Delete ${k.name}?`}>
                  <span>Delete <b>{k.name}</b>? Anything using it stops working, and it can’t be brought back. Pause it instead to stop it for now.</span>
                  <button type="button" className="plain-button danger" disabled={!!busy} onClick={() => act(k, 'delete')}>{doing('delete') ? 'Deleting…' : 'Delete key'}</button>
                  <button type="button" className="plain-button" onClick={() => setConfirm(null)}>Cancel</button>
                </div>
              )}
              {open === k.id && <KeyEditor k={k} saved={assignments[k.id]} agents={agents} ensure={ensure} onDone={(text, bad) => { setNote({ text, bad }); if (!bad) { setOpen(null); onSaved(); } }} />}
            </div>
          );
        })}
        {before && <div className="mini-row muted" role="row" title="Plan use this week that 9router can’t put on any key: accounts used through the old login pool before they came to 9router."><span role="cell">{before.label}</span><span role="cell" /><span role="cell" /><span role="cell" /><span role="cell" className="num">{before.value}</span></div>}
      </div>
      <p className="note">Key values stay hidden: Copy fetches one from 9router straight to your clipboard, after your password check. 9router doesn’t record who a key is for, so HQ links keys named ct&lt;number&gt;-… to that agent, and you can assign the rest, or more agents and other tools, here.</p>
      {dialog}
    </section>
  );
}

const num = (n: number) => n.toLocaleString('en-US');
const STRATEGY: Record<string, string> = { 'fill-first': 'Fill first: use one account until it’s limited, then the next', 'round-robin': 'Round robin: take turns', smart: 'Smart' };

/** "in 2 d 4 h" / "in 35 min" / "now" */
export const until = resetsIn;

function models(text: string) {
  try { const list = JSON.parse(text); return Array.isArray(list) ? list.map(String) : [text]; } catch { return [text]; }
}

function Breakdown({ title, rows }: { title: string; rows: Row[] }) {
  const sorter = useSort(rows, { columns: { name: (r) => r.name, requests: (r) => r.requests, tokens: (r) => r.tokens, cost: (r) => r.cost }, storageKey: `router-usage-${title}` });
  return (
    <div className="mini-table router-break" role="table" aria-label={title}>
      <div className="mini-row mini-headrow" role="row"><SortHeader sorter={sorter} col="name">{title}</SortHeader><SortHeader sorter={sorter} col="requests">Requests</SortHeader><SortHeader sorter={sorter} col="tokens">Tokens</SortHeader><SortHeader sorter={sorter} col="cost">Est. cost</SortHeader></div>
      {rows.length ? sorter.rows.slice(0, 12).map((r) => (
        <div className="mini-row" role="row" key={r.name}><span role="cell" title={r.name}>{r.name}</span><span role="cell" className="num">{num(r.requests)}</span><span role="cell" className="num">{num(r.tokens)}</span><span role="cell" className="num">${r.cost.toFixed(2)}</span></div>
      )) : <div className="mini-row" role="row"><span role="cell" className="muted">Nothing yet</span></div>}
    </div>
  );
}

/** 9router's accounts, in rotation order until a column is picked. */
function AccountsTable({ accounts, inUse, now }: { accounts: RouterAccount[]; inUse: string | null; now: number }) {
  const sorter = useSort(accounts, {
    columns: {
      position: (a) => a.rotation_position, account: (a) => a.name,
      status: (a) => (!a.active ? 'switched off' : a.rate_limited_until ? 'cooling down' : a.test_status ?? 'unknown'),
      left: (a) => a.limit?.left_pct, resets: (a) => a.limit?.resets_at, requests: (a) => a.requests_24h, ping: (a) => (a.auto_ping ? 1 : 0),
    },
    storageKey: 'router-accounts',
  });
  return (
    <div className="mini-table router-accounts" role="table" aria-label="Accounts">
      <div className="mini-row mini-headrow" role="row">
        <SortHeader sorter={sorter} col="position">#</SortHeader><SortHeader sorter={sorter} col="account">Account</SortHeader><SortHeader sorter={sorter} col="status">Status</SortHeader><SortHeader sorter={sorter} col="left">Limit left</SortHeader><SortHeader sorter={sorter} col="resets">Resets</SortHeader><SortHeader sorter={sorter} col="requests">Last 24 h</SortHeader><SortHeader sorter={sorter} col="ping">Auto-ping</SortHeader>
      </div>
      {sorter.rows.map((a) => {
        const bad = !a.active || (a.test_status && a.test_status !== 'active');
        const left = a.limit?.left_pct;
        return (
          <div className="mini-row" role="row" key={a.id} data-in-use={a.id === inUse || undefined}>
            <span role="cell" className="num">{a.rotation_position ?? '—'}</span>
            <span role="cell"><b>{a.name}</b>{a.id === inUse && <span className="router-badge">in use</span>}<span className="muted"> · {a.provider} {a.auth}</span></span>
            <span role="cell" className={bad ? 'control-error' : a.rate_limited_until ? 'behind' : ''}>
              {!a.active ? 'switched off' : a.rate_limited_until ? `cooling down, ${until(a.rate_limited_until, now)}` : a.test_status ?? 'unknown'}
              {a.backoff_level ? <span className="muted"> · backoff {a.backoff_level}</span> : null}
              {a.last_error ? <span className="muted" title={a.last_error}> · {a.last_error.slice(0, 60)}</span> : null}
            </span>
            <span role="cell">{left === undefined || left === null ? '—' : (
              <span className="router-limit"><span className="px-bar" role="meter" aria-label={`${a.name} ${a.limit?.window} left`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={left}><i className={left <= 10 ? 'bad' : left <= 30 ? 'warn' : ''} style={{ width: `${left}%` }} /></span><span className="num">{Math.round(left)}%</span><span className="muted">{a.limit?.window}</span></span>
            )}</span>
            <span role="cell">{until(a.limit?.resets_at, now)}</span>
            <span role="cell" className="num">{num(a.requests_24h)} req · {num(a.tokens_24h)} tok</span>
            <span role="cell" title={a.auto_ping_how ? `9router pings this account ${a.auto_ping_how === 'at reset' ? 'right after each limit reset' : a.auto_ping_how}` : undefined}>
              {a.auto_ping ? <><b className="behind">On</b><span className="muted"> · {a.last_ping_at ? `last ${fmtRelative(a.last_ping_at, now)}` : 'no ping yet'}</span></> : 'Off'}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/** This week's use of the Claude plans, for the Router's totals. */
function PlanStat({ plan, now }: { plan: FleetPlan; now: number }) {
  const view = usageView({ plan, planPct: plan.used_pct, apiPaid: plan.api_cost_week, value: plan.accounts.reduce((sum, a) => sum + (a.cost_week ?? 0), 0) });
  const before = unattributedLine(plan);
  const lines = view.mode === 'plan' || view.mode === 'mixed' ? accountLines(plan, now) : [];
  return (
    <div className="stat" title={view.mode === 'api' ? view.title : PLAN_NOTE}>
      <div className="k">{usageLabel(plan, 'Plan use')}, this week</div>
      <div className="v num">{view.main}</div>
      <div className="d num">{view.mode === 'api' ? view.secondary : `of the plans’ weekly limits · ${view.secondary}`}</div>
      {before && <div className="d num">{before.label}: {before.value}</div>}
      {lines.length > 0 && <ul className="plan-accounts num" aria-label="Plan accounts">{lines.map((line) => <li key={line} title={line}>{line}</li>)}</ul>}
    </div>
  );
}

/** {"claude": {"fallbackStrategy": "fill-first", "stickyRoundRobinLimit": 1}} -> "claude: fill-first, sticky 1" */
export function strategies(value: unknown) {
  if (!value || typeof value !== 'object') return undefined;
  return Object.entries(value as Record<string, Record<string, unknown>>).map(([name, s]) =>
    `${name}: ${s?.fallbackStrategy ?? '—'}${s?.stickyRoundRobinLimit ? `, sticky ${s.stickyRoundRobinLimit}` : ''}`).join('; ') || undefined;
}
const compact = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}k` : String(Math.round(n)));

function Setting({ label, value }: { label: string; value: unknown }) {
  const text = typeof value === 'boolean' ? (value ? 'On' : 'Off') : value === undefined || value === null || value === '' ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value);
  return <div className="router-setting"><span className="muted">{label}</span><b>{text}</b></div>;
}

export function RouterPage() {
  const { data, error, reload } = useApi<RouterData>('/api/router');
  const [graphs, setGraphs] = useGraphs('router');
  const now = Date.now() / 1000;
  const snap = data?.snapshot;
  const head = (text: string) => <div className="hq-page"><h1>Router</h1><p className="muted">{text}</p></div>;
  if (error && !data) return head('Couldn’t load the router reading. Check that HQ is running, then refresh.');
  if (data && !data.configured) return head('HQ needs its Proxmox access to read 9router. See “Control” in the HQ runbook.');
  if (!snap) return head(data?.error ? `Couldn’t read 9router: ${data.error}` : 'Reading 9router… this takes a few seconds after HQ starts.');
  if (!snap.found) return head('HQ didn’t find a container with “9router” in its name on the Proxmox host.');

  const accounts = [...(snap.connections ?? [])].sort((a, b) => (a.rotation_position ?? 99) - (b.rotation_position ?? 99) || (a.priority ?? 99) - (b.priority ?? 99));
  const ready = accounts.filter((a) => a.active && !a.rate_limited_until && (!a.test_status || a.test_status === 'active'));
  const inUse = accounts.find((a) => a.id === snap.in_use);
  const usage = snap.usage;
  const total = usage?.total;
  const settings = snap.settings ?? {};
  const pinged = accounts.filter((a) => a.auto_ping).length;
  const nextReset = accounts.map((a) => a.limit?.resets_at).filter((t): t is number => !!t && t > now).sort()[0];

  return (
    <div className="hq-page proxmox router">
      <header className="page-head">
        <div>
          <h1>Router</h1>
          <p className="lede">9router {snap.version ?? ''} on CT {snap.vmid} · {snap.up ? 'answering' : 'not answering'} · read {snap.checked_at ? fmtRelative(snap.checked_at, now) : '—'}. HQ manages API keys here; change accounts, combos and settings in 9router’s own dashboard.</p>
        </div>
        <GraphsToggle on={graphs} onChange={setGraphs} />
      </header>
      {snap.errors && snap.errors.length > 0 && <p className="sheet-error">Part of the reading failed: {snap.errors.join('; ')}</p>}
      {data?.error && <p className="sheet-error">The latest reading failed, so this is older: {data.error}</p>}

      <section className="stats stats-grid" aria-label="Router totals">
        <div className="stat"><div className="k">Status</div><div className={`v ${snap.up ? '' : 'control-error'}`}>{snap.up ? 'Up' : 'Down'}</div><div className="d">service {snap.services?.['9router'] ?? '—'} · Headroom {snap.services?.headroom ?? '—'}</div></div>
        <div className="stat"><div className="k">Accounts ready</div><div className="v num">{ready.length}/{accounts.length}</div><div className="d">{inUse ? `next request goes to ${inUse.name}` : 'none ready'}</div></div>
        <div className="stat"><div className="k">Next limit reset</div><div className="v">{until(nextReset, now)}</div><div className="d">{nextReset ? new Date(nextReset * 1000).toLocaleString([], { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : 'no reset time known'}</div></div>
        <div className="stat"><div className="k">Requests, {usage?.days ?? 30} days</div><div className="v num">{num(total?.requests ?? 0)}</div><div className="d num">{num((total?.prompt_tokens ?? 0) + (total?.completion_tokens ?? 0))} tokens · {total?.errors ?? 0} errors · est. ${(total?.cost ?? 0).toFixed(2)}</div></div>
        {snap.plan && snap.plan.billing !== 'none' && <PlanStat plan={snap.plan} now={now} />}
      </section>

      <section aria-label="Accounts">
        <div className="section-head"><h2>Accounts and rotation</h2><span className="muted">{STRATEGY[String(settings.fallbackStrategy)] ?? String(settings.fallbackStrategy ?? '')}{snap.rotation_ran_at ? ` · order checked ${fmtRelative(snap.rotation_ran_at, now)}` : ''}</span></div>
        <AccountsTable accounts={accounts} inUse={snap.in_use ?? null} now={now} />
        <p className="note">Limits and reset times are the ones 9router last recorded for each account. “In use” is the first ready account in rotation order: the one the next request goes to. Auto-ping: 9router sends a small request to an account right after its limit resets (or on a schedule), so the next limit window starts straight away. It’s on for {pinged} of {accounts.length} accounts and is switched per account in 9router’s dashboard.</p>
      </section>

      <section aria-label="Usage">
        <div className="section-head"><h2>Usage</h2><span className="muted">last {usage?.days ?? 30} days · costs are 9router’s estimates</span></div>
        {graphs && usage && <TrendChart title="Tokens per day" height={150} format={compact} win={{ start: (snap.checked_at ?? now) - usage.days * 86400, bucket_s: 86400, count: usage.days }}
          series={[{ key: 'tokens', label: 'Tokens', values: usage.daily_tokens, color: 'var(--working)', kind: 'bar' }]} />}
        <div className="router-breaks">
          <Breakdown title="Model" rows={usage?.by_model ?? []} />
          <Breakdown title="Account" rows={usage?.by_account ?? []} />
          <Breakdown title="API key" rows={usage?.by_key ?? []} />
        </div>
      </section>

      <ApiKeys keys={snap.api_keys ?? []} plan={snap.plan} assignments={data?.assignments ?? {}} onSaved={reload} />

      <section aria-label="Models">
        <div className="section-head"><h2>Models and combos</h2><span className="muted">{snap.catalog ? `catalog of ${num(snap.catalog.models)} models from ${snap.catalog.providers} providers` : ''}</span></div>
        {(snap.combos ?? []).map((c) => (
          <div className="router-combo" key={c.name}><b>{c.name}</b> <span className="muted">combo, tried in order:</span> {models(c.models).map((m) => <code key={m}>{m}</code>)}</div>
        ))}
        {Object.entries(snap.models ?? {}).map(([provider, list]) => (
          <p className="muted" key={provider}>{provider}: {list.length ? list.join(', ') : 'models come from the account itself'}</p>
        ))}
        {Object.keys(snap.disabled_models ?? {}).length > 0 && <p className="muted">Switched off: {Object.entries(snap.disabled_models ?? {}).map(([k, v]) => `${k}: ${v}`).join('; ')}</p>}
      </section>

      <section aria-label="Settings">
        <div className="section-head"><h2>Settings</h2></div>
        <div className="router-settings">
          <Setting label="Routing" value={settings.fallbackStrategy} />
          <Setting label="Per provider" value={strategies(settings.providerStrategies)} />
          <Setting label="Combos" value={strategies(settings.comboStrategies)} />
          <Setting label="Headroom compression" value={settings.headroomEnabled} />
          <Setting label="Headroom code-aware" value={settings.headroomCodeAware} />
          <Setting label="Caveman" value={settings.cavemanEnabled ? `on (${settings.cavemanLevel})` : false} />
          <Setting label="Ponytail" value={settings.ponytailEnabled ? `on (${settings.ponytailLevel})` : false} />
          <Setting label="Auto-ping" value={pinged ? `on for ${pinged} of ${accounts.length} accounts` : false} />
        </div>
      </section>
    </div>
  );
}
