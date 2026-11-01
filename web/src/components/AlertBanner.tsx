import { useEffect, useState } from 'react';
import type { LimitCard, Overview } from '../api/types';
import { fmtPercentValue } from '../lib/format';
import './components.css';

const severity: Record<string, number> = { blocked: 3, critical: 2, warn: 1 };
const alertKey = (alert: Overview['alerts'][number]) =>
  JSON.stringify([alert.account_id, alert.window, alert.scope]);

export function AlertBanner({
  alerts,
  cards = [],
}: {
  alerts: Overview['alerts'];
  cards?: LimitCard[];
}) {
  const [dismissed, setDismissed] = useState<Record<string, string>>({});
  useEffect(() => {
    setDismissed((previous) => {
      const next = { ...previous };
      for (const [key, level] of Object.entries(previous)) {
        const current = alerts.find((alert) => alertKey(alert) === key);
        if (!current || current.level !== level) delete next[key];
      }
      return Object.keys(next).length === Object.keys(previous).length ? previous : next;
    });
  }, [alerts]);
  const active = alerts
    .filter((alert) => dismissed[alertKey(alert)] !== alert.level)
    .sort((a, b) => (severity[b.level] ?? 0) - (severity[a.level] ?? 0));
  const alert = active[0];
  if (!alert) return null;
  const card = cards.find((item) => item.account_id === alert.account_id);
  const window = card?.windows.find(
    (item) => item.window === alert.window && item.scope === alert.scope,
  );
  const label = card?.label ?? alert.account_id;
  const labels: Record<string, string> = {
    '5h': '5 hours',
    week: 'Week',
    day: 'Day',
    month: 'Month',
  };
  const subject =
    alert.level === 'blocked'
      ? alert.scope && alert.scope !== '*'
        ? alert.scope
        : 'all models'
      : (labels[alert.window ?? ''] ?? alert.window ?? 'limit');
  const reset = window?.resets_at
    ? ` until ${new Date(window.resets_at * 1000).toLocaleString(undefined, {
        weekday: 'short',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      })}`
    : '';
  return (
    <div className="alert-banner" role="alert">
      <span>
        {label} · {subject}{' '}
        {alert.level === 'blocked'
          ? `blocked${reset}`
          : `at ${fmtPercentValue(alert.used_percent)}`}
      </span>
      {active.length > 1 && <a href="#/limits">+{active.length - 1} more</a>}
      <button
        type="button"
        aria-label="Dismiss alert"
        onClick={() => setDismissed({ ...dismissed, [alertKey(alert)]: alert.level })}
      >
        ×
      </button>
    </div>
  );
}
