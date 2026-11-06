import { useState } from 'react';
import { useApi } from '../api/client';
import type { Health, SessionRow } from '../api/types';
import { EmptyState } from '../components/EmptyState';
import { Inspector } from '../components/Inspector';
import { fmtRelative, fmtTokens } from '../lib/format';
import './agents.css';

const statuses = [
  ['all', 'All'],
  ['working', 'Working'],
  ['active', 'Active (imported)'],
  ['waiting', 'Needs you'],
  ['idle', 'Idle'],
  ['done', 'Done'],
  ['error', 'Error'],
] as const;

export function Agents() {
  const { data, loading, error } = useApi<SessionRow[]>('/api/sessions');
  const { data: health } = useApi<Health>('/api/health');
  const [host, setHost] = useState('all');
  const [status, setStatus] = useState<string>('all');
  const [harness, setHarness] = useState('all');
  const [sort, setSort] = useState<'activity' | 'tokens'>('activity');
  const [selected, setSelected] = useState<string | null>(() => new URLSearchParams(location.hash.split('?')[1] ?? '').get('session'));
  const sessions = data ?? [];
  const harnesses = [
    ...new Set(sessions.map((row) => row.harness).filter((item): item is string => !!item)),
  ].sort();
  const hosts = [...new Set([...Object.keys(health?.hosts ?? {}), ...sessions.flatMap((row) => row.host ? [row.host] : [])])].sort();
  const filtered = sessions.filter(
    (row) =>
      !row.id.endsWith('/zylos:comm') && // A message log from the zylos_metrics source, not a session to open.
      (host === 'all' || (host === 'local' ? !row.host : row.host === host.slice(5))) &&
      (status === 'all' || row.status === status) && (harness === 'all' || row.harness === harness),
  );
  const sorted = [...filtered].sort((a, b) =>
    sort === 'tokens' ? b.tokens - a.tokens : (b.last_event_at ?? 0) - (a.last_event_at ?? 0),
  );
  const ids = new Set(sorted.map((row) => row.id));
  const ordered: { row: SessionRow; depth: number }[] = [];
  const seen = new Set<string>();
  function append(row: SessionRow, depth: number) {
    if (seen.has(row.id)) return;
    seen.add(row.id);
    ordered.push({ row, depth });
    sorted
      .filter((child) => child.parent_id === row.id)
      .forEach((child) => append(child, depth + 1));
  }
  sorted
    .filter((row) => !row.parent_id || !ids.has(row.parent_id))
    .forEach((row) => append(row, 0));
  sorted.forEach((row) => append(row, 0));
  return (
    <div className="agents-layout">
      <div className="agents-page">
        <h1>Sessions</h1>
        <div className="agents-filters">
          <div className="agents-statuses" aria-label="Status filters">
            {statuses.map(([key, label]) => (
              <button
                key={key}
                type="button"
                aria-pressed={status === key}
                onClick={() => setStatus(key)}
              >
                {label}
              </button>
            ))}
          </div>
          <label>
            Host{' '}
            <select value={host} onChange={(event) => setHost(event.target.value)}>
              <option value="all">All hosts</option>
              <option value="local">This PC</option>
              {hosts.map((name) => <option key={name} value={`host:${name}`}>{name}</option>)}
            </select>
          </label>
          <label>
            Harness{' '}
            <select value={harness} onChange={(event) => setHarness(event.target.value)}>
              <option value="all">All harnesses</option>
              {harnesses.map((item) => (
                <option key={item}>{item}</option>
              ))}
            </select>
          </label>
        </div>
        {loading && !data ? (
          <p>Loading…</p>
        ) : error && !data ? (
          <p>Unable to load sessions.</p>
        ) : !ordered.length ? (
          <EmptyState title="No sessions" hint="Sessions will appear here when available." />
        ) : (
          <table className="agents-table">
            <thead>
              <tr>
                <th aria-label="Status" />
                <th>Title</th>
                <th>Harness</th>
                <th>Model</th>
                <th>Account</th>
                <th>Project</th>
                <th>
                  <button
                    type="button"
                    onClick={() => setSort('activity')}
                    aria-pressed={sort === 'activity'}
                  >
                    Last activity
                  </button>
                </th>
                <th className="agents-number">
                  <button
                    type="button"
                    onClick={() => setSort('tokens')}
                    aria-pressed={sort === 'tokens'}
                  >
                    Tokens
                  </button>
                </th>
              </tr>
            </thead>
            <tbody>
              {ordered.map(({ row, depth }) => (
                <tr key={row.id} onClick={() => setSelected(row.id)}>
                  <td>
                    <span
                      className={`status-dot agents-status-dot status-${row.status ?? 'idle'}`}
                      aria-label={
                        row.status === 'active' ? 'Active (imported)' : (row.status ?? 'unknown')
                      }
                    />
                  </td>
                  <td className={depth ? 'agents-child' : undefined}>
                    <button
                      className="agents-row-button"
                      type="button"
                      style={{ paddingLeft: depth * 14 }}
                      onClick={() => setSelected(row.id)}
                    >
                      {row.title || row.id.slice(-12)}
                    </button>
                  </td>
                  <td title={row.harness ?? ''}>{row.harness ?? '—'}</td>
                  <td title={row.model ?? ''}>{row.model ?? '—'}</td>
                  <td title={row.account_id ?? ''}>{row.account_id ?? '—'}</td>
                  <td title={row.project ?? ''}>
                    {row.project
                      ?.replace(/[\\/]+$/, '')
                      .split(/[\\/]/)
                      .pop() || '—'}
                  </td>
                  <td>{fmtRelative(row.last_event_at, Date.now() / 1000)}</td>
                  <td className="agents-number">{fmtTokens(row.tokens)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <Inspector sessionId={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
