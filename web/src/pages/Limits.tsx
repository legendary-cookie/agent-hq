import { useState } from 'react';
import type { FormEvent } from 'react';
import { api, useApi } from '../api/client';
import type { LimitCard, LimitWindow, Overview } from '../api/types';
import { EmptyState } from '../components/EmptyState';
import { LimitBar } from '../components/LimitBar';
import { fmtDuration, fmtPercentValue, fmtRelative, fmtTokens } from '../lib/format';
import './limits.css';

function windowLabel(item: LimitWindow) {
  if (item.source === 'blocked')
    return `Blocked · ${item.scope && item.scope !== '*' ? item.scope : 'all'}`;
  const labels: Record<string, string> = {
    '5h': '5 hours',
    week: 'Week',
    day: 'Day',
    month: 'Month',
  };
  return item.window ? (labels[item.window] ?? item.window) : 'No limit data yet';
}

function ManualLimit({ accountId, reload }: { accountId: string; reload: () => void }) {
  const [windowName, setWindowName] = useState('5h');
  const [percent, setPercent] = useState('');
  const [reset, setReset] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const value = Number(percent);
    if (percent.trim() === '' || !Number.isFinite(value) || value < 0 || value > 100) {
      setError('Enter a percentage between 0 and 100.');
      return;
    }
    setSaving(true);
    setError('');
    try {
      await api('/api/limits/manual', {
        method: 'POST',
        body: JSON.stringify({
          account_id: accountId,
          window: windowName,
          used_percent: value,
          resets_at: reset ? new Date(reset).getTime() / 1000 : null,
        }),
      });
      reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save limit.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <details className="limits-manual">
      <summary>Set manually</summary>
      <form onSubmit={submit}>
        <label>
          Window
          <select value={windowName} onChange={(event) => setWindowName(event.target.value)}>
            <option value="5h">5 hours</option>
            <option value="day">Day</option>
            <option value="week">Week</option>
            <option value="month">Month</option>
          </select>
        </label>
        <label>
          % used
          <input
            type="number"
            min="0"
            max="100"
            step="any"
            required
            value={percent}
            onChange={(event) => setPercent(event.target.value)}
          />
        </label>
        <label>
          Reset time (optional)
          <input
            type="datetime-local"
            value={reset}
            onChange={(event) => setReset(event.target.value)}
          />
        </label>
        <button type="submit" disabled={saving}>
          Save limit
        </button>
        {error && <p role="alert">{error}</p>}
      </form>
    </details>
  );
}

export function Limits() {
  const { data, error, loading, reload } = useApi<LimitCard[]>('/api/limits');
  const { data: overview } = useApi<Overview>('/api/overview');
  const now = Date.now() / 1000;
  return (
    <div className="limits-page">
      <h1>Limits</h1>
      {loading && !data && <p>Loading limits…</p>}
      {error && <p role="alert">{error.message}</p>}
      {data?.length === 0 && (
        <EmptyState
          title="No accounts"
          hint="Add an account in your configuration to see limits."
        />
      )}
      <div className="limits-cards">
        {data?.map((card) => (
          <section
            className="limits-card"
            key={`${card.account_id}-${card.balance?.currency ?? ''}`}
          >
            <h2>{card.label}</h2>
            {card.balance && (
              <div>
                <strong>
                  {card.balance.currency} {card.balance.total.toFixed(2)}
                </strong>
                <div className="limits-meta">
                  <span className="limits-badge">Measured</span>
                  <span>{fmtRelative(card.balance.recorded_at, now)}</span>
                </div>
                <p>Granted: {card.balance.granted.toFixed(2)}</p>
                <p>Topped up: {card.balance.topped_up.toFixed(2)}</p>
                {overview?.alerts?.some(
                  (alert) =>
                    alert.account_id === card.account_id &&
                    alert.level === 'low_balance' &&
                    alert.balance?.currency === card.balance?.currency,
                ) && <p className="limits-warning">Low balance</p>}
              </div>
            )}
            {!card.balance && card.windows.length === 0 && (
              <div>
                <strong>No limit data yet</strong> <span className="limits-badge">Unknown</span>
              </div>
            )}
            {card.windows.map((item, index) => (
              <div className="limits-window" key={`${item.window}-${item.scope}-${index}`}>
                <div className="limits-window-heading">
                  <strong>{windowLabel(item)}</strong>
                  <span>{fmtPercentValue(item.used_percent)}</span>
                </div>
                <LimitBar percent={item.used_percent} source={item.source} stale={item.stale} />
                <div className="limits-meta">
                  <span className="limits-badge">
                    {(
                      {
                        measured: 'Measured',
                        blocked: 'Blocked',
                        manual: 'Manual',
                        unknown: 'Unknown',
                      } as Record<string, string>
                    )[item.source] ?? 'Unknown'}
                  </span>
                  <span>
                    {item.resets_at === null
                      ? 'Reset —'
                      : `Resets ${fmtRelative(item.resets_at, now)}`}
                  </span>
                  {item.stale && (
                    <span>
                      stale · {item.age_s === null ? '—' : `${fmtDuration(item.age_s)} old`}
                    </span>
                  )}
                  {item.forecast_s !== null ? (
                    <span>limit in ~{fmtDuration(item.forecast_s)}</span>
                  ) : (
                    item.source !== 'unknown' && (
                      <span>{fmtTokens(item.burn_tokens_per_h)} tokens/h</span>
                    )
                  )}
                </div>
              </div>
            ))}
            {!card.balance && <ManualLimit accountId={card.account_id} reload={reload} />}
          </section>
        ))}
      </div>
    </div>
  );
}
