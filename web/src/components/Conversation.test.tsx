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
it('conversation hides system and hook events', () => {
  render(<Conversation run={run} approvals={[]} output={['{"type":"system","subtype":"hook_started","hook_name":"hidden hook"}', '{"type":"rate_limit_event","message":"hidden limit"}', 'not json']} />);
  expect(screen.queryByText(/hidden hook|hidden limit|not json/)).not.toBeInTheDocument();
});
it('conversation shows assistant text tool calls and result', () => {
  const output = ['[agent-hq] turn 1', JSON.stringify({ type: 'assistant', message: { content: [{ type: 'text', text: 'I will help' }, { type: 'tool_use', name: 'Write', input: { file_path: 'hello.txt', content: 'hello' } }] } }), JSON.stringify({ type: 'result', result: 'All done' }), JSON.stringify({ type: 'item.completed', item: { type: 'agent_message', text: 'Codex says hello' } }), JSON.stringify({ type: 'text', part: { type: 'text', text: 'OpenCode says hello' } }), JSON.stringify({ type: 'step_update', text: 'Antigravity says hello' })];
  render(<Conversation run={run} approvals={[{ id: 'a', run_id: 'run_1', tool_name: 'Write', summary: 'hello.txt', requested_at: 1, expires_at: 2, state: 'waiting' }]} output={output} />);
  for (const value of ['turn 1', 'I will help', 'Write hello.txt', 'All done', 'Codex says hello', 'OpenCode says hello', 'Antigravity says hello']) expect(screen.getByText(value)).toBeInTheDocument();
  expect(screen.getByText(/Approval:/)).toBeInTheDocument();
});
it('raw toggle shows original lines', () => {
  render(<Conversation run={run} approvals={[]} output={['{"type":"system","subtype":"init"}', 'unparseable']} />);
  fireEvent.click(screen.getByRole('button', { name: 'Show raw output' }));
  expect(screen.getByText(/unparseable/)).toHaveTextContent('{"type":"system","subtype":"init"}');
});
it('send enabled for finished runs', () => {
  const { rerender } = render(<Conversation run={run} output={[]} approvals={[]} />);
  fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'Hello' } });
  for (const status of ['idle', 'done', 'stopped', 'interrupted', 'timed_out']) {
    rerender(<Conversation run={{ ...run, status }} output={[]} approvals={[]} />);
    expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled();
  }
  for (const status of ['starting', 'running', 'needs_you']) {
    rerender(<Conversation run={{ ...run, status }} output={[]} approvals={[]} />);
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
  }
});
