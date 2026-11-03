import { it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { DiffView } from '../components/DiffView';
it('diff shown before merge enabled', () => {
  render(<DiffView diff="+added\n-removed" truncated />);
  expect(screen.getByText(/\+added/)).toBeInTheDocument();
  expect(screen.getByText(/truncated/i)).toBeInTheDocument();
});

import { afterEach, vi } from 'vitest';
import { cleanup, fireEvent, waitFor } from '@testing-library/react';
import { Runs } from './Runs';
const item = { id: 'run_1', tool: 'codex', model: null, account: 'account', project: 'project', workdir: '/tmp', branch: 'branch', base_commit: 'abc', level: 'edit', guard_mode: 'warn', mode: 'run', status: 'done', session_id: null, prompt_preview: 'task', started_at: 1, ended_at: 10, exit_code: 0, time_limit_s: 30, merge_state: null };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function setupRun(status = 'done') {
  const run = { ...item, status };
  const fetcher = vi.fn().mockImplementation(async (path: string, init?: RequestInit) => ({ ok: true, json: async () => path.endsWith('/diff') ? { diff: '+added', truncated: false, branch: 'branch', commit: 'abc' } : path.endsWith('/output') ? ['output'] : path.endsWith('/merge') ? { merge_state: 'conflict' } : init?.method === 'POST' ? {} : path === '/api/runs' ? [run] : run }));
  vi.stubGlobal('fetch', fetcher);
  vi.stubGlobal('EventSource', class { addEventListener() {} close() {} });
  render(<Runs />);
  return fetcher;
}
it('lists runs with status pills', async () => {
  setupRun();
  expect(await screen.findByRole('button', { name: 'done' })).toHaveClass('run-status');
  expect(screen.getByText('account')).toBeInTheDocument();
});
it('stop button calls stopRun', async () => {
  const fetcher = setupRun('running');
  fireEvent.click(await screen.findByRole('button', { name: 'Stop run' }));
  await waitFor(() => expect(fetcher.mock.calls.some((call) => call[0] === '/api/runs/run_1/stop')).toBe(true));
});
it('merge conflict message', async () => {
  setupRun();
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  fireEvent.click(await screen.findByRole('button', { name: 'done' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Merge into project' }));
  expect(await screen.findByText('Merge stopped: conflicts. Your branch is unchanged; resolve it in a terminal.')).toBeInTheDocument();
  vi.restoreAllMocks();
});
it('live run event refreshes row', async () => {
  let listener: (() => void) | undefined;
  let status = 'running';
  vi.stubGlobal('EventSource', class { addEventListener(_name: string, cb: () => void) { listener = cb; } close() {} });
  vi.stubGlobal('fetch', vi.fn().mockImplementation(async (path: string) => ({ ok: true, json: async () => path === '/api/runs' ? [{ ...item, status }] : [] })));
  render(<Runs />);
  await screen.findByRole('button', { name: 'running' });
  status = 'done';
  listener?.();
  expect(await screen.findByRole('button', { name: 'done' }, { timeout: 2500 })).toBeInTheDocument();
});
