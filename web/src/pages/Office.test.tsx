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
it('Overview is the default route', () => expect(parseRoute('')).toBe('overview'));
it('Office shows empty state', async () => {
  render(<Office skinFactory={factory} />);
  expect(await screen.findByText('No agents in the last 24 hours')).toBeInTheDocument();
});
it('replay empty state names the moment, not the last 24 hours', async () => {
  render(<Office skinFactory={factory} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Replay' }));
  expect(await screen.findByText('No agents at this moment')).toBeInTheDocument();
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
it('no walks when data first arrives', async () => {
  const sceneModule = await import('../office/scene');
  const build = vi.spyOn(sceneModule, 'buildScene');
  sessions = [
    { ...session, id: 'parent', harness: 'claude_code' },
    { ...session, id: 'child', parent_id: 'parent' },
  ];
  delegations = [
    {
      id: 'd',
      ts: Date.now() / 1000,
      provider: 'codex',
      model: 'm',
      task_kind: 'work',
      outcome: 'ok',
    },
  ];
  render(<Office skinFactory={factory} />);
  await screen.findByRole('button', { name: /codex.*Build feature/i });
  expect(build.mock.results[build.mock.results.length - 1]?.value.walks).toEqual([]);
  build.mockRestore();
});
it('filtering does not move seats', async () => {
  const sceneModule = await import('../office/scene');
  const build = vi.spyOn(sceneModule, 'buildScene');
  sessions = [
    { ...session, id: 'parent', harness: 'claude_code' },
    { ...session, id: 'child', parent_id: 'parent' },
  ];
  render(<Office skinFactory={factory} />);
  await screen.findByRole('button', { name: /codex.*Build feature/i });
  const initial = build.mock.results[build.mock.results.length - 1]?.value.npcs.find(
    (n: { id: string }) => n.id === 'child',
  )?.deskId;
  fireEvent.change(screen.getByLabelText('Harness'), { target: { value: 'claude_code' } });
  fireEvent.change(screen.getByLabelText('Harness'), { target: { value: '' } });
  const last = build.mock.results[build.mock.results.length - 1]?.value;
  expect(last.npcs.find((n: { id: string }) => n.id === 'child').deskId).toBe(initial);
  expect(last.walks).toEqual([]);
  build.mockRestore();
});
it('mount failure shows fallback', async () => {
  const failedFactory = async () =>
    ({
      mount: async () => {
        throw new Error('WebGL');
      },
      render: vi.fn(),
      pick: vi.fn(),
      resize: vi.fn(),
      destroy: vi.fn(),
    }) as Skin;
  render(<Office skinFactory={failedFactory} />);
  expect(
    await screen.findByText('The office view could not start (WebGL unavailable).'),
  ).toBeInTheDocument();
});
