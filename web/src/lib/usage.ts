/** How usage is shown everywhere: as a share of the Claude plans' weekly limits when the fleet runs on
 *  subscription plans through 9router, as dollars only where usage was really paid through an API key,
 *  and as the old "value at API prices" when there's no plan data. */
import type { FleetPlan } from '../api/types';
import { money } from '../pages/fleet-shared';

export const PLAN_NOTE =
  'Share of the Claude plans’ weekly limits this week, estimated from 9router: each agent’s share of each account’s use, weighted by API price. Usage before the move to 9router is spread by the same shares.';

/** 64% · 7.5% · <0.1% · 0% · — */
export function fmtPlanPct(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—';
  if (value <= 0) return '0%';
  if (value < 0.1) return '<0.1%';
  if (value < 10) return `${value.toFixed(1).replace(/\.0$/, '')}%`;
  return `${Math.round(value)}%`;
}

export type UsageMode = 'plan' | 'api' | 'mixed' | 'value';

export function usageMode(plan: FleetPlan | null | undefined): UsageMode {
  if (!plan || plan.billing === 'none' || !plan.billing) return 'value';
  return plan.billing;
}

export interface UsageInput {
  plan: FleetPlan | null | undefined;
  /** Plan share: plan.used_pct for the fleet, plan_pct for an agent or key. */
  planPct?: number | null;
  /** Dollars really paid through API-key accounts: api_cost_week for the fleet, api_paid for an agent. */
  apiPaid?: number | null;
  /** Value at API prices for the period shown. */
  value: number;
  unpriced?: number;
}

export interface UsageView {
  mode: UsageMode;
  /** The headline figure. */
  main: string;
  /** Muted second line, in full ("$X at API prices"). Null when there's nothing to add. */
  secondary: string | null;
  /** The same, in as few characters as fit a table cell. */
  short: string | null;
  /** Tooltip explaining what the figure is. */
  title: string;
  /** What to sort by: the main figure, null when unknown (sorted last). */
  sort: number | null;
}

export function usageView({ plan, planPct, apiPaid, value, unpriced = 0 }: UsageInput): UsageView {
  const mode = usageMode(plan);
  const atApi = money(value, unpriced);
  const pct = planPct ?? null;
  if (mode === 'plan') {
    return { mode, main: fmtPlanPct(pct), secondary: `${atApi} at API prices`, short: atApi, title: `${PLAN_NOTE} ${atApi} is what the same tokens would cost at API prices, not your bill.`, sort: pct };
  }
  if (mode === 'mixed') {
    const paid = money(apiPaid ?? 0);
    return { mode, main: fmtPlanPct(pct), secondary: `${paid} paid via API · ${atApi} at API prices`, short: `${paid} paid · ${atApi}`, title: `${PLAN_NOTE} ${paid} was really paid through API-key accounts this week; ${atApi} is the value of all tokens at API prices.`, sort: pct };
  }
  if (mode === 'api') {
    const paid = money(apiPaid ?? 0);
    return { mode, main: paid, secondary: 'paid via API this week', short: null, title: 'Dollars actually paid through API-key accounts in 9router this week.', sort: apiPaid ?? 0 };
  }
  return { mode, main: atApi, secondary: null, short: null, title: 'What these tokens would cost at API prices. It isn’t your bill.', sort: value };
}

/** The column or tile title that goes with the figure. */
export function usageLabel(plan: FleetPlan | null | undefined, fallback = 'Value'): string {
  const mode = usageMode(plan);
  return mode === 'plan' || mode === 'mixed' ? 'Plan use' : mode === 'api' ? 'Paid via API' : fallback;
}

/** The part of the week's plan use 9router can't put on any agent, as a line, or null when there's none. */
export function unattributedLine(plan: FleetPlan | null | undefined): { label: string; value: string } | null {
  const mode = usageMode(plan);
  const pct = plan?.unattributed_pct;
  if ((mode !== 'plan' && mode !== 'mixed') || pct == null || !(pct > 0)) return null;
  return { label: 'Before 9router (old pool, this week)', value: fmtPlanPct(pct) };
}

/** "in 2 d 4 h" / "in 35 min" / "now" */
export function resetsIn(at: number | null | undefined, now: number) {
  if (!at) return '—';
  const s = at - now;
  if (s <= 0) return 'now';
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `in ${d} d ${h} h` : h ? `in ${h} h ${m} min` : m ? `in ${m} min` : 'in under a minute';
}

/** One line per plan account: "name: 64% used, resets in 2 d 3 h". */
export function accountLines(plan: FleetPlan | null | undefined, now: number): string[] {
  return (plan?.accounts ?? []).filter((a) => a.plan).map((a) =>
    `${a.name}: ${a.used_pct == null ? 'use unknown' : `${fmtPlanPct(a.used_pct)} used`}${a.resets_at ? `, resets ${resetsIn(a.resets_at, now)}` : ''}`);
}
