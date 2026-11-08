import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { RouterData } from './Router';

const state = vi.hoisted(() => ({ data: null as unknown, fleet: null as unknown }));
vi.mock('../api/client', () => ({
  useApi: (path: string) => ({
    data: path === '/api/router' ? state.data : path?.startsWith('/api/fleet') ? state.fleet : path === '/api/control' ? { elevated_until: Date.now() / 1000 + 600 } : null,
    error: null, loading: false, reload: () => undefined,
  }),
  api: vi.fn(),
}));
import { RouterPage, until } from './Router';

const NOW = Date.now() / 1000;
const DATA: RouterData = {
  configured: true, error: null,
  snapshot: {
    found: true, vmid: '122', status: 'running', up: true, version: '0.5.112', checked_at: NOW - 30,
    services: { '9router': 'active', headroom: 'active' }, health: 200, in_use: 'c3', rotation_ran_at: NOW - 600,
    connections: [
      { id: 'c2', provider: 'claude', auth: 'oauth', name: 'two@example.test', email: 'two@example.test', priority: 2, active: true, test_status: 'active', token_expires_at: null, last_error: null, last_error_at: null, error_code: null, backoff_level: 1, rate_limited_until: NOW + 1200, limit: { window: 'weekly (7d)', left_pct: 34, resets_at: NOW + 2 * 86400 }, rotation_position: 2, requests_24h: 0, tokens_24h: 0, last_used_at: null, auto_ping: false },
      { id: 'c3', provider: 'claude', auth: 'oauth', name: 'three@example.test', email: 'three@example.test', priority: 1, active: true, test_status: 'active', token_expires_at: null, last_error: null, last_error_at: null, error_code: null, backoff_level: 0, rate_limited_until: null, limit: { window: 'weekly (7d)', left_pct: 58, resets_at: NOW + 3600 }, rotation_position: 1, requests_24h: 12, tokens_24h: 3400, last_used_at: NOW - 60, auto_ping: true },
    ],
    settings: { fallbackStrategy: 'fill-first', headroomEnabled: true, autoPing: { enabled: false, connections: {}, schedules: {} } },
    combos: [{ name: 'default', kind: null, models: '["cc/claude-opus-5-5","cc/claude-sonnet-5-5"]' }],
    api_keys: [{ id: 'k0', name: 'Default Key', active: true, created: '', restricted: false, allow: [], vmid: null, requests_24h: 0, requests_30d: 12, tokens_30d: 3400, cost_30d: 1.25, errors_30d: 1, last_used_at: NOW - 60 }],
    models: { claude: [] }, catalog: { models: 5613, providers: 25, synced: null },
    usage: { total: { requests: 12, prompt_tokens: 3000, completion_tokens: 400, cost: 1.25, errors: 1 }, daily_tokens: Array(30).fill(0), days: 30,
      by_model: [{ name: 'claude/claude-opus-5-5', requests: 12, tokens: 3400, cost: 1.25 }], by_account: [], by_key: [] },
    errors: [],
  },
};

beforeEach(() => { state.data = DATA; localStorage.clear(); });
afterEach(cleanup);

it('shows accounts in rotation order, the one in use, limits and reset times', () => {
  render(<RouterPage />);
  const rows = screen.getAllByRole('row').filter((r) => r.textContent?.includes('@example.test'));
  expect(rows[0]).toHaveTextContent('three@example.test');
  expect(rows[0]).toHaveTextContent('in use');
  expect(rows[0]).toHaveTextContent('58%');
  expect(rows[1]).toHaveTextContent(/cooling down, in (19|20) min/);
  expect(screen.getByText('1/2')).toBeInTheDocument();
  expect(screen.getByText(/next request goes to three@example.test/)).toBeInTheDocument();
  // The next reset, in the totals and on the account's row (a second may have passed since NOW).
  expect(screen.getAllByText(/^in (1 h 0 min|59 min)$/)).toHaveLength(2);
});

it('shows usage, combos and settings', () => {
  render(<RouterPage />);
  expect(screen.getByText('claude/claude-opus-5-5')).toBeInTheDocument();
  expect(screen.getByText('cc/claude-sonnet-5-5')).toBeInTheDocument();
  expect(screen.getByText(/Fill first/)).toBeInTheDocument();
  expect(screen.getByText('Default Key')).toBeInTheDocument();
});

it('explains when 9router is missing or down', () => {
  state.data = { ...DATA, snapshot: { found: false } };
  render(<RouterPage />);
  expect(screen.getByText(/didn’t find a container/)).toBeInTheDocument();
  cleanup();
  state.data = { ...DATA, snapshot: { ...DATA.snapshot!, up: false } };
  render(<RouterPage />);
  expect(screen.getByText('Down')).toBeInTheDocument();
});

it('lists API keys by name only, with the agent from the name and saved assignments', async () => {
  const { api } = await import('../api/client');
  state.data = {
    ...DATA,
    assignments: { k2: { agents: [], tools: ['n8n'], note: 'workflows' } },
    snapshot: { ...DATA.snapshot!, api_keys: [
      { id: 'k1', name: 'ct201-assistant', active: true, created: '', restricted: true, allow: ['default', 'cc/claude-opus-5-5'], vmid: 201, requests_24h: 1, requests_30d: 9, tokens_30d: 1200, cost_30d: 0.2, errors_30d: 0, last_used_at: NOW - 60 },
      { id: 'k2', name: 'automation', active: true, created: '', restricted: false, allow: [], vmid: null, requests_24h: 0, requests_30d: 0, tokens_30d: 0, cost_30d: 0, errors_30d: 0, last_used_at: null },
      { id: 'k3', name: 'ct401-klaviyo-specialist', active: false, created: '', restricted: true, allow: [], vmid: 401, requests_24h: 0, requests_30d: 0, tokens_30d: 0, cost_30d: 0, errors_30d: 0, last_used_at: null },
    ] },
  };
  state.fleet = { agents: [{ host: 'ct201-assistant', label: 'Assistant AI' }] };
  render(<RouterPage />);
  const row = (name: string) => screen.getAllByRole('row').find((r) => r.textContent?.includes(name))!;
  expect(row('ct201-assistant')).toHaveTextContent('Assistant AI');
  expect(row('ct201-assistant')).not.toHaveTextContent('from its name');
  expect(row('ct401-klaviyo-specialist')).toHaveTextContent('paused');
  expect(row('ct201-assistant')).toHaveTextContent('2 allowed');
  expect(row('automation')).toHaveTextContent('n8n · workflows');
  expect(row('ct401-klaviyo-specialist')).toHaveTextContent('CT 401 (not in HQ)');
  expect(screen.getByText(/3 keys · 2 active · 1 unused in 30 days/)).toBeInTheDocument();
  vi.mocked(api).mockResolvedValue({});
  fireEvent.click(screen.getByRole('button', { name: 'Assign automation' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Assistant AI' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await waitFor(() => expect(api).toHaveBeenCalledWith('/api/router/keys', { method: 'POST', body: JSON.stringify({ id: 'k2', agents: ['ct201-assistant'], tools: ['n8n'], note: 'workflows' }) }));
});

it('copies, pauses, deletes after a confirm, and creates keys with the agent keys’ models', async () => {
  const { api } = await import('../api/client');
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
  state.data = { ...DATA, assignments: {}, snapshot: { ...DATA.snapshot!, api_keys: [
    { id: 'k1', name: 'ct201-assistant', active: true, created: '', restricted: true, allow: ['default', 'cc/claude-opus-5-5'], vmid: 201, requests_24h: 0, requests_30d: 0, tokens_30d: 0, cost_30d: 0, errors_30d: 0, last_used_at: null },
  ] } };
  state.fleet = { agents: [] };
  render(<RouterPage />);
  expect(screen.queryByText(/sk-/)).not.toBeInTheDocument();
  vi.mocked(api).mockReset();
  vi.mocked(api).mockResolvedValueOnce({ key: 'sk-hidden-value' });
  fireEvent.click(screen.getByRole('button', { name: 'Copy ct201-assistant' }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith('sk-hidden-value'));
  expect(api).toHaveBeenCalledWith('/api/router/key-action', { method: 'POST', body: JSON.stringify({ action: 'reveal', id: 'k1' }) });
  expect(screen.queryByText(/sk-hidden-value/)).not.toBeInTheDocument();
  vi.mocked(api).mockResolvedValueOnce({ ok: true });
  fireEvent.click(screen.getByRole('button', { name: 'Pause ct201-assistant' }));
  await waitFor(() => expect(api).toHaveBeenLastCalledWith('/api/router/key-action', { method: 'POST', body: JSON.stringify({ action: 'pause', id: 'k1' }) }));
  fireEvent.click(screen.getByRole('button', { name: 'Delete ct201-assistant' }));
  vi.mocked(api).mockResolvedValueOnce({ ok: true });
  fireEvent.click(screen.getByRole('button', { name: 'Delete key' }));
  await waitFor(() => expect(api).toHaveBeenLastCalledWith('/api/router/key-action', { method: 'POST', body: JSON.stringify({ action: 'delete', id: 'k1' }) }));
  fireEvent.click(screen.getByRole('button', { name: 'New key' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'n8n-main' } });
  vi.mocked(api).mockResolvedValueOnce({ id: 'k9', name: 'n8n-main', key: 'sk-new' });
  fireEvent.click(screen.getByRole('button', { name: 'Create key' }));
  await waitFor(() => expect(api).toHaveBeenLastCalledWith('/api/router/key-action', { method: 'POST', body: JSON.stringify({ action: 'create', name: 'n8n-main', access: { restricted: true, allow: ['default', 'cc/claude-opus-5-5'] } }) }));
  expect(await screen.findByText(/is ready/)).toBeInTheDocument();
  expect(screen.queryByText(/sk-new/)).not.toBeInTheDocument();
});

it('shows this week’s plan use per key, the old pool, and sorts keys by it', () => {
  const key = (id: string, name: string, plan_pct: number | null) => ({ id, name, active: true, created: '', restricted: false, allow: [], vmid: null, requests_24h: 0, requests_30d: 1, tokens_30d: 1, cost_30d: 1, errors_30d: 0, last_used_at: null, plan_pct, cost_week: 3, api_paid: 0 });
  state.data = { ...DATA, assignments: {}, snapshot: { ...DATA.snapshot!,
    plan: { billing: 'plan', used_pct: 57.7, unattributed_pct: 29.1, plan_accounts: 2, api_cost_week: 0, accounts: [{ name: 'three@example.test', plan: true, used_pct: 42, resets_at: NOW + 3600, week_start: NOW - 86400, cost_week: 6 }] },
    api_keys: [key('k1', 'small-key', 0.04), key('k2', 'big-key', 20), key('k3', 'idle-key', null)] } };
  state.fleet = { agents: [] };
  render(<RouterPage />);
  const totals = screen.getByRole('region', { name: 'Router totals' });
  expect(totals).toHaveTextContent('58%');
  expect(totals).toHaveTextContent('Before 9router (old pool, this week): 29%');
  const table = screen.getByRole('table', { name: 'API keys' });
  expect(table).toHaveTextContent('<0.1%');
  expect(table).toHaveTextContent('Before 9router (old pool, this week)');
  fireEvent.click(screen.getByRole('button', { name: /Plan use, this week/ }));
  const names = within(table).getAllByRole('row').slice(1).map((r) => within(r).getAllByRole('cell')[0].textContent).filter((t) => t?.endsWith('-key'));
  expect(names).toEqual(['big-key', 'small-key', 'idle-key']);
});

it('words times until a reset', () => {
  expect(until(1000 + 2 * 86400 + 3 * 3600, 1000)).toBe('in 2 d 3 h');
  expect(until(1000 + 300, 1000)).toBe('in 5 min');
  expect(until(500, 1000)).toBe('now');
  expect(until(null, 1000)).toBe('—');
});
