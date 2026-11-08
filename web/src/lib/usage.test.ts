import { expect, it } from 'vitest';
import type { FleetPlan } from '../api/types';
import { accountLines, fmtPlanPct, unattributedLine, usageLabel, usageView } from './usage';

const plan = (over: Partial<FleetPlan>): FleetPlan => ({ billing: 'plan', used_pct: 64, unattributed_pct: 0, plan_accounts: 2, api_cost_week: 0, accounts: [], ...over });

it('formats plan shares: one decimal under 10%, whole numbers above, and a floor for tiny use', () => {
  expect(fmtPlanPct(64.4)).toBe('64%');
  expect(fmtPlanPct(7.25)).toBe('7.3%');
  expect(fmtPlanPct(3)).toBe('3%');
  expect(fmtPlanPct(0.04)).toBe('<0.1%');
  expect(fmtPlanPct(0)).toBe('0%');
  expect(fmtPlanPct(null)).toBe('—');
});

it('plan billing: plan % first, the API-price value second', () => {
  const view = usageView({ plan: plan({}), planPct: 12.5, apiPaid: 0, value: 6180 });
  expect(view).toMatchObject({ mode: 'plan', main: '13%', secondary: '$6,180 at API prices', short: '$6,180', sort: 12.5 });
  expect(view.title).toMatch(/weekly limits this week/);
  expect(usageLabel(plan({}))).toBe('Plan use');
});

it('api billing: the dollars really paid, with no plan %', () => {
  const view = usageView({ plan: plan({ billing: 'api', used_pct: null, plan_accounts: 0, api_cost_week: 42 }), planPct: null, apiPaid: 3.5, value: 99 });
  expect(view).toMatchObject({ mode: 'api', main: '$3.50', sort: 3.5 });
  expect(view.main).not.toMatch(/%/);
  expect(usageLabel(plan({ billing: 'api' }))).toBe('Paid via API');
});

it('mixed billing: plan % first, then what was paid via API and the API-price value', () => {
  const view = usageView({ plan: plan({ billing: 'mixed', api_cost_week: 20 }), planPct: 4, apiPaid: 2, value: 30 });
  expect(view).toMatchObject({ mode: 'mixed', main: '4%', secondary: '$2.00 paid via API · $30.00 at API prices', sort: 4 });
});

it('no plan data: the old value at API prices, unchanged', () => {
  for (const missing of [undefined, plan({ billing: 'none' })]) {
    const view = usageView({ plan: missing, planPct: 50, value: 4.5 });
    expect(view).toMatchObject({ mode: 'value', main: '$4.50', secondary: null, sort: 4.5 });
  }
  expect(usageView({ plan: undefined, value: 0, unpriced: 10 }).main).toBe('Unpriced');
  expect(usageLabel(undefined, 'Usage value')).toBe('Usage value');
});

it('shows plan use from before 9router only when there is some', () => {
  expect(unattributedLine(plan({ unattributed_pct: 29.1 }))).toEqual({ label: 'Before 9router (old pool, this week)', value: '29%' });
  expect(unattributedLine(plan({ unattributed_pct: 0 }))).toBeNull();
  expect(unattributedLine(plan({ unattributed_pct: null }))).toBeNull();
  expect(unattributedLine(plan({ billing: 'api', unattributed_pct: 5 }))).toBeNull();
});

it('lists each plan account with its use and reset', () => {
  const lines = accountLines(plan({ accounts: [
    { name: 'one', plan: true, used_pct: 80, resets_at: 1000 + 2 * 86400 + 3600, week_start: 0, cost_week: 1 },
    { name: 'key', plan: false, used_pct: null, resets_at: null, week_start: 0, cost_week: 1 },
  ] }), 1000);
  expect(lines).toEqual(['one: 80% used, resets in 2 d 1 h']);
});
