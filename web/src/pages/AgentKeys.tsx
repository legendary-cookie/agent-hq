/** Settings: keys that let AI agents use HQ as an MCP tool server. */
import { useState } from 'react';
import { api, useApi } from '../api/client';
import { fmtRelative } from '../lib/format';
import { usePasswordCheck } from './AgentControl';
import './watch.css';

export type AgentKey = { id: string; name: string; scopes: string[]; created_at: number; last_used_at: number | null; uses: number };
type KeysData = { keys: AgentKey[]; scopes: Record<string, string> };

export function mcpAddress() {
  return `${window.location.origin}/mcp`;
}

export function AgentKeysPanel() {
  const { data, reload } = useApi<KeysData>('/api/agent-keys', { live: false });
  const { data: control } = useApi<{ elevated_until?: number | null }>('/api/control', { live: false });
  const { ensure, dialog } = usePasswordCheck(control?.elevated_until);
  const [name, setName] = useState('');
  const [scopes, setScopes] = useState<string[]>([]);
  const [made, setMade] = useState<{ token: string; name: string } | null>(null);
  const [note, setNote] = useState<{ text: string; bad?: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  if (!data || !Array.isArray(data.keys) || !data.scopes) return null;
  const now = Date.now() / 1000;

  const create = async () => {
    if (!name.trim() || !(await ensure())) return;
    setBusy(true); setNote(null);
    try {
      const answer = await api<{ token: string; key: AgentKey }>('/api/agent-keys', { method: 'POST', body: JSON.stringify({ name: name.trim(), scopes }) });
      setMade({ token: answer.token, name: answer.key.name });
      setName(''); setScopes([]); reload();
    } catch (problem) { setNote({ text: (problem as Error).message, bad: true }); }
    finally { setBusy(false); }
  };
  const revoke = async (key: AgentKey) => {
    if (!window.confirm(`Revoke the key "${key.name}"? Anything using it stops working at once.`) || !(await ensure())) return;
    try { await api('/api/agent-keys/revoke', { method: 'POST', body: JSON.stringify({ id: key.id }) }); setNote({ text: `Revoked "${key.name}".` }); reload(); }
    catch (problem) { setNote({ text: (problem as Error).message, bad: true }); }
  };
  const command = made ? `claude mcp add --transport http agent-hq ${mcpAddress()} --header "Authorization: Bearer ${made.token}"` : '';

  return (
    <section className="watch-settings" aria-label="AI agent access">
      {dialog}
      <h2>AI agent access (MCP)</h2>
      <p className="muted">AI agents can use HQ as a tool server at <code>{mcpAddress()}</code>. Each agent gets its own key. Every key can read the fleet, an agent’s status, health warnings, Proxmox capacity, updates and 9router’s status. Logins, message text and passwords are never shared.</p>
      <div className="watch-fields">
        <label>Key name
          <input value={name} maxLength={60} placeholder="Which agent it’s for, such as Manager Agent" onChange={(event) => setName(event.target.value)} />
        </label>
      </div>
      <fieldset className="watch-kinds">
        <legend>Also allow (off unless needed; an agent can be talked into things by what it reads)</legend>
        {Object.entries(data.scopes).map(([scope, label]) => (
          <label key={scope} className="watch-check"><input type="checkbox" checked={scopes.includes(scope)} onChange={(event) => setScopes(event.target.checked ? [...scopes, scope] : scopes.filter((s) => s !== scope))} /> {label}</label>
        ))}
      </fieldset>
      <div className="watch-buttons"><button type="button" className="primary" disabled={busy || !name.trim()} onClick={create}>{busy ? 'Making…' : 'Make a key'}</button></div>
      {made && (
        <div className="agent-key-made" role="status">
          <p><b>Key for {made.name}.</b> Copy it now; HQ keeps only a fingerprint and can’t show it again.</p>
          <pre>{made.token}</pre>
          <p className="muted">To add HQ to that agent’s Claude Code, run this on the agent (it also needs to trust HQ’s certificate; see the HQ runbook):</p>
          <pre>{command}</pre>
          <button type="button" className="plain-button" onClick={() => setMade(null)}>Done, I’ve copied it</button>
        </div>
      )}
      {note && <p className={note.bad ? 'control-error' : 'watch-ok'} role="status">{note.text}</p>}
      {data.keys.length > 0 && (
        <div className="mini-table agent-keys" role="table" aria-label="Agent keys">
          <div className="mini-row mini-headrow" role="row"><span role="columnheader">Key</span><span role="columnheader">Can also</span><span role="columnheader">Last used</span><span role="columnheader" /></div>
          {data.keys.map((key) => (
            <div className="mini-row" role="row" key={key.id}>
              <span role="cell">{key.name}</span>
              <span role="cell" className="muted">{key.scopes.length ? key.scopes.map((s) => data.scopes[s] ?? s).join(', ') : 'Read only'}</span>
              <span role="cell" className="muted">{key.last_used_at ? `${fmtRelative(key.last_used_at, now)} · ${key.uses} calls` : 'Never'}</span>
              <span role="cell"><button type="button" className="plain-button" onClick={() => revoke(key)}>Revoke</button></span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
