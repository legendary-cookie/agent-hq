import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { HqOffice } from './HqOffice';

const NOW = Date.now() / 1000;
const DATA = {
  now: NOW,
  agents: [
    { host: 'ct1', label: 'Assistant', owner: 'Taylor', group: 'Management', department: null, status: 'working', error: null, catching_up: false, last_seen: NOW - 5, activity: { kind: 'read', title: 'Secret plan', at: NOW - 5 }, earlier: [] },
    { host: 'ct2', label: 'Worker', owner: 'Worker', group: 'Staff', department: null, status: 'attention', error: 'ssh failed', catching_up: false, last_seen: NOW - 500, activity: null, earlier: [] },
  ],
  exchanges: [],
  meetings: [],
  feed: [{ at: NOW - 5, type: 'activity', host: 'ct1', kind: 'read', title: 'Secret plan', between_agents: false }],
};

vi.mock('../api/client', () => ({
  useApi: (path: string | null) => ({ data: path === '/api/office' ? DATA : null, error: null, loading: false, reload: () => undefined }),
}));
vi.mock('./Inbox', () => ({ Inbox: () => null }));
vi.mock('../hq-office/engine3d', () => ({ createOffice3D: () => { throw new Error('no WebGL in tests'); } }));
vi.mock('../hq-office/engineFlat', () => ({ createOfficeFlat: () => ({ update: vi.fn(), configure: vi.fn(), fly: vi.fn(), select: vi.fn(), dispose: vi.fn() }), FLAT_STYLES: {} }));

describe('HqOffice', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
  });
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  it('shows real counts and activity', async () => {
    render(<HqOffice />);
    expect(await screen.findByText('Assistant: Secret plan')).toBeTruthy();
    const tally = screen.getByLabelText('Summary');
    expect(tally.textContent).toContain('1Working');
    expect(tally.textContent).toContain('1Need help');
    expect(screen.queryByText('Sample data')).toBeNull();
  });

  it('privacy view hides what the agent is working on', async () => {
    render(<HqOffice />);
    await screen.findByText('Assistant: Secret plan');
    fireEvent.click(screen.getByRole('button', { name: 'Privacy' }));
    expect(screen.queryByText(/Secret plan/)).toBeNull();
    expect(screen.getByText('Assistant: reading files')).toBeTruthy();
  });

  it('falls back to the flat view when 3D can’t start', async () => {
    render(<HqOffice />);
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('flat view'));
    expect(screen.queryByRole('group', { name: 'Time of day' })).toBeNull();
  });

  it('remembers choices', async () => {
    const { unmount } = render(<HqOffice />);
    await screen.findByText('Assistant: Secret plan');
    fireEvent.click(screen.getByRole('button', { name: 'Privacy' }));
    unmount();
    render(<HqOffice />);
    await screen.findByText('Assistant: reading files');
  });
});
