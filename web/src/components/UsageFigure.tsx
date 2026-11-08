import type { UsageInput, UsageView } from '../lib/usage';
import { usageView } from '../lib/usage';

/** A usage figure: plan % (or dollars paid via API) as the headline, the API-price value small and muted.
 *  compact: for table cells, with the short second line; the full wording is in the tooltip. */
export function UsageFigure({ view, compact = false, ...input }: Partial<UsageInput> & { view?: UsageView; compact?: boolean }) {
  const shown = view ?? usageView({ plan: input.plan, planPct: input.planPct, apiPaid: input.apiPaid, value: input.value ?? 0, unpriced: input.unpriced });
  const second = compact ? shown.short : shown.secondary;
  return (
    <span className="usage-fig" title={shown.title} data-mode={shown.mode}>
      <span className="num usage-main">{shown.main}</span>
      {second && <small className="num usage-sub">{second}</small>}
    </span>
  );
}
