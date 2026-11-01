import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { Office } from './Office';
import { parseRoute } from '../app/router';
import type { Skin } from '../office/skin';

const session = {
  id: 's',
  harness: 'codex',
  title: 'Build feature',
  status: 'working',
  last_event_at: Date.now() / 1000,
  model: 'model',
  account_id: 'a',
  parent_id: null,
};
let sessions: unknown[] = [];
let delegations: unknown[] = [];
let skins: Skin[] = [];
const factory = vi.fn(async () => {
  const skin: Skin = {
    mount: vi.fn(async () => {}),
    render: vi.fn(),
    pick: vi.fn(() => null),
    resize: vi.fn(),
    destroy: vi.fn(),
  };
  skins.push(skin);
  return skin;
});
beforeEach(() => {
  sessions = [];
  delegations = [];
  skins = [];
  localStorage.clear();
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    },
  );
  vi.stubGlobal(
    'EventSource',
    class {
      addEventListener() {}
      close() {}
    },
  );
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => ({
      ok: true,
      json: async () =>
        path.startsWith('/api/sessions?') || path === '/api/sessions'
          ? sessions
          : path.startsWith('/api/delegations')
            ? delegations
            : path === '/api/settings'
              ? { default_skin: 'isometric' }
              : path === '/api/overview'
                ? null
                : { session, events: [], children: [], value: {}, account_limit: null },
    })),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it('Office is the default route', () => expect(parseRoute('')).toBe('office'));
it('Office shows empty state', async () => {
  render(<Office skinFactory={factory} />);
  expect(await screen.findByText('No agents in the last 24 hours')).toBeInTheDocument();
});
it('Office renders accessible npc list', async () => {
  sessions = [session];
  render(<Office skinFactory={factory} />);
  expect(
    await screen.findByRole('button', { name: /codex.*working.*Build feature/i }),
  ).toBeInTheDocument();
});
it('switching skins destroys the previous skin', async () => {
  render(<Office skinFactory={factory} />);
  await waitFor(() => expect(skins.length).toBe(1));
  fireEvent.click(screen.getByRole('button', { name: 'Tower' }));
  await waitFor(() => expect(skins[0].destroy).toHaveBeenCalled());
});
it('skin choice persists in localStorage', () => {
  render(<Office skinFactory={factory} />);
  fireEvent.click(screen.getByRole('button', { name: 'Top-down' }));
  expect(localStorage.getItem('hq-skin')).toBe('topdown');
});
it('clicking accessible npc opens inspector', async () => {
  sessions = [session];
  render(<Office skinFactory={factory} />);
  fireEvent.click(await screen.findByRole('button', { name: /codex.*working.*Build feature/i }));
  expect(await screen.findByLabelText('Session inspector')).toBeInTheDocument();
});
it('ticker shows newest recent delegation', async () => {
  delegations = [
    {
      id: 'd',
      ts: Date.now() / 1000,
      provider: 'codex',
      model: 'gpt',
      task_kind: 'implementation',
      outcome: 'ok',
    },
  ];
  render(<Office skinFactory={factory} />);
  expect(await screen.findByText(/delegated implementation to codex · gpt/)).toBeInTheDocument();
});
