import type { fmtDelta } from '../lib/format';
import './components.css';

export function KpiTile({
  label,
  value,
  delta,
  hint,
}: {
  hint?: string;
  label: string;
  value: string;
  delta?: ReturnType<typeof fmtDelta>;
}) {
  return (
    <div className="kpi-tile">
      <div className="component-label">{label}</div>
      <div className="kpi-value">{value}</div>
      {hint && <div className="component-label">{hint}</div>}
      {delta && delta.dir !== 'none' && <div className="component-label">{delta.text}</div>}
    </div>
  );
}
