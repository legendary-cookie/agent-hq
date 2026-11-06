import { useEffect, useState } from 'react';
import { useApi } from '../api/client';
import type { FleetAgent, SessionRow } from '../api/types';
import { Inspector } from '../components/Inspector';
import { fmtRelative, fmtTokens } from '../lib/format';
import { STATUS, StatusDot, actions, count, messages, money, percent, perTask } from './fleet-shared';

export const AGENT_EVENT = 'hq-open-agent';
const COMM = '/zylos:comm';

/** Which agent is open. Also follows search, which can open an agent from anywhere. */
export function useOpenAgent(): [string | null, (host: string | null) => void] {
  const [host, setHost] = useState<string | null>(null);
  useEffect(() => {
    const open = (event: Event) => setHost((event as CustomEvent<string>).detail);
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setHost(null); };
    window.addEventListener(AGENT_EVENT, open);
    window.addEventListener('keydown', close);
    return () => { window.removeEventListener(AGENT_EVENT, open); window.removeEventListener('keydown', close); };
  }, []);
  return [host, setHost];
}

export function AgentSheet({ agent, periodLabel, onClose }: { agent: FleetAgent; periodLabel: string; onClose: () => void }) {
  const { data: sessions } = useApi<SessionRow[]>(`/api/sessions?host=${encodeURIComponent(agent.host)}&limit=16`, { live: false });
  const [session, setSession] = useState<string | null>(null);
  const now = Date.now() / 1000;
  const work = (sessions ?? []).filter((row) => !row.id.endsWith(COMM)).slice(0, 15);
  if (session) return <Inspector sessionId={session} onClose={() => setSession(null)} />;
  return (
    <aside className="agent-sheet" aria-label={`${agent.label} details`}>
      <div className="sheet-head">
        <div>
          <h2>{agent.label}</h2>
          <div className="muted">{agent.owner ? `${agent.owner} · ` : ''}{agent.group}</div>
        </div>
        <button type="button" className="sheet-close" aria-label="Close" onClick={onClose}>×</button>
      </div>
      <div className="sheet-status">
        <StatusDot status={agent.status} />
        <span>{STATUS[agent.status]}</span>
        <span className="muted">· {agent.last_seen ? fmtRelative(agent.last_seen, now) : 'No activity yet'}</span>
      </div>
      {agent.error && <p className="sheet-error">Couldn’t reach this agent on its last check, so its numbers may be out of date. <span className="muted">{agent.error}</span></p>}
      {agent.catching_up && <p className="sheet-note">Still copying this agent’s history, so its numbers will rise.</p>}
      <div className="sheet-pairs">
        <div><b className="num">{count(messages(agent))}</b><small>Messages, {periodLabel}</small></div>
        <div><b className="num">{count(agent.agent_messages)}</b><small>Between agents</small></div>
        <div><b className="num">{count(agent.tasks)}</b><small>Tasks</small></div>
        <div><b className="num">{count(actions(agent))}</b><small>Actions</small></div>
        <div><b className="num">{perTask(agent.value_usd, agent.tasks)}</b><small>Cost per task</small></div>
        <div><b className="num">{percent(agent.errors, actions(agent), 1)}</b><small>Error rate</small></div>
        <div><b className="num">{money(agent.value_usd, agent.unpriced_tokens)}</b><small>Usage value</small></div>
        <div><b className="num">{fmtTokens(agent.tokens)}</b><small>Tokens</small></div>
      </div>
      <h3>Recent work</h3>
      {sessions === null ? <p className="muted">Loading…</p> : work.length ? (
        <ul className="sheet-work">
          {work.map((row) => (
            <li key={row.id}>
              <button type="button" onClick={() => setSession(row.id)}>
                <span>{row.title || 'Untitled session'}</span>
                <small className="num">{fmtRelative(row.last_event_at, now)} · {fmtTokens(row.tokens)} tokens</small>
              </button>
            </li>
          ))}
        </ul>
      ) : <p className="muted">No sessions yet.</p>}
    </aside>
  );
}
