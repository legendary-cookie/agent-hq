import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { Fleet, FleetAgent } from '../api/types';
import { AgentsList } from './AgentsList';
import { Overview, verdict } from './Overview';
import { Usage } from './Usage';

const agent = (over: Partial<FleetAgent>): FleetAgent => ({
  host: 'box1', label: 'Ada', owner: 'Ada Lane', group: 'Staff', status: 'idle', error: null, catching_up: false,
  last_seen: 1000, messages_in: 3, messages_out: 4, agent_messages: 5, tasks: 2, errors: 0, cells: [0, 1, 6], gap: 0, tokens: 1000, cache_read_tokens: 900, value_usd: 1.5, top_tier_usd: 1, unpriced_tokens: 0, ...over,
});
const fleet: Fleet = {
  window: { start: 0, end: 10800, bucket_s: 3600, count: 3 },
  totals: { agents: 3, working: 1, active: 1, attention: 1, quiet: 0, catching_up: 1, messages: 21, previous_messages: 14, agent_messages: 15, tasks: 6, tokens: 3000, cache_read_tokens: 2700, actions: 200, value_usd: 4.5, top_tier_usd: 3, projected_30d_usd: 19.29, silent_usd: 1.5, silent_agents: 1, unpriced_tokens: 0, previous_value_usd: 3, errors: 4 },
  models: [{ model: 'claude-sonnet-5', tokens: 3000, value_usd: 4.5, unpriced_tokens: 0 }],
  agents: [
    agent({ host: 'box1', status: 'working' }),
    agent({ host: 'box2', label: 'Nova', owner: 'Grace', status: 'attention', error: 'ssh exited 255', gap: 1 }),
    agent({ host: 'box3', label: 'Kit', owner: 'Kim', group: 'Management', catching_up: true, value_usd: 0, unpriced_tokens: 500 }),
    agent({ host: 'box4', label: 'Busy', owner: 'Bo', messages_in: 0, messages_out: 0 }),
  ],
};
const calls = vi.fn();
vi.mock('../api/client', () => ({
  useApi: (path: string | null) => {
    calls(path);
    if (path?.startsWith('/api/fleet')) return { data: fleet, error: null };
    if (path?.startsWith('/api/sessions')) return { data: [{ id: 'box1/claude_code:a', host: 'box1', title: 'Weekly sales summary', tokens: 10, last_event_at: 1 }, { id: 'box1/zylos:comm', host: 'box1', title: 'Zylos messages', tokens: 0, last_event_at: 2 }], error: null };
    return { data: null, error: null };
  },
  stopRun: vi.fn(),
}));

beforeEach(() => { calls.mockClear(); sessionStorage.clear(); localStorage.clear(); });
afterEach(cleanup);

it('says what matters in a sentence, with correct grammar', () => {
  const base = fleet.totals;
  expect(verdict(base)).toEqual(['1 agent working.', '1 needs attention.']);
  expect(verdict({ ...base, working: 3, attention: 2 })).toEqual(['3 agents working.', '2 need attention.']);
  expect(verdict({ ...base, working: 0, attention: 0 })).toEqual(['No agents working.', 'Nothing needs attention.']);
});

it('overview shows the verdict, the unreachable agent and each group', () => {
  render(<Overview />);
  expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('1 agent working. 1 needs attention.');
  expect(screen.getByRole('button', { name: /couldn’t be reached/ })).toBeInTheDocument();
  expect(screen.getByText('Management')).toBeInTheDocument();
  expect(screen.getByText('Staff')).toBeInTheDocument();
});

it('says when history is still being copied', () => {
  render(<Overview />);
  expect(screen.getByText('Still copying history from 1 agent')).toBeInTheDocument();
});

it('compares messages with the period before', () => {
  render(<Overview />);
  expect(screen.getByText('+50% vs the previous 7 days')).toBeInTheDocument();
});

it('shows messages between agents apart from messages with people', () => {
  render(<Overview />);
  expect(screen.getByText('15 more between agents')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Ada' }));
  const sheet = screen.getByRole('complementary', { name: 'Ada details' });
  expect(within(sheet).getByText('Between agents')).toBeInTheDocument();
});

it('marks cells with no data, unlike cells with no activity', () => {
  const { container } = render(<Overview />);
  expect(container.querySelectorAll('.cell-gap')).toHaveLength(1);
  expect(container.querySelector('.cell-gap')).toHaveAttribute('data-tip', expect.stringContaining('no data'));
  expect(container.querySelector('.f-strip .cell-0')).toHaveAttribute('data-tip', expect.stringContaining('0 actions'));
});

it('remembers the period between pages', () => {
  render(<Overview />);
  fireEvent.click(screen.getByRole('button', { name: '30 days' }));
  expect(calls).toHaveBeenCalledWith('/api/fleet?range=month');
  cleanup();
  render(<AgentsList />);
  expect(screen.getByRole('button', { name: '30 days' })).toHaveAttribute('aria-pressed', 'true');
});

it('clicking an agent opens details, without the message log', () => {
  render(<Overview />);
  fireEvent.click(screen.getByRole('button', { name: 'Ada' }));
  const sheet = screen.getByRole('complementary', { name: 'Ada details' });
  expect(within(sheet).getByText('Weekly sales summary')).toBeInTheDocument();
  expect(within(sheet).queryByText('Zylos messages')).not.toBeInTheDocument();
});

it('escape closes details', () => {
  render(<Overview />);
  fireEvent.click(screen.getByRole('button', { name: 'Ada' }));
  fireEvent.keyDown(window, { key: 'Escape' });
  expect(screen.queryByRole('complementary', { name: 'Ada details' })).not.toBeInTheDocument();
});

it('details explain why an agent needs attention', () => {
  render(<Overview />);
  fireEvent.click(screen.getByRole('button', { name: 'Nova' }));
  expect(screen.getByText('ssh exited 255')).toBeInTheDocument();
});

it('agents can be filtered by person', () => {
  render(<AgentsList />);
  fireEvent.change(screen.getByLabelText('Filter by agent or owner'), { target: { value: 'grace' } });
  expect(screen.getByRole('button', { name: 'Nova' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Ada' })).not.toBeInTheDocument();
});

it('agents can be filtered by status, and an empty result says so', () => {
  render(<AgentsList />);
  fireEvent.click(screen.getByRole('button', { name: 'Needs attention' }));
  expect(screen.getByRole('button', { name: 'Nova' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Kit' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Quiet' }));
  expect(screen.getByText(/No agents match/)).toBeInTheDocument();
});

it('usage shows the measures', () => {
  render(<Usage />);
  const measures = within(screen.getByRole('region', { name: 'Usage measures' }));
  expect(measures.getByText('$19.29')).toBeInTheDocument();
  expect(measures.getByText('33% of usage, 1 agent')).toBeInTheDocument();
  expect(measures.getByText('67%')).toBeInTheDocument();
  expect(measures.getByText('$0.75')).toBeInTheDocument();
  expect(measures.getByText('90%')).toBeInTheDocument();
  expect(measures.getByText('2.0%')).toBeInTheDocument();
});

it('usage names agents working with no messages', () => {
  render(<Usage />);
  const heading = screen.getByRole('heading', { name: 'Working with no messages' });
  const section = within(heading.closest('.side-section') as HTMLElement);
  expect(section.getByText('Busy')).toBeInTheDocument();
  expect(section.queryByText('Ada')).not.toBeInTheDocument();
});

it('usage says it is not the bill and shows models', () => {
  render(<Usage />);
  expect(screen.getByText(/It isn’t your bill/)).toBeInTheDocument();
  expect(screen.getByText('claude-sonnet-5')).toBeInTheDocument();
  expect(screen.getByText('$4.50 of usage in the last 7 days, +50% vs the period before.')).toBeInTheDocument();
  expect(screen.getByText('Unpriced')).toBeInTheDocument();
});
