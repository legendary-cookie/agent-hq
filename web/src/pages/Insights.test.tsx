import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import type { FleetAgent } from '../api/types';
import { InsightsView, rollUp } from './Insights';
import type { InsightData } from './Insights';

afterEach(cleanup);
const reply = (median: number | null, n = 5) => ({ replies: n, median_s: median, p90_s: median ? median * 3 : null });
const DATA: InsightData = {
  replies: { all: reply(90, 40), by_channel: { telegram: reply(60, 30), whatsapp: reply(300, 10) } },
  tasks: { count: 12, median_s: 120, p90_s: 900, outcomes: { done: 10, error: 2 } },
  reliability: { limit_messages: 3, api_errors: 2, tool_errors: 5, last_limit: { a: 999_000 } },
  tools: { total: 100, top: [{ tool: 'Read', count: 50 }, { tool: 'mcp__shopify__list-orders', count: 10 }] },
  week: Array.from({ length: 7 }, (_, day) => Array.from({ length: 24 }, (_, hour) => (day === 2 && hour === 10 ? 9 : 1))),
  agents: [{ host: 'a', reply: reply(400), limit_messages: 3, api_errors: 0, tool_errors: 1 }, { host: 'b', reply: reply(30), limit_messages: 0, api_errors: 0, tool_errors: 0 }],
  generated_at: 1_000_000,
};
const agent = (host: string, label: string, group: string, owner: string | null, value: number) => ({ host, label, owner, group, status: 'idle', error: null, catching_up: false, last_seen: null, messages_in: 2, messages_out: 1, agent_messages: 0, tasks: 1, errors: 0, cells: [], gap: 0, tokens: 0, cache_read_tokens: 0, value_usd: value, top_tier_usd: 0, unpriced_tokens: 0 }) as FleetAgent;

it('shows reply speed, reliability and where work goes', () => {
  render(<InsightsView range="week" setRange={() => undefined} fleet={null} data={DATA} failed={false} />);
  expect(screen.getByText('1 min')).toBeInTheDocument();
  expect(screen.getByText('Telegram')).toBeInTheDocument();
  expect(screen.getByText('shopify · list-orders')).toBeInTheDocument();
  expect(screen.getByRole('img', { name: 'Busiest hour: Wed 10:00' })).toBeInTheDocument();
  expect(screen.getByText(/3 limit · 1 failed tools/)).toBeInTheDocument();
});

it('rolls agents up by group and owner', () => {
  const rows = rollUp([agent('a', 'A', 'Staff', 'Ann', 2), agent('b', 'B', 'Staff', 'Ann', 1), agent('c', 'C', 'Management', null, 5)], (a) => a.group);
  expect(rows.map((row) => [row.name, row.agents, row.value, row.messages])).toEqual([['Management', 1, 5, 3], ['Staff', 2, 3, 6]]);
});

it('says when there is nothing to show', () => {
  render(<InsightsView range="today" setRange={() => undefined} fleet={null} data={{ ...DATA, agents: [], replies: { all: reply(null, 0), by_channel: {} }, tools: { total: 0, top: [] } }} failed={false} />);
  expect(screen.getByText('No replies to people in this period.')).toBeInTheDocument();
  expect(screen.getByText('No limits or errors in this period.')).toBeInTheDocument();
});
