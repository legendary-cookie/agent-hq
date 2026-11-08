import { useEffect, useState } from 'react';
import { api, useApi } from '../api/client';
import { useMe } from '../api/me';
import { usePasswordCheck } from './AgentControl';

export interface AgentSettingsData {
  container: { cores: number; memory: number; swap: number; onboot: boolean };
  claude?: { model: string; auto_updates: boolean };
  telegram?: { enabled: boolean; owner_chat_id: string; owner_username: string; dm_policy: string; dm_allow: string[]; group_policy: string; groups: string[] };
  services?: { name: string; enabled: string; active: string }[];
  timezone?: string;
  identity?: string | null;
  error?: string;
}
const MODELS = [['', 'Default'], ['sonnet', 'Sonnet'], ['opus', 'Opus'], ['haiku', 'Haiku'], ['opusplan', 'Opus plans, Sonnet works']];
const POLICIES = [['allowlist', 'Only listed people'], ['open', 'Anyone'], ['disabled', 'Nobody']];
const RISKY = new Set(['zylos-creds-puller', 'zylos-claude-runtime', 'zylos-c4-dispatcher']);
const list = (text: string) => text.split(/[\s,]+/).map((item) => item.trim()).filter(Boolean);

/** Settings for one agent. Each part saves on its own, after the password check, and is logged. */
export function AgentSettings({ host, label, controlled }: { host: string; label: string; controlled: boolean }) {
  const [open, setOpen] = useState(false);
  const { data, reload, error } = useApi<AgentSettingsData>(open && controlled ? `/api/agents/settings?host=${encodeURIComponent(host)}` : null, { live: false });
  const { data: control } = useApi<{ elevated_until?: number | null }>(open ? '/api/control' : null, { live: false });
  const { ensure, dialog } = usePasswordCheck(control?.elevated_until);
  const [draft, setDraft] = useState<AgentSettingsData | null>(null);
  const [note, setNote] = useState<{ text: string; bad?: boolean } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const mayChange = useMe().can('agents.create');
  useEffect(() => { if (data) setDraft(JSON.parse(JSON.stringify(data))); }, [data]);
  if (!controlled || !mayChange) return null;
  const save = async (section: string, values: object, question?: string) => {
    if (question && !window.confirm(`${label}: ${question}`)) return;
    if (!(await ensure())) return;
    setBusy(section);
    setNote(null);
    try {
      const answer = await api<{ done: string[] }>('/api/agents/settings', { method: 'POST', body: JSON.stringify({ host, section, values }) });
      setNote({ text: answer.done.join('. ') + '.' });
      reload();
    } catch (problem) {
      setNote({ text: (problem as Error).message, bad: true });
    } finally {
      setBusy(null);
    }
  };
  const d = draft;
  const edit = (patch: Partial<AgentSettingsData>) => setDraft((old) => (old ? { ...old, ...patch } : old));
  return (
    <section className="agent-control agent-settings">
      <button type="button" className="settings-toggle" aria-expanded={open} onClick={() => setOpen(!open)}><h3>Settings</h3><span className="muted">{open ? 'Hide' : 'Show'}</span></button>
      {open && !d && <p className="muted">{error ? error.message : 'Loading…'}</p>}
      {open && d && (
        <>
          <fieldset>
            <legend>Container</legend>
            <label>CPU cores <input type="number" min={1} max={16} value={d.container.cores} onChange={(e) => edit({ container: { ...d.container, cores: Number(e.target.value) } })} /></label>
            <label>Memory (MB) <input type="number" min={512} max={65536} step={256} value={d.container.memory} onChange={(e) => edit({ container: { ...d.container, memory: Number(e.target.value) } })} /></label>
            <label>Swap (MB) <input type="number" min={0} max={16384} step={256} value={d.container.swap} onChange={(e) => edit({ container: { ...d.container, swap: Number(e.target.value) } })} /></label>
            <label className="check"><input type="checkbox" checked={d.container.onboot} onChange={(e) => edit({ container: { ...d.container, onboot: e.target.checked } })} /> Start when Proxmox starts</label>
            <button type="button" className="plain-button" disabled={!!busy} onClick={() => save('container', d.container)}>Save</button>
          </fieldset>
          {d.claude && (
            <fieldset>
              <legend>Claude Code</legend>
              <label>Model <select value={d.claude.model} onChange={(e) => edit({ claude: { ...d.claude!, model: e.target.value } })}>
                {/* The agent's own model stays selectable, so saving never changes it by accident. */}
                {[...MODELS, ...(MODELS.some(([v]) => v === data?.claude?.model) || !data?.claude?.model ? [] : [[data.claude.model, data.claude.model]])].map(([v, t]) => <option key={v} value={v}>{t}</option>)}
              </select></label>
              <label className="check"><input type="checkbox" checked={d.claude.auto_updates} onChange={(e) => edit({ claude: { ...d.claude!, auto_updates: e.target.checked } })} /> Let Claude Code update itself</label>
              <button type="button" className="plain-button" disabled={!!busy} onClick={() => save('claude', d.claude!, d.claude!.auto_updates && !data?.claude?.auto_updates ? 'Allow Claude Code to update itself? Updates were switched off on purpose on most agents; the Updates page lets you choose versions instead.' : undefined)}>Save</button>
              <small>Applies from the Claude session’s next restart.</small>
            </fieldset>
          )}
          {d.telegram && (
            <fieldset>
              <legend>Telegram</legend>
              <label className="check"><input type="checkbox" checked={d.telegram.enabled} onChange={(e) => edit({ telegram: { ...d.telegram!, enabled: e.target.checked } })} /> Telegram on</label>
              <label>Owner’s Telegram ID <input value={d.telegram.owner_chat_id} onChange={(e) => edit({ telegram: { ...d.telegram!, owner_chat_id: e.target.value } })} /></label>
              <label>Owner’s username <input value={d.telegram.owner_username} onChange={(e) => edit({ telegram: { ...d.telegram!, owner_username: e.target.value } })} /></label>
              <label>Direct messages from <select value={d.telegram.dm_policy} onChange={(e) => edit({ telegram: { ...d.telegram!, dm_policy: e.target.value } })}>{POLICIES.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select></label>
              <label className="wide">Allowed people (Telegram IDs) <input value={d.telegram.dm_allow.join(', ')} onChange={(e) => edit({ telegram: { ...d.telegram!, dm_allow: list(e.target.value) } })} /></label>
              <label>Groups <select value={d.telegram.group_policy} onChange={(e) => edit({ telegram: { ...d.telegram!, group_policy: e.target.value } })}>{POLICIES.map(([v, t]) => <option key={v} value={v}>{t === 'Only listed people' ? 'Only listed groups' : t}</option>)}</select></label>
              <div className="wide">{d.telegram.groups.length ? d.telegram.groups.map((g) => (
                <span key={g} className="chip settings-chip">{g} <button type="button" aria-label={`Remove group ${g}`} onClick={() => edit({ telegram: { ...d.telegram!, groups: d.telegram!.groups.filter((x) => x !== g) } })}>×</button></span>
              )) : <span className="muted">In no groups.</span>}</div>
              <button type="button" className="plain-button" disabled={!!busy} onClick={() => save('telegram', d.telegram!, 'Save Telegram settings? The Telegram service restarts, which takes a few seconds.')}>Save</button>
            </fieldset>
          )}
          {d.services && (
            <fieldset>
              <legend>Services</legend>
              {d.services.map((service) => (
                <div className="settings-service" key={service.name}>
                  <span><b>{service.name.replace(/^zylos-/, '')}</b> <span className={service.active === 'active' ? 'control-ok' : 'muted'}>{service.active}</span>{service.enabled === 'disabled' ? <span className="muted"> · off at boot</span> : null}</span>
                  <span className="control-buttons">
                    {service.active === 'active'
                      ? <><button type="button" className="plain-button" disabled={!!busy} onClick={() => save('service', { name: service.name, action: 'restart' }, `Restart ${service.name}?`)}>Restart</button>
                        <button type="button" className="plain-button" disabled={!!busy} onClick={() => save('service', { name: service.name, action: 'stop' }, `Stop ${service.name}?${RISKY.has(service.name) ? ' The agent can’t work without it.' : ''}`)}>Stop</button></>
                      : <button type="button" className="plain-button" disabled={!!busy} onClick={() => save('service', { name: service.name, action: 'start' })}>Start</button>}
                    {service.enabled === 'enabled'
                      ? <button type="button" className="plain-button" disabled={!!busy} onClick={() => save('service', { name: service.name, action: 'disable' }, `Switch ${service.name} off, including after restarts?${RISKY.has(service.name) ? ' The agent can’t work without it.' : ''}`)}>Switch off</button>
                      : service.enabled === 'disabled' ? <button type="button" className="plain-button" disabled={!!busy} onClick={() => save('service', { name: service.name, action: 'enable' }, service.name === 'zylos-hxa-connect' ? 'Switch HXA Connect on? Only do this once the agent has its own HXA identity, not a copied one.' : undefined)}>Switch on</button> : null}
                  </span>
                </div>
              ))}
            </fieldset>
          )}
          <fieldset>
            <legend>Time zone</legend>
            <label>Time zone <input value={d.timezone ?? ''} onChange={(e) => edit({ timezone: e.target.value })} placeholder="Asia/Singapore" /></label>
            <button type="button" className="plain-button" disabled={!!busy} onClick={() => save('timezone', { timezone: d.timezone })}>Save</button>
          </fieldset>
          {d.identity !== null && d.identity !== undefined && (
            <fieldset>
              <legend>Persona</legend>
              <textarea className="wide" aria-label="Persona" rows={10} value={d.identity} onChange={(e) => edit({ identity: e.target.value })} />
              <button type="button" className="plain-button" disabled={!!busy} onClick={() => save('identity', { text: d.identity }, 'Save the persona? The agent reads it at its next session.')}>Save</button>
              <small>memory/identity.md. The old version is kept as a backup and in the memory history.</small>
            </fieldset>
          )}
          {note && <p className={note.bad ? 'control-error' : 'control-ok'} role="status">{note.text}</p>}
          <p className="note">Each change keeps a backup of the file it edits, restarts only what it needs, and is logged.</p>
        </>
      )}
      {dialog}
    </section>
  );
}
