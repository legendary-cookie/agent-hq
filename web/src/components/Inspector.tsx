import { useEffect, useState } from 'react';
import { useApi } from '../api/client';
import type { SessionDetail } from '../api/types';
import { fmtDuration, fmtTokens, fmtUsd } from '../lib/format';
import { LimitBar } from './LimitBar';
import './components.css';

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="inspector-row">
      <span>{label}</span>
      <span>{value}</span>
    </div>
  );
}

function Timeline({ events }: { events: SessionDetail['events'] }) {
  if (!events.length) return <p className="inspector-subtitle">No events yet.</p>;
  return (
    <ul className="inspector-list">
      {events.map((event) => (
        <li key={event.id}>
          <time>
            {new Date(event.ts * 1000).toLocaleTimeString(undefined, {
              hour: '2-digit',
              minute: '2-digit',
              hourCycle: 'h23',
            })}
          </time>
          <span aria-label={event.kind}>●</span>
          <span>{event.tool ?? event.preview ?? event.kind}</span>
        </li>
      ))}
    </ul>
  );
}

export function Inspector({
  sessionId,
  onClose,
}: {
  sessionId: string | null;
  onClose: () => void;
}) {
  const [selected, setSelected] = useState(sessionId);
  useEffect(() => setSelected(sessionId), [sessionId]);
  const path = selected
    ? `/api/sessions/${encodeURIComponent(selected)}?limit=50&order=desc`
    : null;
  const { data, loading, error } = useApi<SessionDetail>(path);
  const detail = data?.session.id === selected ? data : null;
  const parentId = detail?.session.parent_id;
  const parentPath = parentId ? `/api/sessions/${encodeURIComponent(parentId)}?limit=1` : null;
  const { data: parent } = useApi<SessionDetail>(parentPath);
  if (!selected) return null;
  const session = detail?.session;
  const latest = detail?.events[0];
  const duration =
    session?.started_at != null
      ? (session.ended_at ?? Date.now() / 1000) - session.started_at
      : null;
  const startedBy = parentId
    ? parent?.session.id === parentId
      ? (parent.session.title ?? parentId)
      : parentId
    : '—';
  return (
    <aside className="inspector" aria-label="Session inspector">
      <button
        className="inspector-close"
        type="button"
        aria-label="Close inspector"
        onClick={onClose}
      >
        ×
      </button>
      {loading && !detail && <p>Loading…</p>}
      {error && !detail && <p>Unable to load session.</p>}
      {session && detail && (
        <>
          <div className="inspector-title">{session.harness ?? 'Agent'}</div>
          <div className="inspector-subtitle">
            {session.model ?? '—'} · {session.account_id ?? '—'}
          </div>
          <div className="inspector-heading">Now</div>
          <div>
            <span className={`status-dot status-${session.status ?? 'idle'}`} />
            {latest?.tool ?? latest?.preview ?? '—'}
          </div>
          <div className="inspector-heading">Session</div>
          <DetailRow label="Project" value={session.project ?? '—'} />
          <DetailRow label="Started by" value={startedBy} />
          <DetailRow label="Duration" value={fmtDuration(duration)} />
          <DetailRow label="Tokens" value={fmtTokens(detail.value.tokens)} />
          <DetailRow
            label="Value"
            value={fmtUsd(detail.value.value_usd, {
              unpricedTokens: detail.value.unpriced_tokens,
              estimated: detail.value.estimated,
            })}
          />
          <DetailRow label="Effective cost" value={fmtUsd(detail.value.effective_cost_usd)} />
          <div className="inspector-heading">Account limit</div>
          {detail.account_limit?.windows.map((window, index) => (
            <div key={`${window.window}-${window.scope}-${index}`} className="inspector-limit">
              <div>
                {window.window ?? 'Unknown'}
                {window.scope && window.scope !== '*' ? ` · ${window.scope}` : ''} · {window.source}
              </div>
              <LimitBar percent={window.used_percent} source={window.source} stale={window.stale} />
            </div>
          )) ?? <div className="inspector-subtitle">No limit data</div>}
          <div className="inspector-heading">Timeline</div>
          <Timeline events={detail.events} />
          <div className="inspector-heading">Subagents</div>
          {detail.children.length ? (
            <ul className="inspector-list">
              {detail.children.map((child) => (
                <li key={child.id}>
                  <button type="button" onClick={() => setSelected(child.id)}>
                    {child.title ?? child.id}
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="inspector-subtitle">No subagents.</p>
          )}
        </>
      )}
    </aside>
  );
}
