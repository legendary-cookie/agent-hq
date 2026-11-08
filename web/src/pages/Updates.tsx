import { useEffect, useState } from 'react';
import { api, useApi } from '../api/client';
import { useMe } from '../api/me';
import type { Fleet } from '../api/types';
import { fmtRelative } from '../lib/format';
import { usePasswordCheck } from './AgentControl';
import { PickBox } from '../components/PickBox';
import { SortHeader, useSort } from '../lib/useSort';
import './fleet.css';
import './control.css';
import './insights.css';
import './updates.css';

export type Tool = 'claude' | 'zylos' | 'hermes' | 'codex';
export const TOOLS: Tool[] = ['claude', 'zylos', 'hermes', 'codex'];
export interface AgentVersions {
  ok: boolean; error?: string; debian?: string | null; list_days?: number | null; pending?: number | null; security?: number | null; reboot?: boolean;
  node?: string | null; claude?: string | null; zylos?: string | null; hermes?: string | null; codex?: string | null; updates_held?: boolean;
  claude_behind?: boolean; zylos_behind?: boolean; hermes_behind?: boolean; codex_behind?: boolean;
}
export interface Job { id: string; host: string; kind: Tool | 'security'; version: string | null; state: 'running' | 'done' | 'failed'; steps: { at: number; text: string }[]; started_at: number; ended_at: number | null; snapshot: string | null; error: string | null }
export interface Schedule { mode: 'every' | 'daily' | 'weekly'; hours: number; time: string; day: number }
export interface UpdateSettings { snapshot: boolean; cleanup: 'keep' | 'after_check' | 'after_hours'; cleanup_hours: number; schedule: Schedule }
export interface UpdatesData {
  configured: boolean; checked_at: number | null; checking: boolean; agents: Record<string, AgentVersions>;
  latest: { claude_stable?: string; claude_latest?: string; zylos_latest?: string; hermes_latest?: string; codex_latest?: string };
  totals: { checked: number; claude_behind: number; zylos_behind: number; hermes_behind?: number; codex_behind?: number; security: number; reboot: number; failed: number } & Partial<Record<`${Tool}_installed`, number>>;
  jobs: Job[];
  settings?: UpdateSettings;
  next_check_at?: number;
}
const KIND = { claude: 'Claude Code', zylos: 'Zylos', hermes: 'Hermes', codex: 'Codex', security: 'Security updates' };
const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const DEFAULTS: UpdateSettings = { snapshot: true, cleanup: 'keep', cleanup_hours: 24, schedule: { mode: 'every', hours: 12, time: '03:00', day: 0 } };

export function scheduleText(schedule: Schedule) {
  if (schedule.mode === 'daily') return `every day at ${schedule.time}`;
  if (schedule.mode === 'weekly') return `every ${DAYS[schedule.day]} at ${schedule.time}`;
  return schedule.hours === 1 ? 'every hour' : `every ${schedule.hours} hours`;
}

/** The newest version to offer for a tool, or null when HQ updates it with the tool's own updater. */
function targetOf(latest: UpdatesData['latest'], tool: Tool) {
  if (tool === 'claude') return latest.claude_stable ?? latest.claude_latest ?? null;
  if (tool === 'codex') return latest.codex_latest ?? null;
  return null;
}

/** Which agents a tool update applies to: those whose last check found the tool. Claude Code and Codex
 * install a set version, so agents already on it or newer are left out too, rather than downgraded. */
export function withTool(agents: Record<string, AgentVersions>, hosts: string[], tool: Tool) {
  return hosts.filter((host) => agents[host]?.ok && !!agents[host]?.[tool] && (tool === 'zylos' || tool === 'hermes' || !!agents[host]?.[`${tool}_behind`]));
}

function Snapshots({ host, label, ensure, onDone }: { host: string; label: string; ensure: () => Promise<boolean>; onDone: (text: string, bad?: boolean) => void }) {
  const { data, reload } = useApi<{ snapshots: { name: string; taken_at: number | null; description: string }[] }>(`/api/updates/snapshots?host=${encodeURIComponent(host)}`, { live: false });
  const [busy, setBusy] = useState<string | null>(null);
  const act = async (path: string, name: string, question: string, done: string) => {
    if (!window.confirm(`${label}: ${question}`) || !(await ensure())) return;
    setBusy(name);
    try {
      await api(path, { method: 'POST', body: JSON.stringify({ host, name }) });
      onDone(done);
      reload();
    } catch (error) {
      onDone((error as Error).message, true);
    } finally {
      setBusy(null);
    }
  };
  if (!data) return <p className="muted">Loading snapshots…</p>;
  if (!data.snapshots.length) return <p className="muted">No snapshots.</p>;
  return (
    <div className="snapshots">
      {data.snapshots.map((snap) => (
        <div className="list-row" key={snap.name}>
          <span><b>{snap.name}</b>{snap.description ? <span className="muted"> · {snap.description}</span> : null}{snap.taken_at ? <span className="muted"> · {fmtRelative(snap.taken_at, Date.now() / 1000)}</span> : null}</span>
          <span className="control-buttons">
            <button type="button" className="plain-button danger" disabled={!!busy} onClick={() => act('/api/updates/rollback', snap.name, `Roll back to ${snap.name}? The container restarts, and everything changed since the snapshot, including the agent's memory and files, goes back to how it was then.`, `${label} rolled back to ${snap.name}.`)}>{busy === snap.name ? 'Working…' : 'Roll back'}</button>
            <button type="button" className="plain-button" disabled={!!busy} onClick={() => act('/api/updates/delete-snapshot', snap.name, `Delete snapshot ${snap.name}? You can't roll back to it afterwards.`, `Snapshot ${snap.name} deleted.`)}>Delete</button>
          </span>
        </div>
      ))}
    </div>
  );
}

function UpdateSettingsPanel({ settings, ensure, onDone }: { settings: UpdateSettings; ensure: () => Promise<boolean>; onDone: (text: string, bad?: boolean) => void }) {
  const [form, setForm] = useState<UpdateSettings>(settings);
  const [saving, setSaving] = useState(false);
  useEffect(() => setForm(settings), [settings]);
  const schedule = (change: Partial<Schedule>) => setForm({ ...form, schedule: { ...form.schedule, ...change } });
  const save = async () => {
    if (!(await ensure())) return;
    setSaving(true);
    try {
      await api('/api/updates/settings', { method: 'POST', body: JSON.stringify(form) });
      onDone('Update settings saved.');
    } catch (error) {
      onDone((error as Error).message, true);
    } finally {
      setSaving(false);
    }
  };
  return (
    <section className="updates-settings" aria-label="Update settings">
      <h2>Settings</h2>
      <label className="watch-check"><input type="checkbox" checked={form.snapshot} onChange={(event) => setForm({ ...form, snapshot: event.target.checked })} /> Take a snapshot before each update</label>
      <label>Delete HQ’s snapshots
        <select value={form.cleanup} disabled={!form.snapshot} onChange={(event) => setForm({ ...form, cleanup: event.target.value as UpdateSettings['cleanup'] })}>
          <option value="keep">Never, I delete them myself</option>
          <option value="after_check">As soon as the agent passes its checks</option>
          <option value="after_hours">After a wait, if the agent is still fine</option>
        </select>
      </label>
      {form.cleanup === 'after_hours' && form.snapshot && (
        <label>Wait (hours)
          <input type="number" min={1} max={720} value={form.cleanup_hours} onChange={(event) => setForm({ ...form, cleanup_hours: Number(event.target.value) })} />
        </label>
      )}
      <label>Check for new versions
        <select value={form.schedule.mode} onChange={(event) => schedule({ mode: event.target.value as Schedule['mode'] })}>
          <option value="every">Every few hours</option>
          <option value="daily">Daily</option>
          <option value="weekly">Weekly</option>
        </select>
      </label>
      {form.schedule.mode === 'every' && (
        <label>Every (hours)
          <input type="number" min={1} max={168} value={form.schedule.hours} onChange={(event) => schedule({ hours: Number(event.target.value) })} />
        </label>
      )}
      {form.schedule.mode === 'weekly' && (
        <label>Day
          <select value={form.schedule.day} onChange={(event) => schedule({ day: Number(event.target.value) })}>
            {DAYS.map((day, index) => <option key={day} value={index}>{day}</option>)}
          </select>
        </label>
      )}
      {form.schedule.mode !== 'every' && (
        <label>Time
          <input type="time" value={form.schedule.time} onChange={(event) => schedule({ time: event.target.value })} />
        </label>
      )}
      <div className="control-buttons"><button type="button" className="plain-button" disabled={saving} onClick={save}>{saving ? 'Saving…' : 'Save settings'}</button></div>
      <p className="note">Checks after an update: the container is still running and HQ can still read the agent’s versions. Each agent is checked again as soon as its update ends, so its row is up to date. A snapshot waiting to be deleted is kept if the agent fails its checks at that time.</p>
    </section>
  );
}

export function Updates() {
  const { data, reload } = useApi<UpdatesData>('/api/updates');
  const { data: fleet } = useApi<Fleet>('/api/fleet?range=today', { live: false });
  const { data: control } = useApi<{ elevated_until?: number | null }>('/api/control', { live: false });
  const { ensure, dialog } = usePasswordCheck(control?.elevated_until);
  const { can } = useMe();
  const mayRun = can('updates.run');
  const [note, setNote] = useState<{ text: string; bad?: boolean } | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [showSettings, setShowSettings] = useState(false);
  // Per run; starts from the saved setting.
  const [snapshot, setSnapshot] = useState<boolean | null>(null);
  // Set the moment an update is chosen, so a second click can't start it twice before the server answers.
  const [launching, setLaunching] = useState<string | null>(null);
  const running = data?.checking || !!launching || data?.jobs.some((job) => job.state === 'running');
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(reload, 3000);
    return () => window.clearInterval(timer);
  }, [running, reload]);
  const label = (host: string) => fleet?.agents.find((agent) => agent.host === host)?.label ?? host;
  const info = (host: string) => (data?.agents[host]?.ok ? data.agents[host] : null);
  // Sorted by name until a column is picked; groups keep their order, rows sort within each group.
  const sorter = useSort(Object.keys(data?.agents ?? {}).sort((a, b) => label(a).localeCompare(label(b))), {
    columns: {
      agent: label, security: (host) => info(host)?.security, lists: (host) => info(host)?.list_days,
      ...Object.fromEntries(TOOLS.map((tool) => [tool, (host: string) => info(host)?.[tool]])),
    },
    storageKey: 'updates',
  });
  if (!data) return <div className="hq-page"><h1>Updates</h1><p className="muted">Loading…</p></div>;
  const settings = data.settings ?? DEFAULTS;
  const takeSnapshot = snapshot ?? settings.snapshot;
  const hosts = sorter.rows;
  // Only tools found on at least one agent get a column, a count and a button. Codex appears once an agent has it.
  const tools = TOOLS.filter((tool) => hosts.some((host) => data.agents[host]?.[tool]));
  // Sections follow the fleet's own group order (Management, Department, Staff, ...).
  const groupOf = (host: string) => fleet?.agents.find((agent) => agent.host === host)?.group || 'Other';
  const order = [...new Set([...(fleet?.agents ?? []).map((agent) => agent.group || 'Other'), 'Other'])];
  const groups = order.map((name) => ({ name, hosts: hosts.filter((host) => groupOf(host) === name) })).filter((group) => group.hosts.length);
  const say = (text: string, bad?: boolean) => { setNote({ text, bad }); reload(); };
  const startUpdate = async (picked: string[], kind: Tool | 'security', version?: string | null) => {
    if (!picked.length || launching) return;
    const list = kind === 'security' ? picked : withTool(data.agents, picked, kind);
    const skipped = picked.filter((host) => !list.includes(host));
    if (!list.length) return;
    const what = version ? `${KIND[kind]} ${version}` : KIND[kind];
    const names = list.map(label).join(', ');
    const how = { claude: 'Staff agents restart their Claude session to use it; others pick it up at their next container restart.', codex: 'Installs it with npm.', zylos: 'Runs zylos upgrade --self.', hermes: 'Runs hermes update, which pulls the newest Hermes and its dependencies.', security: 'Debian security and other package updates are installed.' }[kind];
    const snap = takeSnapshot ? 'Each agent gets a snapshot first, so you can roll back.' : 'No snapshot is taken, so this can’t be rolled back.';
    const reason = kind === 'claude' || kind === 'codex' ? `${KIND[kind]} isn’t installed there or is already on ${version} or newer` : `${KIND[kind]} isn’t installed there`;
    const skip = skipped.length ? `\n\nSkipped, because ${reason}: ${skipped.map(label).join(', ')}.` : '';
    if (!window.confirm(`Install ${what} on ${names}?\n\n${snap} ${how} Agents are updated one at a time.${skip}`)) return;
    setLaunching(list[0]);
    try {
      if (!(await ensure())) return;
      setNote(null);
      for (const host of list) {
        setLaunching(host);
        try {
          await api('/api/updates/start', { method: 'POST', body: JSON.stringify({ host, kind, version: version ?? undefined, snapshot: takeSnapshot }) });
          // One at a time: wait for this agent before starting the next, and stop at the first failure.
          for (;;) {
            await new Promise((resolve) => setTimeout(resolve, 3000));
            const now = await api<UpdatesData>('/api/updates');
            const job = now.jobs.find((item) => item.host === host);
            if (job && job.state !== 'running') {
              if (job.state === 'failed') { say(`${label(host)}: ${job.error}. Stopped before the remaining agents.`, true); return; }
              break;
            }
          }
        } catch (error) {
          say(`${label(host)}: ${(error as Error).message}`, true);
          return;
        }
      }
      say(`${what} installed on ${names}.${skipped.length ? ` Skipped ${skipped.map(label).join(', ')}.` : ''}`);
      setSelected(new Set());
    } finally {
      setLaunching(null);
    }
  };
  const check = async () => {
    try { await api('/api/updates/check', { method: 'POST', body: '{}' }); reload(); }
    catch (error) { setNote({ text: (error as Error).message, bad: true }); }
  };
  const toggle = (host: string) => setSelected((old) => { const next = new Set(old); if (next.has(host)) next.delete(host); else next.add(host); return next; });
  const chosen = [...selected];
  const now = Date.now() / 1000;
  const columns = `28px minmax(0, 1.4fr) ${tools.map(() => '1fr').join(' ')} .8fr .7fr 96px`;
  return (
    <div className="hq-page">
      <header className="page-head">
        <div>
          <h1>Updates</h1>
          <p className="lede">Versions on every agent, checked {scheduleText(settings.schedule)} and after each update. {settings.snapshot ? 'Updates take a snapshot first, so each one can be rolled back.' : 'Snapshots before updates are switched off.'}</p>
        </div>
        <div className="control-buttons">
          <span className="muted">{data.checking ? 'Checking now…' : data.checked_at ? `Checked ${fmtRelative(data.checked_at, now)}` : 'Not checked yet'}{!data.checking && data.next_check_at && data.next_check_at > now + 60 ? ` · next ${new Date(data.next_check_at * 1000).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' })}` : ''}</span>
          <button type="button" className="plain-button" disabled={data.checking || !data.configured} onClick={check}>Check now</button>
          {mayRun && <button type="button" className="plain-button" aria-expanded={showSettings} onClick={() => setShowSettings(!showSettings)}>Settings</button>}
        </div>
      </header>
      {!data.configured && <p className="muted">Updates need HQ’s Proxmox access. See “Control” in the HQ runbook.</p>}
      {showSettings && <UpdateSettingsPanel settings={settings} ensure={ensure} onDone={say} />}
      <section className="stats stats-grid" aria-label="Update measures">
        {tools.map((tool) => {
          const target = tool === 'claude' ? targetOf(data.latest, tool) : tool === 'zylos' ? data.latest.zylos_latest : tool === 'hermes' ? data.latest.hermes_latest : data.latest.codex_latest;
          const installed = data.totals[`${tool}_installed`] ?? hosts.filter((host) => data.agents[host]?.[tool]).length;
          return <div className="stat" key={tool}><div className="k">{KIND[tool]} behind</div><div className="v num">{data.totals[`${tool}_behind`] ?? 0}</div><div className="d">of {installed} agents · {tool === 'claude' ? 'stable' : 'latest'} is {target ?? 'unknown'}</div></div>;
        })}
        <div className="stat"><div className="k">Security updates waiting</div><div className="v num">{data.totals.security}</div><div className="d">{data.totals.reboot} agents need a restart to finish earlier updates</div></div>
      </section>
      {note && <p className={note.bad ? 'control-error' : 'control-ok'} role="status">{note.text}</p>}
      <div className="updates-select">
        <button type="button" className="plain-button" onClick={() => setSelected(selected.size === hosts.length ? new Set() : new Set(hosts))}>{selected.size === hosts.length && hosts.length ? 'Clear selection' : 'Select all'}</button>
        {groups.map((group) => <button key={group.name} type="button" className="plain-button" onClick={() => setSelected(new Set([...selected, ...group.hosts]))}>+ {group.name} ({group.hosts.length})</button>)}
      </div>
      {chosen.length > 0 && mayRun && (
        <div className="updates-bulk" role="toolbar" aria-label="Update selected agents">
          <b>{chosen.length} selected</b>
          {tools.map((tool) => {
            const has = withTool(data.agents, chosen, tool).length;
            if (!has) return null;  // none of the selected agents has this tool
            const version = targetOf(data.latest, tool);
            if ((tool === 'claude' || tool === 'codex') && !version) return null;
            const text = tool === 'claude' || tool === 'codex' ? `${KIND[tool]} ${version}` : `Update ${KIND[tool]}`;
            return <button key={tool} type="button" className="plain-button" disabled={!!running} title={has < chosen.length ? `${has} of the ${chosen.length} selected need this; the others don’t have ${KIND[tool]} or are already up to date` : undefined} onClick={() => startUpdate(chosen, tool, version)}>{text}{has < chosen.length ? ` (${has})` : ''}</button>;
          })}
          <button type="button" className="plain-button" disabled={!!running} onClick={() => startUpdate(chosen, 'security')}>Security updates</button>
          <label className="updates-snap"><input type="checkbox" checked={takeSnapshot} disabled={!!running} onChange={(event) => setSnapshot(event.target.checked)} /> Snapshot first</label>
          <button type="button" className="plain-button" onClick={() => setSelected(new Set())}>Clear</button>
          <span className="muted">Tip: try one quiet agent first, such as Intern.</span>
        </div>
      )}
      <div className="mini-table updates-table" role="table" aria-label="Versions per agent" style={{ ['--update-cols' as string]: columns }}>
        <div className="mini-row mini-headrow" role="row"><span role="columnheader"><PickBox hosts={hosts} picked={selected} onChange={setSelected} label="Select every agent" /></span><SortHeader sorter={sorter} col="agent">Agent</SortHeader>{tools.map((tool) => <SortHeader sorter={sorter} col={tool} key={tool}>{KIND[tool]}</SortHeader>)}<SortHeader sorter={sorter} col="security">Security</SortHeader><SortHeader sorter={sorter} col="lists">Lists</SortHeader><span role="columnheader" /></div>
        {groups.map((group) => [
          <div className="mini-row updates-group" role="row" key={`g-${group.name}`}>
            <span role="cell"><PickBox hosts={group.hosts} picked={selected} onChange={setSelected} label={`Select every ${group.name} agent`} /></span>
            <span role="cell" className="updates-group-name">{group.name} <span className="muted">{group.hosts.length}</span></span>
          </div>,
          ...group.hosts.map((host) => {
            const info = data.agents[host];
            const job = data.jobs.find((item) => item.host === host);
            const updating = launching === host || job?.state === 'running';
            return (
              <div key={host}>
                <div className="mini-row" role="row" data-updating={updating || undefined}>
                  <span role="cell"><input type="checkbox" aria-label={`Select ${label(host)}`} checked={selected.has(host)} disabled={updating} onChange={() => toggle(host)} /></span>
                  <span role="cell">{label(host)}{updating && <span className="updating" role="status"><i className="spin" aria-hidden />Updating…</span>}</span>
                  {info.ok ? <>
                    {tools.map((tool) => (
                      <span role="cell" key={tool} className={info[`${tool}_behind`] ? 'behind' : ''}>{info[tool] ?? <span className="muted" title={`${KIND[tool]} isn’t installed on this agent`}>—</span>}{tool === 'claude' && info.claude && info.updates_held ? <span className="muted" title="Automatic updates are switched off on this agent"> · held</span> : null}</span>
                    ))}
                    <span role="cell" className={info.security ? 'behind' : ''}>{info.security ?? '—'}{info.reboot ? <span className="muted"> · restart</span> : null}</span>
                    <span role="cell" className="muted">{info.list_days === null || info.list_days === undefined ? '—' : `${info.list_days} d old`}</span>
                  </> : <span role="cell" className="control-error updates-wide" style={{ gridColumn: `span ${tools.length + 2}` }}>{info.error}</span>}
                  <span role="cell"><button type="button" className="plain-button" onClick={() => setOpen(open === host ? null : host)}>{open === host ? 'Hide' : 'Snapshots'}</button></span>
                </div>
                {job && <div className={`updates-job ${job.state}`}><b>{KIND[job.kind]}{job.version ? ` ${job.version}` : ''}:</b> {job.steps[job.steps.length - 1]?.text ?? 'Starting…'}</div>}
                {open === host && <Snapshots host={host} label={label(host)} ensure={ensure} onDone={say} />}
              </div>
            );
          }),
        ])}
      </div>
      <p className="note">“—” means the tool isn’t installed on that agent, so it gets no update for it. “Held” means Claude Code’s own automatic updates are switched off on that agent. HQ leaves that setting alone and installs the version you choose. Package lists older than a few days make the security count too low; installing security updates refreshes them first. Snapshots take space on the Proxmox storage; Settings can delete them automatically once an update has settled.</p>
      {dialog}
    </div>
  );
}
