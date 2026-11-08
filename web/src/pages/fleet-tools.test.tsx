import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({ api: vi.fn(), data: {} as Record<string, unknown> }));
vi.mock('../api/client', () => ({
  useApi: (path: string | null) => ({ data: path ? state.data[path.split('?')[0]] ?? null : null, error: null, loading: false, reload: () => undefined }),
  api: state.api,
}));

import { BulkBar, request } from './BulkActions';
import { describe as say } from './Activity';
import { groupFiles, hostFromHash } from './Memory';
import { HealthWarnings } from './Watch';

beforeEach(() => {
  state.api.mockReset();
  state.data = { '/api/control': { elevated_until: Date.now() / 1000 + 3600, managed: ['a', 'b'] } };
  vi.spyOn(window, 'confirm').mockReturnValue(true);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('maps each bulk action to the single-agent call', () => {
  expect(request('restart-claude', 'a')).toEqual(['/api/control/restart-claude', { host: 'a' }]);
  expect(request('reboot', 'a')).toEqual(['/api/control/power', { host: 'a', action: 'reboot' }]);
  expect(request('message', 'a', 'hi')).toEqual(['/api/control/send', { host: 'a', text: 'hi' }]);
});

it('runs an action on each controllable agent in turn and reports failures', async () => {
  state.api.mockImplementation(async (_path: string, init: { body: string }) => {
    if (JSON.parse(init.body).host === 'b') throw new Error('CT is locked');
    return { ok: true };
  });
  render(<BulkBar agents={[{ host: 'a', label: 'Ada' }, { host: 'b', label: 'Bo' }, { host: 'x', label: 'Xen' }]} onClear={() => undefined} />);
  expect(screen.getByText('1 can’t be controlled from HQ')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Restart Claude' }));
  await waitFor(() => expect(screen.getByText('Failed: CT is locked')).toBeInTheDocument());
  expect(screen.getByText('Done')).toBeInTheDocument();
  expect(state.api).toHaveBeenCalledTimes(2);
});

it('sends a broadcast message only after it is written', async () => {
  state.api.mockResolvedValue({ ok: true });
  render(<BulkBar agents={[{ host: 'a', label: 'Ada' }]} onClear={() => undefined} />);
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
  const send = screen.getByRole('button', { name: 'Send to 1 agent' });
  expect(send).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Message to send'), { target: { value: 'Please send the weekly report' } });
  fireEvent.click(send);
  await waitFor(() => expect(state.api).toHaveBeenCalledWith('/api/control/send', { method: 'POST', body: JSON.stringify({ host: 'a', text: 'Please send the weekly report' }) }));
});

it('describes activity in plain words', () => {
  expect(say({ at: 1, action: 'power_reboot', host: 'a' })).toMatchObject({ text: 'Restarted the container', kind: 'control' });
  expect(say({ at: 1, action: 'alert_sent', text: 'A isn’t reporting' }).kind).toBe('alerts');
  expect(say({ at: 1, action: 'restart_claude_failed', error: 'no session' })).toMatchObject({ bad: true, kind: 'problems' });
  expect(say({ at: 1, action: 'memory_edited', path: 'identity.md' }).text).toBe('Edited memory file identity.md');
});

it('groups memory files by folder and reads the agent from the address', () => {
  const files = [{ path: 'identity.md', size: 1, modified: 1 }, { path: 'reference/a.md', size: 1, modified: 1 }, { path: 'reference/b.md', size: 1, modified: 1 }];
  expect(groupFiles(files, '').map(([folder, list]) => [folder, list.length])).toEqual([['', 1], ['reference', 2]]);
  expect(groupFiles(files, 'b.md').map(([folder]) => folder)).toEqual(['reference']);
  expect(hostFromHash('#/memory?host=ct301-x')).toBe('ct301-x');
});

it('shows health warnings only when there are some', () => {
  state.data['/api/watch'] = { warnings: [], checked_at: 1 };
  const { container, unmount } = render(<HealthWarnings />);
  expect(container).toBeEmptyDOMElement();
  unmount();
  state.data['/api/watch'] = { warnings: [{ kind: 'disk', host: 'a', label: 'Ada', text: 'Ada: disk 93% full', since: Date.now() / 1000 - 120 }], checked_at: 1 };
  render(<HealthWarnings />);
  expect(screen.getByText('1 thing needs a look')).toBeInTheDocument();
  expect(screen.getByText('Ada: disk 93% full')).toBeInTheDocument();
});
