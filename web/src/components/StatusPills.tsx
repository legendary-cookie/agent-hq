import type { Overview } from '../api/types';
import { fmtTokens, fmtUsd } from '../lib/format';
import { Pill } from './Pill';

export function StatusPills({ overview }: { overview: Overview }) {
  const tokens = overview.tokens;
  return (
    <div className="dashboard-pills">
      <Pill color="var(--working)">{overview.status_counts.working ?? 0} working</Pill>
      <Pill color="var(--active)">{overview.status_counts.active ?? 0} Active (imported)</Pill>
      <Pill color="var(--waiting)">{overview.status_counts.waiting ?? 0} needs you</Pill>
      <Pill color="var(--idle)">{overview.status_counts.idle ?? 0} idle</Pill>
      {overview.best_now?.[0] && (
        <Pill>
          Best now: {overview.best_now[0].provider} · {overview.best_now[0].model}
        </Pill>
      )}
      <Pill>
        Today · {fmtTokens(tokens.input + tokens.output + tokens.cache_read + tokens.cache_write)}{' '}
        tokens ·{' '}
        {overview.value.total_usd === 0 && overview.value.unpriced_tokens > 0
          ? 'value unpriced'
          : fmtUsd(overview.value.total_usd, { estimated: overview.value.estimated })}
      </Pill>
    </div>
  );
}
