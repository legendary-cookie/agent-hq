/** Alerts to Telegram, the warnings HQ is watching right now, and automatic Claude restarts. */
import { useEffect, useState } from 'react';
import { api, useApi } from '../api/client';
import { fmtRelative } from '../lib/format';
import { usePasswordCheck } from './AgentControl';
import './control.css';
import './watch.css';

export type Warning = { kind: string; host: string; label: string; text: string; since: number };
export type WatchSettings = {
  enabled: boolean; chat_id: string; has_token: boolean; ready: boolean; kinds: Record<string, boolean>; kind_names: Record<string, string>;
  auto_restart: boolean; cooldown_min: number; disk_pct: number; memory_pct: number; backup_hours?: number;
};
export type WatchData = { warnings: Warning[]; checked_at: number | null; settings: WatchSettings; restarts: Record<string, number[]> };

/** The problems HQ sees right now, for the top of Overview. Nothing is shown when all is well. */
export function HealthWarnings() {
  const { data } = useApi<WatchData>('/api/watch');
  const [open, setOpen] = useState(false);
  const warnings = data?.warnings ?? [];
  if (!warnings.length) return null;
  const shown = open ? warnings : warnings.slice(0, 4);
  const now = Date.now() / 1000;
  return (
    <section className="health" aria-label="Health warnings">
      <div className="health-head">
        <strong>{warnings.length} {warnings.length === 1 ? 'thing needs' : 'things need'} a look</strong>
        <a href="#/activity">Activity</a>
      </div>
      <ul>
        {shown.map((warning) => (
          <li key={`${warning.kind}:${warning.host}`} data-kind={warning.kind}>
            <span>{warning.text}</span>
            <small className="muted">since {fmtRelative(warning.since, now)}</small>
          </li>
        ))}
      </ul>
      {warnings.length > 4 && <button type="button" className="health-more" onClick={() => setOpen(!open)}>{open ? 'Show fewer' : `Show all ${warnings.length}`}</button>}
    </section>
  );
}

export function AlertSettingsPanel() {
  const { data, reload } = useApi<WatchData>('/api/watch', { live: false });
  const { data: control } = useApi<{ elevated_until?: number | null }>('/api/control', { live: false });
  const { ensure, dialog } = usePasswordCheck(control?.elevated_until);
  const [form, setForm] = useState<WatchSettings | null>(null);
  const [token, setToken] = useState('');
  const [note, setNote] = useState<{ text: string; bad?: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (data && !form) setForm(data.settings); }, [data, form]);
  if (!form) return null;
  const set = (patch: Partial<WatchSettings>) => setForm({ ...form, ...patch });
  const save = async () => {
    if (!(await ensure())) return;
    setBusy(true); setNote(null);
    try {
      const body = { enabled: form.enabled, chat_id: form.chat_id, kinds: form.kinds, auto_restart: form.auto_restart, cooldown_min: form.cooldown_min,
        disk_pct: form.disk_pct, memory_pct: form.memory_pct, ...(form.backup_hours !== undefined ? { backup_hours: form.backup_hours } : {}), ...(token.trim() ? { bot_token: token.trim() } : {}) };
      const saved = await api<WatchSettings>('/api/watch/settings', { method: 'POST', body: JSON.stringify(body) });
      setForm(saved); setToken(''); reload();
      setNote({ text: saved.enabled && !saved.ready ? 'Saved. Alerts are on but need a bot token and chat ID before anything is sent.' : 'Saved.' });
    } catch (problem) { setNote({ text: (problem as Error).message, bad: true }); }
    finally { setBusy(false); }
  };
  const test = async () => {
    if (!(await ensure())) return;
    setBusy(true); setNote(null);
    try { await api('/api/watch/test', { method: 'POST', body: '{}' }); setNote({ text: 'Test alert sent. Check Telegram.' }); }
    catch (problem) { setNote({ text: (problem as Error).message, bad: true }); }
    finally { setBusy(false); }
  };
  const number = (key: 'cooldown_min' | 'disk_pct' | 'memory_pct' | 'backup_hours', label: string, min: number, max: number, unit: string) => (
    <label className="watch-number">{label}
      <span><input type="number" min={min} max={max} value={form[key] ?? ''} onChange={(event) => set({ [key]: Math.round(Number(event.target.value)) } as Partial<WatchSettings>)} /> {unit}</span>
    </label>
  );
  return (
    <section className="watch-settings" aria-label="Alerts">
      {dialog}
      <h2>Alerts and health</h2>
      <p className="muted">HQ checks the fleet every minute and container health every 5 minutes. Problems show at the top of Overview, and can also be sent to Telegram.</p>
      <label className="watch-check"><input type="checkbox" checked={form.enabled} onChange={(event) => set({ enabled: event.target.checked })} /> Send alerts to Telegram</label>
      <div className="watch-fields">
        <label>Bot token
          <input type="password" autoComplete="off" placeholder={form.has_token ? 'Saved. Paste a new one to replace it' : '123456789:ABC… from BotFather'} value={token} onChange={(event) => setToken(event.target.value)} />
        </label>
        <label>Chat ID
          <input inputMode="numeric" placeholder="Your user ID, or a group ID starting with -100" value={form.chat_id} onChange={(event) => set({ chat_id: event.target.value })} />
        </label>
      </div>
      <p className="note">Make a bot with @BotFather in Telegram and paste its token here (or run <code>python3 -m hq --set-alerts</code> on the HQ container). Send the bot any message, then get your chat ID from @userinfobot. The token is kept only on the HQ container and never shown again.</p>
      <fieldset className="watch-kinds">
        <legend>Send alerts for</legend>
        {Object.entries(form.kind_names).map(([kind, name]) => (
          <label key={kind} className="watch-check"><input type="checkbox" checked={form.kinds[kind] !== false} onChange={(event) => set({ kinds: { ...form.kinds, [kind]: event.target.checked } })} /> {name}</label>
        ))}
      </fieldset>
      <div className="watch-fields">
        {number('disk_pct', 'Warn when disk is', 50, 99, '% full')}
        {number('memory_pct', 'Warn when memory is', 50, 99, '% full')}
        {number('cooldown_min', 'Repeat an alert after', 5, 1440, 'minutes')}
        {form.backup_hours !== undefined && number('backup_hours', 'Warn when an agent has no backup for', 6, 2160, 'hours')}
      </div>
      <p className="note">Disk and memory limits also apply to the Proxmox nodes, their storage, and other containers such as HQ’s own.</p>
      <label className="watch-check"><input type="checkbox" checked={form.auto_restart} onChange={(event) => set({ auto_restart: event.target.checked })} /> Restart Claude automatically when its session has stopped</label>
      <p className="note">Only for agents started by the Zylos runtime service, only after two checks 5 minutes apart find no Claude session, and at most 3 times in 6 hours per agent. Each restart is logged in Activity.</p>
      {note && <p className={note.bad ? 'control-error' : 'watch-ok'} role="status">{note.text}</p>}
      <div className="watch-buttons">
        <button type="button" className="primary" disabled={busy} onClick={() => void save()}>Save</button>
        <button type="button" disabled={busy || !(form.has_token || token) || !form.chat_id} onClick={() => void test()}>Send a test alert</button>
      </div>
    </section>
  );
}
