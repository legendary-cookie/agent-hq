import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Conversation } from './Conversation';
const run = { id: 'run_1', status: 'running', prompt_preview: 'Initial task' };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it('send disabled while working', () => {
  render(<Conversation run={run} output={[]} approvals={[]} />);
  fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'Hello' } });
  expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
});
it('send prepares then posts', async () => {
  const fetcher = vi.fn(async (path: string) => ({ ok: true, json: async () => path.endsWith('/prepare') ? { nonce: 'n' } : { ok: true } }));
  vi.stubGlobal('fetch', fetcher);
  render(<Conversation run={{ ...run, status: 'idle' }} output={[]} approvals={[]} />);
  fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'Hello' } }); fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  expect(fetcher.mock.calls.map(([path]) => path)).toEqual(['/api/runs/run_1/message/prepare', '/api/runs/run_1/message']);
  expect(JSON.parse((fetcher.mock.calls[1] as unknown as [string, RequestInit])[1].body as string)).toEqual({ text: 'Hello', nonce: 'n' });
});
