import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({ suggest: null as unknown, api: vi.fn() }));
vi.mock('../api/client', () => ({
  useApi: (path: string | null) => ({
    data: path === '/api/agents/new' ? state.suggest : path === '/api/control' ? { elevated_until: Date.now() / 1000 + 600 } : path?.startsWith('/api/fleet') ? { agents: [{ host: 'ct309-template', label: 'Template' }] } : null,
    error: null, loading: false, reload: () => undefined,
  }),
  api: state.api,
}));
import { NewAgent } from './NewAgent';

beforeEach(() => {
  state.suggest = { vmid: 314, ip: '192.0.2.79', ips: ['192.0.2.79'], sources: ['ct309-template'], jobs: [] };
  state.api.mockReset();
  vi.spyOn(window, 'confirm').mockReturnValue(true);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const fill = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });

it('suggests the next container and address, and a short name from the display name', () => {
  render(<NewAgent />);
  expect(screen.getByLabelText('Container number')).toHaveValue('314');
  expect(screen.getByLabelText('IP address')).toHaveValue('192.0.2.79');
  fill('Display name', 'Alex Smith');
  expect(screen.getByLabelText('Short name')).toHaveValue('alex-smith');
  expect(screen.getByText('Used for the container: alex-smith-ai-agent')).toBeInTheDocument();
});

it('creates after confirming, sends the form once and clears the token', async () => {
  state.api.mockResolvedValue({ job: 'j' });
  render(<NewAgent />);
  fill('Display name', 'Nadia');
  fill('Owner', 'Alex Smith');
  fill('New bot token', '1234567890:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA');
  fill('Owner’s Telegram ID', '123456789');
  fireEvent.click(screen.getByRole('button', { name: 'Create agent' }));
  expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('wiped before it can connect'));
  await waitFor(() => expect(state.api).toHaveBeenCalledTimes(1));
  const [path, init] = state.api.mock.calls[0];
  expect(path).toBe('/api/agents/create');
  expect(JSON.parse(init.body)).toMatchObject({ label: 'Nadia', name: 'nadia', vmid: 314, source: 'ct309-template', group: 'Staff' });
  await waitFor(() => expect(screen.getByLabelText('New bot token')).toHaveValue(''));
});

it('shows a server refusal', async () => {
  state.api.mockRejectedValue(new Error('192.0.2.79 is already used by another container'));
  render(<NewAgent />);
  fill('Display name', 'Nadia');
  fill('Owner', 'Nadia');
  fill('New bot token', '1234567890:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA');
  fireEvent.click(screen.getByRole('button', { name: 'Create agent' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('already used');
});

it('shows progress and blocks a second creation while one runs', () => {
  state.suggest = { ...(state.suggest as object), jobs: [{ id: 'j', name: 'nadia', label: 'Nadia', vmid: 314, host: 'ct314-nadia', state: 'running', steps: [{ at: 0, text: 'Wiping the copied memory' }], error: null }] };
  render(<NewAgent />);
  expect(screen.getByText('Nadia (CT 314): in progress')).toBeInTheDocument();
  expect(screen.getByText('Wiping the copied memory')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Create agent' })).toBeDisabled();
});
