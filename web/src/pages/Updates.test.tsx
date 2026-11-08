import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { UpdatesData } from './Updates';

const state = vi.hoisted(() => ({ updates: null as unknown, api: vi.fn() }));
vi.mock('../api/client', () => ({
  useApi: (path: string | null) => ({
    data: path === '/api/updates' ? state.updates : path === '/api/control' ? { elevated_until: Date.now() / 1000 + 600 } : path?.startsWith('/api/updates/snapshots') ? { snapshots: [{ name: 'hq-claude-1', taken_at: null, description: 'Before HQ claude update' }] } : path?.startsWith('/api/fleet') ? { agents: [{ host: 'ct310', label: 'Intern' }, { host: 'ct306', label: 'Alex' }, { host: 'ct253', label: 'Marketing' }] } : null,
    error: null, loading: false, reload: () => undefined,
  }),
  api: state.api,
}));
import { Updates } from './Updates';

const DATA: UpdatesData = {
  configured: true, checked_at: Date.now() / 1000 - 3600, checking: false,
  latest: { claude_stable: '2.1.311', zylos_latest: '0.9.5', hermes_latest: '0.22.7' },
  agents: {
    ct310: { ok: true, claude: '2.1.281', zylos: '0.9.3', security: 4, reboot: true, list_days: 120, updates_held: true, claude_behind: true, zylos_behind: true },
    ct306: { ok: false, error: 'CHECK FAILED' },
    ct253: { ok: true, claude: '2.1.311', hermes: '0.13.0', hermes_behind: true, security: 0 },
  },
  totals: { checked: 2, claude_behind: 1, zylos_behind: 1, security: 4, reboot: 1, failed: 1 },
  jobs: [],
};

beforeEach(() => { state.updates = DATA; state.api.mockReset(); vi.spyOn(window, 'confirm').mockReturnValue(true); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('shows versions, what is behind and the watchdog time', () => {
  render(<Updates />);
  expect(screen.getByText(/stable is 2.1.311/)).toBeInTheDocument();
  expect(screen.getByText('Checked 1 h ago')).toBeInTheDocument();
  expect(screen.getByText('2.1.281')).toHaveClass('behind');
  expect(screen.getByText('· held')).toBeInTheDocument();
  expect(screen.getByText('CHECK FAILED')).toBeInTheDocument();
});

it('updates selected agents one at a time and reports the result', async () => {
  state.api.mockImplementation(async (path: string) => (path === '/api/updates' ? { ...DATA, jobs: [{ id: 'j', host: 'ct310', kind: 'claude', version: '2.1.311', state: 'done', steps: [], started_at: 0, ended_at: 1, snapshot: 'hq-claude-1', error: null }] } : { job: 'j' }));
  vi.useFakeTimers({ shouldAdvanceTime: true });
  render(<Updates />);
  fireEvent.click(screen.getByRole('checkbox', { name: 'Select Intern' }));
  fireEvent.click(screen.getByRole('button', { name: 'Claude Code 2.1.311' }));
  expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('Each agent gets a snapshot first'));
  await waitFor(() => expect(state.api).toHaveBeenCalledWith('/api/updates/start', { method: 'POST', body: JSON.stringify({ host: 'ct310', kind: 'claude', version: '2.1.311', snapshot: true }) }));
  await vi.advanceTimersByTimeAsync(3500);
  expect(await screen.findByText('Claude Code 2.1.311 installed on Intern.')).toBeInTheDocument();
  vi.useRealTimers();
});

it('stops at the first failure', async () => {
  state.api.mockImplementation(async (path: string) => (path === '/api/updates' ? { ...DATA, jobs: [{ id: 'j', host: 'ct306', kind: 'security', version: null, state: 'failed', steps: [], started_at: 0, ended_at: 1, snapshot: 'hq-security-1', error: 'apt failed' }] } : { job: 'j' }));
  vi.useFakeTimers({ shouldAdvanceTime: true });
  render(<Updates />);
  fireEvent.click(screen.getByRole('checkbox', { name: 'Select Alex' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Select Intern' }));
  fireEvent.click(screen.getByRole('button', { name: 'Security updates' }));
  await vi.advanceTimersByTimeAsync(3500);
  expect(await screen.findByText('Alex: apt failed. Stopped before the remaining agents.')).toBeInTheDocument();
  expect(state.api.mock.calls.filter((call) => call[0] === '/api/updates/start')).toHaveLength(1);
  vi.useRealTimers();
});

it('shows Updating on the agent at once, and a second click starts nothing', async () => {
  let finish = false;
  state.api.mockImplementation(async (path: string) => (path === '/api/updates'
    ? { ...DATA, jobs: [{ id: 'j', host: 'ct310', kind: 'security', version: null, state: finish ? 'done' : 'running', steps: [], started_at: 0, ended_at: null, snapshot: null, error: null }] }
    : { job: 'j' }));
  vi.useFakeTimers({ shouldAdvanceTime: true });
  render(<Updates />);
  fireEvent.click(screen.getByRole('checkbox', { name: 'Select Intern' }));
  const button = screen.getByRole('button', { name: 'Security updates' });
  fireEvent.click(button);
  expect(await screen.findByText('Updating…')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Security updates' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Security updates' }));
  await vi.advanceTimersByTimeAsync(3500);
  finish = true;
  await vi.advanceTimersByTimeAsync(3500);
  expect(state.api.mock.calls.filter((call) => call[0] === '/api/updates/start')).toHaveLength(1);
  expect(window.confirm).toHaveBeenCalledTimes(1);
  vi.useRealTimers();
});

it('rolls back from a snapshot after a clear warning', async () => {
  state.api.mockResolvedValue({ ok: true });
  render(<Updates />);
  fireEvent.click(screen.getAllByRole('button', { name: 'Snapshots' })[1]);
  fireEvent.click(screen.getByRole('button', { name: 'Roll back' }));
  expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining("including the agent's memory and files"));
  await waitFor(() => expect(state.api).toHaveBeenCalledWith('/api/updates/rollback', { method: 'POST', body: JSON.stringify({ host: 'ct310', name: 'hq-claude-1' }) }));
});

it('shows a column only for tools some agent has, and dashes where an agent lacks one', () => {
  render(<Updates />);
  expect(screen.getByRole('columnheader', { name: 'Hermes' })).toBeInTheDocument();
  expect(screen.queryByRole('columnheader', { name: 'Codex' })).not.toBeInTheDocument();
  expect(screen.getByText('0.13.0')).toHaveClass('behind');
});

it('offers a tool update only for selected agents that have the tool, and skips the rest', async () => {
  state.api.mockImplementation(async (path: string) => (path === '/api/updates' ? { ...DATA, jobs: [{ id: 'j', host: 'ct253', kind: 'hermes', version: null, state: 'done', steps: [], started_at: 0, ended_at: 1, snapshot: null, error: null }] } : { job: 'j' }));
  vi.useFakeTimers({ shouldAdvanceTime: true });
  render(<Updates />);
  fireEvent.click(screen.getByRole('checkbox', { name: 'Select Intern' }));
  // The bulk toolbar's buttons, not the table's sortable column headers.
  expect(within(screen.getByRole('toolbar', { name: 'Update selected agents' })).queryByRole('button', { name: /Hermes/ })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Update Zylos' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Select Marketing' }));
  expect(screen.getByRole('button', { name: 'Update Zylos (1)' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Update Hermes (1)' }));
  expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining("Skipped, because Hermes isn’t installed there: Intern."));
  await vi.advanceTimersByTimeAsync(3500);
  const starts = state.api.mock.calls.filter((call) => call[0] === '/api/updates/start');
  expect(starts).toEqual([['/api/updates/start', { method: 'POST', body: JSON.stringify({ host: 'ct253', kind: 'hermes', snapshot: true }) }]]);
  vi.useRealTimers();
});

it('never offers Claude Code to an agent already on that version or newer', () => {
  render(<Updates />);
  fireEvent.click(screen.getByRole('checkbox', { name: 'Select Marketing' }));
  expect(within(screen.getByRole('toolbar', { name: 'Update selected agents' })).queryByRole('button', { name: /Claude Code/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Select Intern' }));
  expect(screen.getByRole('button', { name: 'Claude Code 2.1.311 (1)' })).toBeInTheDocument();
});

it('can update without a snapshot', async () => {
  state.api.mockResolvedValue({ job: 'j' });
  render(<Updates />);
  fireEvent.click(screen.getByRole('checkbox', { name: 'Select Intern' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Snapshot first' }));
  fireEvent.click(screen.getByRole('button', { name: 'Security updates' }));
  expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('No snapshot is taken'));
  await waitFor(() => expect(state.api).toHaveBeenCalledWith('/api/updates/start', { method: 'POST', body: JSON.stringify({ host: 'ct310', kind: 'security', snapshot: false }) }));
});

it('saves the snapshot clean-up and check schedule', async () => {
  state.api.mockResolvedValue({});
  render(<Updates />);
  expect(screen.getByText(/checked every 12 hours and after each update/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
  fireEvent.change(screen.getByLabelText('Delete HQ’s snapshots'), { target: { value: 'after_hours' } });
  fireEvent.change(screen.getByLabelText('Check for new versions'), { target: { value: 'weekly' } });
  fireEvent.change(screen.getByLabelText('Day'), { target: { value: '6' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
  await waitFor(() => expect(state.api).toHaveBeenCalledWith('/api/updates/settings', { method: 'POST', body: JSON.stringify({ snapshot: true, cleanup: 'after_hours', cleanup_hours: 24, schedule: { mode: 'weekly', hours: 12, time: '03:00', day: 6 } }) }));
});

it('check now asks the server to run the audit', async () => {
  state.api.mockResolvedValue({ checking: true });
  render(<Updates />);
  fireEvent.click(screen.getByRole('button', { name: 'Check now' }));
  await waitFor(() => expect(state.api).toHaveBeenCalledWith('/api/updates/check', { method: 'POST', body: '{}' }));
});
