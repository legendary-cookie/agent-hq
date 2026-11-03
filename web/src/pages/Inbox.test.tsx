import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Inbox } from './Inbox';
import { RunSheet } from '../components/RunSheet';

const approval = { id: 'run_1:req', run_id: 'run_1', tool_name: 'Bash', summary: 'secret command', requested_at: 1, expires_at: Date.now() / 1000 + 120, state: 'waiting' };
let fetcher: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetcher = vi.fn(async (_path: string, _init?: RequestInit) => ({ ok: true, json: async () => [approval] }));
  vi.stubGlobal('fetch', fetcher);
  vi.stubGlobal('EventSource', class { addEventListener() {} close() {} });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it('inbox lists waiting with countdown', async () => {
  render(<Inbox />);
  expect(await screen.findByText('secret command')).toBeInTheDocument();
  expect(screen.getByText(/remaining/)).toBeInTheDocument();
});
it('allow once posts decision', async () => {
  render(<Inbox />); fireEvent.click(await screen.findByRole('button', { name: 'Allow once' }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/approvals/run_1%3Areq', expect.objectContaining({ method: 'POST', body: JSON.stringify({ decision: 'allow_once', note: '' }) , headers: expect.any(Headers) })));
});
it('deny sends note', async () => {
  render(<Inbox />); fireEvent.change(await screen.findByLabelText('Note'), { target: { value: 'No thanks' } }); fireEvent.click(screen.getByRole('button', { name: 'Deny' }));
  await waitFor(() => expect(fetcher.mock.calls.some(([, init]) => init?.body === JSON.stringify({ decision: 'deny', note: 'No thanks' }))).toBe(true));
});
it('ask me only for can_ask tools', () => {
  const options = { run_mode: true, tools: { yes: { levels: ['read_only', 'ask'], harness: 'yes', tested: true, can_ask: true }, no: { levels: ['edit', 'ask'], harness: 'no', tested: true, can_ask: false } }, projects: [], profiles: [], defaults: { level: 'read_only', guard_mode: 'warn', time_limit_min: 30 }, max_concurrent: 3 };
  render(<RunSheet options={options} onClose={() => {}} />);
  expect(screen.getByRole('option', { name: 'Ask me' })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Tool'), { target: { value: 'no' } });
  expect(screen.queryByRole('option', { name: 'Ask me' })).not.toBeInTheDocument();
});
it('notification text has no command', async () => {
  const notify = vi.fn();
  vi.stubGlobal('Notification', Object.assign(notify, { permission: 'granted', requestPermission: vi.fn() }));
  render(<Inbox />);
  fireEvent.click(await screen.findByRole('button', { name: /enable notifications/i }));
  await waitFor(() => expect(notify).toHaveBeenCalled());
  expect(JSON.stringify(notify.mock.calls)).not.toContain('secret command');
});
