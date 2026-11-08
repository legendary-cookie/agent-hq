import { lazy, Suspense, useState } from 'react';
import { api, useApi } from '../api/client';
import { useMe } from '../api/me';
import { fmtDuration } from '../lib/format';

const Terminal = lazy(() => import('./Terminal').then((module) => ({ default: module.Terminal })));

export interface ControlData {
  configured: boolean; error?: string; managed: string[]; elevated_until?: number | null;
  containers: Record<string, { vmid: number; status: string; uptime: number | null; cpu: number | null; cpus: number | null; mem: number | null; maxmem: number | null }>;
}
type Mode = 'watch' | 'control' | 'root';
const ACTIONS: { key: string; label: string; confirm: string; when: (running: boolean) => boolean }[] = [
  { key: 'start', label: 'Start', confirm: 'Start this agent’s container?', when: (running) => !running },
  { key: 'reboot', label: 'Restart container', confirm: 'Restart the whole container? The agent stops answering for a minute or two.', when: (running) => running },
  { key: 'shutdown', label: 'Shut down', confirm: 'Shut the container down? The agent stops answering until it’s started again.', when: (running) => running },
  { key: 'stop', label: 'Force stop', confirm: 'Force-stop the container? Unsaved work in it is lost. Use only if Shut down doesn’t work.', when: (running) => running },
];

/** Asks for the HQ password once, then allows control actions for 8 hours. */
export function usePasswordCheck(elevatedUntil: number | null | undefined) {
  const [until, setUntil] = useState<number | null>(null);
  const [asking, setAsking] = useState<{ resolve: (ok: boolean) => void } | null>(null);
  const current = Math.max(until ?? 0, elevatedUntil ?? 0);
  const ensure = () => (current > Date.now() / 1000 + 5 ? Promise.resolve(true) : new Promise<boolean>((resolve) => setAsking({ resolve })));
  const dialog = asking && <PasswordDialog onDone={(value) => { if (value) setUntil(value); asking.resolve(!!value); setAsking(null); }} />;
  return { ensure, dialog };
}

function PasswordDialog({ onDone }: { onDone: (until: number | null) => void }) {
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      const answer = await api<{ elevated_until: number }>('/api/control/elevate', { method: 'POST', body: JSON.stringify({ password }) });
      onDone(answer.elevated_until);
    } catch (problem) {
      setError((problem as Error).message || 'That password is not right.');
      setBusy(false);
    }
  };
  return (
    <div className="control-scrim" role="dialog" aria-label="Confirm your password">
      <form className="control-dialog" onSubmit={submit}>
        <h3>Confirm it’s you</h3>
        <p className="muted">This needs your HQ password. It’s asked again after 8 hours.</p>
        <input type="password" autoFocus aria-label="HQ password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" />
        {error && <p className="control-error">{error}</p>}
        <div className="control-buttons">
          <button type="button" className="plain-button" onClick={() => onDone(null)}>Cancel</button>
          <button type="submit" className="plain-button primary" disabled={!password || busy}>{busy ? 'Checking…' : 'Confirm'}</button>
        </div>
      </form>
    </div>
  );
}

export function AgentControl({ host, label }: { host: string; label: string }) {
  const { data, reload } = useApi<ControlData>('/api/control');
  const { data: session } = useApi<{ session: string | null; restartable: boolean }>(data?.configured && data.containers[host]?.status === 'running' ? `/api/control/session?host=${encodeURIComponent(host)}` : null, { live: false });
  const { ensure, dialog } = usePasswordCheck(data?.elevated_until);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<{ text: string; bad?: boolean } | null>(null);
  const [terminal, setTerminal] = useState<Mode | null>(null);
  const [message, setMessage] = useState('');
  const { can } = useMe();
  if (!data) return null;
  if (!data.managed.includes(host)) return null;
  if (!data.configured) {
    return (
      <section className="agent-control">
        <h3>Control</h3>
        <p className="muted">HQ can’t control agents yet. It needs its Proxmox key installed on the host. See “Control” in the HQ runbook.</p>
      </section>
    );
  }
  const box = data.containers[host];
  const running = box?.status === 'running';
  const act = async (key: string, run: () => Promise<unknown>, done: string, confirmText?: string) => {
    if (confirmText && !window.confirm(`${label}: ${confirmText}`)) return;
    if (!(await ensure())) return;
    setBusy(key);
    setNote(null);
    try {
      await run();
      setNote({ text: done });
      reload();
    } catch (problem) {
      setNote({ text: (problem as Error).message, bad: true });
    } finally {
      setBusy(null);
    }
  };
  const openTerminal = async (mode: Mode) => {
    if (mode !== 'watch' && !(await ensure())) return;
    setTerminal(mode);
  };
  const post = (path: string, body: object) => api(path, { method: 'POST', body: JSON.stringify({ host, ...body }) });
  return (
    <section className="agent-control">
      <h3>Control</h3>
      {data.error && <p className="control-error">{data.error}</p>}
      <div className="control-status">
        <span className={`dot ${running ? 'dot-working' : 'dot-quiet'}`} />
        <span>{box ? (running ? 'Container running' : `Container ${box.status}`) : 'Container not found'}</span>
        {running && box?.uptime ? <span className="muted">· up {fmtDuration(box.uptime)}</span> : null}
        {running && box?.maxmem ? <span className="muted">· memory {Math.round(((box.mem ?? 0) / box.maxmem) * 100)}% · CPU {Math.round((box.cpu ?? 0) * 100)}%</span> : null}
      </div>
      {running && <div className="control-status muted">Claude session: {session ? (session.session ?? 'not running') : '…'}</div>}
      <div className="control-buttons">
        {running && can('view.transcripts') && <button type="button" className="plain-button" onClick={() => openTerminal('watch')}>Watch terminal</button>}
        {running && can('agents.terminal') && <button type="button" className="plain-button" onClick={() => openTerminal('control')}>Take control</button>}
        {running && can('agents.terminal') && <button type="button" className="plain-button" onClick={() => openTerminal('root')}>Root shell</button>}
        {running && session?.restartable && can('agents.control') && <button type="button" className="plain-button" disabled={!!busy} onClick={() => act('claude', () => post('/api/control/restart-claude', {}), 'Claude session restarted.', 'Restart the Claude session? Work in progress in it stops.')}>{busy === 'claude' ? 'Restarting…' : 'Restart Claude'}</button>}
        {ACTIONS.filter((item) => item.when(running) && can('agents.control')).map((item) => (
          <button type="button" key={item.key} className={`plain-button${item.key === 'stop' ? ' danger' : ''}`} disabled={!!busy}
            onClick={() => act(item.key, () => post('/api/control/power', { action: item.key }), `${item.label} done.`, item.confirm)}>
            {busy === item.key ? 'Working…' : item.label}
          </button>
        ))}
      </div>
      {running && session?.session && can('agents.message') && (
        <form className="control-send" onSubmit={(event) => { event.preventDefault(); const text = message; act('send', () => post('/api/control/send', { text }), 'Message typed into the Claude session.').then(() => setMessage('')); }}>
          <input aria-label={`Message to ${label}`} placeholder="Type a message into its Claude session" value={message} maxLength={4000} onChange={(event) => setMessage(event.target.value)} />
          <button type="submit" className="plain-button" disabled={!message.trim() || !!busy}>Send</button>
        </form>
      )}
      {note && <p className={note.bad ? 'control-error' : 'control-ok'} role="status">{note.text}</p>}
      <p className="note">Every action here is confirmed and logged. Take control, root shell and the buttons above ask for your HQ password, then allow 8 hours.</p>
      {dialog}
      {terminal && (
        <Suspense fallback={null}>
          <Terminal host={host} label={label} mode={terminal} onClose={() => setTerminal(null)} />
        </Suspense>
      )}
    </section>
  );
}
