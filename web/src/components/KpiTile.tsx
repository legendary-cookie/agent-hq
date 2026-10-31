import type { fmtDelta } from '../lib/format';
import './components.css';

export function KpiTile({
  label,
  value,
  delta,
}: {
  label: string;
  value: string;
  delta?: ReturnType<typeof fmtDelta>;
}) {
  return (
    <div className="kpi-tile">
      <div className="component-label">{label}</div>
      <div className="kpi-value">{value}</div>
      {delta && delta.dir !== 'none' && <div className="component-label">{delta.text}</div>}
    </div>
  );
}
