import './components.css';

export function LimitBar({
  percent,
  source,
  stale,
}: {
  percent: number | null;
  source: string;
  stale: boolean;
}) {
  const known = percent !== null && Number.isFinite(percent) && source !== 'unknown';
  const value = source === 'blocked' ? 100 : known ? Math.max(0, Math.min(100, percent)) : null;
  const color =
    source === 'blocked' || (value !== null && value >= 95)
      ? 'red'
      : value !== null && value >= 80
        ? 'orange'
        : 'green';
  return (
    <div className={`limit-track${stale ? ' limit-stale' : ''}`}>
      {value !== null && (
        <div className={`limit-fill limit-${color}`} style={{ width: `${value}%` }} />
      )}
    </div>
  );
}
