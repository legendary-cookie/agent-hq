/** Do one thing to several agents at once: restart Claude, restart or start the container, or send a message. */
import { useState } from 'react';
import { api, useApi } from '../api/client';
import { useMe } from '../api/me';
import { usePasswordCheck } from './AgentControl';
import './control.css';
import './bulk.css';

export type BulkAgent = { host: string; label: string };
type Action = 'restart-claude' | 'reboot' | 'start' | 'message';
type Result = { host: string; label: string; state: 'waiting' | 'running' | 'done' | 'failed'; error?: string };

const NAMES: Record<Action, string> = {
  'restart-claude': 'Restart Claude',
  reboot: 'Restart container',
  start: 'Start container',
  message: 'Send message',
};

/** What each action sends, for one agent. */
export function request(action: Action, host: string, text = ''): [string, Record<string, unknown>] {
  if (action === 'restart-claude') return ['/api/control/restart-claude', { host }];
  if (action === 'message') return ['/api/control/send', { host, text }];
  return ['/api/control/power', { host, action }];
}

export function BulkBar({ agents, onClear }: { agents: BulkAgent[]; onClear: () => void }) {
  const { data: control } = useApi<{ elevated_until?: number | null; managed?: string[] }>('/api/control', { live: false });
  const { ensure, dialog } = usePasswordCheck(control?.elevated_until);
  const { can } = useMe();
  const [results, setResults] = useState<Result[] | null>(null);
  const [writing, setWriting] = useState(false);
  const [text, setText] = useState('');
  const managed = new Set(control?.managed ?? agents.map((agent) => agent.host));
  const targets = agents.filter((agent) => managed.has(agent.host));
  const skipped = agents.length - targets.length;
  const busy = results?.some((result) => result.state === 'running' || result.state === 'waiting');

  const run = async (action: Action, message = '') => {
    if (!targets.length) return;
    const names = targets.length <= 6 ? targets.map((agent) => agent.label).join(', ') : `${targets.length} agents`;
    const what = action === 'message' ? `Send this message to ${names}?\n\n“${message}”` : `${NAMES[action]} on ${names}?`;
    if (!window.confirm(`${what}\n\nThey are done one at a time, and each is logged.`)) return;
    if (!(await ensure())) return;
    setWriting(false);
    const list: Result[] = targets.map((agent) => ({ host: agent.host, label: agent.label, state: 'waiting' }));
    setResults([...list]);
    for (const item of list) {
      item.state = 'running';
      setResults([...list]);
      try {
        const [path, body] = request(action, item.host, message);
        await api(path, { method: 'POST', body: JSON.stringify(body) });
        item.state = 'done';
      } catch (error) {
        item.state = 'failed';
        item.error = (error as Error).message;
      }
      setResults([...list]);
    }
    if (action === 'message') setText('');
  };

  return (
    <div className="bulk" role="region" aria-label="Selected agents">
      {dialog}
      <div className="bulk-bar">
        <strong>{agents.length} selected</strong>
        {skipped > 0 && <span className="muted">{skipped} can’t be controlled from HQ</span>}
        <span className="bulk-actions">
          {can('agents.control') && <>
            <button type="button" disabled={busy || !targets.length} onClick={() => void run('restart-claude')}>Restart Claude</button>
            <button type="button" disabled={busy || !targets.length} onClick={() => void run('reboot')}>Restart container</button>
            <button type="button" disabled={busy || !targets.length} onClick={() => void run('start')}>Start</button>
          </>}
          {can('agents.message') && <button type="button" disabled={busy || !targets.length} onClick={() => setWriting((open) => !open)} aria-expanded={writing}>Send message</button>}
          {can('updates.run') && <a href="#/updates">Update…</a>}
          <button type="button" className="quiet" disabled={busy} onClick={() => { setResults(null); onClear(); }}>Clear</button>
        </span>
      </div>
      {writing && (
        <form className="bulk-message" onSubmit={(event) => { event.preventDefault(); if (text.trim()) void run('message', text.trim()); }}>
          <textarea aria-label="Message to send" rows={3} maxLength={4000} value={text} onChange={(event) => setText(event.target.value)}
            placeholder="Typed into each agent’s Claude session, as if you sent it there. One line; new lines become spaces." />
          <div><button type="submit" disabled={!text.trim() || busy}>Send to {targets.length} {targets.length === 1 ? 'agent' : 'agents'}</button></div>
        </form>
      )}
      {results && (
        <ul className="bulk-results" aria-live="polite">
          {results.map((result) => (
            <li key={result.host} data-state={result.state}>
              <span>{result.label}</span>
              <span className="muted">{result.state === 'failed' ? `Failed: ${result.error}` : result.state === 'done' ? 'Done' : result.state === 'running' ? 'Working…' : 'Waiting'}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
