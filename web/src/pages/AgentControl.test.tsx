import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ControlData } from './AgentControl';

const state = vi.hoisted(() => ({ control: null as unknown, session: null as unknown, api: vi.fn() }));
vi.mock('../api/client', () => ({
  useApi: (path: string | null) => ({ data: path === '/api/control' ? state.control : path?.startsWith('/api/control/session') ? state.session : null, error: null, loading: false, reload: () => undefined }),
  api: state.api,
}));
const perms = vi.hoisted(() => ({ allowed: null as string[] | null }));
vi.mock('../api/me', () => ({ useMe: () => ({ me: null, loaded: true, can: (p: string) => perms.allowed === null || perms.allowed.includes(p) }) }));
vi.mock('./Terminal', async (actual) => ({ ...(await actual<typeof import('./Terminal')>()), Terminal: ({ mode }: { mode: string }) => <div>terminal {mode}</div> }));
import { AgentControl } from './AgentControl';
import { terminalUrl } from './Terminal';

const running: ControlData = { configured: true, managed: ['ct306'], containers: { ct306: { vmid: 306, status: 'running', uptime: 7200, cpu: .1, cpus: 2, mem: 1, maxmem: 4 } }, elevated_until: null };

beforeEach(() => { perms.allowed = null; state.control = running; state.session = { session: 'claude-alex', restartable: true }; state.api.mockReset(); vi.spyOn(window, 'confirm').mockReturnValue(true); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('hides itself for agents HQ does not control', () => {
  render(<AgentControl host="ct999" label="X" />);
  expect(screen.queryByText('Control')).toBeNull();
});

it('explains when Proxmox access is missing', () => {
  state.control = { ...running, configured: false };
  render(<AgentControl host="ct306" label="Alex" />);
  expect(screen.getByText(/needs its Proxmox key/)).toBeInTheDocument();
});

it('shows the right buttons for a running container', () => {
  render(<AgentControl host="ct306" label="Alex" />);
  expect(screen.getByText('Container running')).toBeInTheDocument();
  expect(screen.getByText('Claude session: claude-alex')).toBeInTheDocument();
  for (const name of ['Watch terminal', 'Take control', 'Root shell', 'Restart Claude', 'Restart container', 'Shut down', 'Force stop']) expect(screen.getByRole('button', { name })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Start' })).toBeNull();
});

it('hides the buttons the role is not allowed to use', () => {
  perms.allowed = ['view.transcripts', 'agents.message'];
  render(<AgentControl host="ct306" label="Alex" />);
  expect(screen.getByRole('button', { name: 'Watch terminal' })).toBeInTheDocument();
  expect(screen.getByLabelText('Message to Alex')).toBeInTheDocument();
  for (const name of ['Take control', 'Root shell', 'Restart Claude', 'Restart container', 'Shut down', 'Force stop']) expect(screen.queryByRole('button', { name })).toBeNull();
});

it('offers only Start for a stopped container', () => {
  state.control = { ...running, containers: { ct306: { ...running.containers.ct306, status: 'stopped' } } };
  render(<AgentControl host="ct306" label="Alex" />);
  expect(screen.getByRole('button', { name: 'Start' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Root shell' })).toBeNull();
});

it('watching needs no password, but taking control asks for it first', async () => {
  render(<AgentControl host="ct306" label="Alex" />);
  fireEvent.click(screen.getByRole('button', { name: 'Watch terminal' }));
  expect(await screen.findByText('terminal watch')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Take control' }));
  expect(await screen.findByRole('dialog', { name: 'Confirm your password' })).toBeInTheDocument();
  state.api.mockResolvedValueOnce({ elevated_until: Date.now() / 1000 + 900 });
  fireEvent.change(screen.getByLabelText('HQ password'), { target: { value: 'secret-pass-123' } });
  fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
  expect(await screen.findByText('terminal control')).toBeInTheDocument();
  expect(state.api).toHaveBeenCalledWith('/api/control/elevate', { method: 'POST', body: JSON.stringify({ password: 'secret-pass-123' }) });
});

it('restarts after confirming, without asking again inside the 8 hours', async () => {
  state.control = { ...running, elevated_until: Date.now() / 1000 + 600 };
  state.api.mockResolvedValue({ ok: true });
  render(<AgentControl host="ct306" label="Alex" />);
  fireEvent.click(screen.getByRole('button', { name: 'Restart container' }));
  await waitFor(() => expect(state.api).toHaveBeenCalledWith('/api/control/power', { method: 'POST', body: JSON.stringify({ host: 'ct306', action: 'reboot' }) }));
  expect(await screen.findByText('Restart container done.')).toBeInTheDocument();
});

it('cancelling the confirm does nothing', () => {
  vi.spyOn(window, 'confirm').mockReturnValue(false);
  render(<AgentControl host="ct306" label="Alex" />);
  fireEvent.click(screen.getByRole('button', { name: 'Force stop' }));
  expect(state.api).not.toHaveBeenCalled();
});

it('sends a message into the Claude session', async () => {
  state.control = { ...running, elevated_until: Date.now() / 1000 + 600 };
  state.api.mockResolvedValue({ ok: true });
  render(<AgentControl host="ct306" label="Alex" />);
  fireEvent.change(screen.getByLabelText('Message to Alex'), { target: { value: 'Please send the weekly report' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  await waitFor(() => expect(state.api).toHaveBeenCalledWith('/api/control/send', { method: 'POST', body: JSON.stringify({ host: 'ct306', text: 'Please send the weekly report' }) }));
});

it('builds a secure terminal address on HTTPS', () => {
  expect(terminalUrl('ct306', 'watch', 't/k', { protocol: 'https:', host: 'hq.test' })).toBe('wss://hq.test/api/terminal?host=ct306&mode=watch&token=t%2Fk');
});
