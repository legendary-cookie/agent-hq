import { useState } from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { RunSheet } from './RunSheet';
import type { RunVerdict } from '../api/types';

describe('Run sheet', () => {
  it('shows off message when run mode off', () => {
    render(<RunSheet options={{ run_mode: false, tools: {}, projects: [], profiles: [], defaults: { level: 'edit', guard_mode: 'warn', time_limit_min: 30 }, max_concurrent: 3 }} onClose={() => {}} />);
    expect(screen.getByText('Run mode is off. Turn it on in config/run.json.')).toBeInTheDocument();
  });
});

import { afterEach, vi } from 'vitest';
import { act, cleanup, fireEvent, waitFor } from '@testing-library/react';
const options = { run_mode: true, tools: { codex: { levels: ['read_only', 'full_auto'], harness: 'codex', tested: 'real' } }, projects: ['project'], profiles: [{ id: 'p', tool: 'codex', account_id: 'a', max_used_percent: 82, blocked_until: null }], defaults: { level: 'read_only', guard_mode: 'warn', time_limit_min: 30 }, max_concurrent: 3 };
const allowed: RunVerdict = { allowed: true, reasons: [], warnings: ['Quota warning'], suggestion: { tool: 'claude', model: 'other', profile: 'p2', command: 'secret template' }, profile: 'p', earliest_reset: null };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function setup(verdict = allowed, enterTask = true) {
  const fetcher = vi.fn().mockImplementation(async (path: string) => ({ ok: true, json: async () => path.endsWith('/prepare') ? { verdict, nonce: 'nonce', expires_at: 100 } : { id: 'run' } }));
  vi.stubGlobal('fetch', fetcher);
  render(<RunSheet options={options} onClose={() => {}} />);
  if (enterTask) fireEvent.change(screen.getByLabelText('Task'), { target: { value: 'Do work' } });
  return fetcher;
}
it('asks to reopen when the run key is missing', () => {
  render(<RunSheet options={{ ...options, run_key_ok: false }} onClose={() => {}} />);
  expect(screen.getByText(/reopen Agent HQ from its launch link to use run mode/i)).toBeInTheDocument();
  expect(screen.queryByLabelText('Task')).not.toBeInTheDocument();
});
it('lists only installed tools and their levels', () => {
  setup();
  expect(screen.getByRole('option', { name: 'codex' })).toBeInTheDocument();
  expect(screen.getByRole('option', { name: 'Read-only' })).toBeInTheDocument();
  expect(screen.queryByRole('option', { name: 'Edit project folder' })).not.toBeInTheDocument();
});
it('start disabled with reason when verdict not allowed', async () => {
  setup({ ...allowed, allowed: false, reasons: ['Blocked'] });
  expect(await screen.findByText('Blocked')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Start run' })).toBeDisabled();
});
it('suggestion shows tool and model only', async () => {
  setup();
  expect(await screen.findByText('Quota warning')).toBeInTheDocument();
  expect(screen.getByText(/82% used/)).toBeInTheDocument();
  expect(screen.getByText('Suggested: claude · other (profile p2)')).toBeInTheDocument();
  expect(screen.queryByText(/secret template/)).not.toBeInTheDocument();
});
it('start sends prepare then start with nonce', async () => {
  const fetcher = setup();
  await screen.findByText('Quota warning');
  fireEvent.click(screen.getByRole('button', { name: 'Start run' }));
  await waitFor(() => expect(fetcher.mock.calls.some((call) => call[0] === '/api/runs')).toBe(true));
  const call = fetcher.mock.calls.find((item) => item[0] === '/api/runs')!;
  expect(JSON.parse(call[1].body).nonce).toBe('nonce');
});
it('full auto needs second confirm', async () => {
  const fetcher = setup();
  fireEvent.change(screen.getByLabelText('Level'), { target: { value: 'full_auto' } });
  await screen.findByText('Quota warning');
  fireEvent.click(screen.getByRole('button', { name: 'Start run' }));
  expect(await screen.findByText('Full auto lets the agent run commands without asking. Start anyway?')).toBeInTheDocument();
  expect(fetcher.mock.calls.some((call) => call[0] === '/api/runs')).toBe(false);
});

it('every run sheet field has an accessible name', () => {
  setup();
  for (const label of ['Tool', 'Model (optional)', 'Project', 'Task', 'Level', 'Guard mode', 'Account', 'Time limit (minutes)']) {
    const control = screen.getByLabelText(label);
    expect(control.id).toBeTruthy();
    expect(document.querySelector(`label[for="${control.id}"]`)).toBeInTheDocument();
  }
});
it('empty task shows hint and skips prepare', async () => {
  const fetcher = setup(allowed, false);
  expect(screen.getByText('Describe the task to start')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Start run' })).toBeDisabled();
  await new Promise((resolve) => setTimeout(resolve, 350));
  expect(fetcher).not.toHaveBeenCalled();
});
it('no tools message', () => {
  render(<RunSheet options={{ ...options, tools: {} }} onClose={() => {}} />);
  expect(screen.getByText('No agent tools are ready on this PC yet. Tools appear after a real test run (see Settings).')).toBeInTheDocument();
  expect(screen.queryByLabelText('Task')).not.toBeInTheDocument();
});
it('sheet stays mounted while closing then unmounts', () => {
  vi.useFakeTimers();
  const onClose = vi.fn();
  function Container() {
    const [open, setOpen] = useState(true);
    return open ? <RunSheet options={options} onClose={() => { onClose(); setOpen(false); }} /> : null;
  }
  render(<Container />);
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  expect(screen.getByRole('dialog')).toBeInTheDocument();
  expect(onClose).not.toHaveBeenCalled();
  act(() => { vi.advanceTimersByTime(2000); });
  expect(onClose).toHaveBeenCalledOnce();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  vi.useRealTimers();
});
