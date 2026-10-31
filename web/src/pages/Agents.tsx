import { useState } from 'react';
import { useApi } from '../api/client';
import type { SessionRow } from '../api/types';
import { EmptyState } from '../components/EmptyState';
import { Inspector } from '../components/Inspector';
import { fmtRelative, fmtTokens } from '../lib/format';
import './agents.css';

const statuses = [
  ['all', 'All'],
  ['working', 'Working'],
  ['waiting', 'Needs you'],
  ['idle', 'Idle'],
  ['done', 'Done'],
  ['error', 'Error'],
] as const;

export function Agents() {
  const { data, loading, error } = useApi<SessionRow[]>('/api/sessions');
  const [status, setStatus] = useState<string>('all');
  const [harness, setHarness] = useState('all');
  const [sort, setSort] = useState<'activity' | 'tokens'>('activity');
  const [selected, setSelected] = useState<string | null>(null);
  const sessions = data ?? [];
  const harnesses = [
    ...new Set(sessions.map((row) => row.harness).filter((item): item is string => !!item)),
  ].sort();
  const filtered = sessions.filter(
    (row) =>
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
        <h1>Agents</h1>
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
                      className={`status-dot status-${row.status ?? 'idle'}`}
                      aria-label={row.status ?? 'unknown'}
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
