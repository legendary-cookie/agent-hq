import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { AgentSettingsData } from './AgentSettings';

const state = vi.hoisted(() => ({ settings: null as unknown, api: vi.fn() }));
vi.mock('../api/client', () => ({
  useApi: (path: string | null) => ({ data: path?.startsWith('/api/agents/settings') ? state.settings : path === '/api/control' ? { elevated_until: Date.now() / 1000 + 600 } : null, error: null, loading: false, reload: () => undefined }),
  api: state.api,
}));
import { AgentSettings } from './AgentSettings';

const DATA: AgentSettingsData = {
  container: { cores: 2, memory: 2048, swap: 512, onboot: true },
  claude: { model: '', auto_updates: false },
  telegram: { enabled: true, owner_chat_id: '111111', owner_username: 'nadia', dm_policy: 'allowlist', dm_allow: ['111111', '222222'], group_policy: 'allowlist', groups: ['-1001'] },
  services: [{ name: 'zylos-telegram', enabled: 'enabled', active: 'active' }, { name: 'zylos-hxa-connect', enabled: 'disabled', active: 'inactive' }],
  timezone: 'Asia/Singapore',
  identity: '# Identity\nI am Nadia’s agent.',
};

beforeEach(() => { state.settings = DATA; state.api.mockReset(); state.api.mockResolvedValue({ done: ['Saved'] }); vi.spyOn(window, 'confirm').mockReturnValue(true); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const openIt = () => { render(<AgentSettings host="ct310" label="Intern" controlled />); fireEvent.click(screen.getByRole('button', { name: /Settings/ })); };
const lastBody = () => JSON.parse(state.api.mock.calls[state.api.mock.calls.length - 1][1].body);

it('stays closed until asked, and hides for agents HQ does not control', () => {
  render(<AgentSettings host="ct310" label="Intern" controlled={false} />);
  expect(screen.queryByText('Settings')).toBeNull();
  cleanup();
  render(<AgentSettings host="ct310" label="Intern" controlled />);
  expect(screen.queryByLabelText('Persona')).toBeNull();
});

it('saves container size', async () => {
  openIt();
  fireEvent.change(screen.getByLabelText('CPU cores'), { target: { value: '4' } });
  fireEvent.click(screen.getAllByRole('button', { name: 'Save' })[0]);
  await waitFor(() => expect(lastBody()).toEqual({ host: 'ct310', section: 'container', values: { cores: 4, memory: 2048, swap: 512, onboot: true } }));
  expect(await screen.findByText('Saved.')).toBeInTheDocument();
});

it('warns before letting Claude Code update itself', async () => {
  openIt();
  fireEvent.click(screen.getByLabelText('Let Claude Code update itself'));
  fireEvent.click(screen.getAllByRole('button', { name: 'Save' })[1]);
  expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('switched off on purpose'));
  await waitFor(() => expect(lastBody().values).toEqual({ model: '', auto_updates: true }));
});

it('edits Telegram access and removes a group', async () => {
  openIt();
  fireEvent.change(screen.getByLabelText('Allowed people (Telegram IDs)'), { target: { value: '111111, 333333' } });
  fireEvent.click(screen.getByRole('button', { name: 'Remove group -1001' }));
  fireEvent.click(screen.getAllByRole('button', { name: 'Save' })[2]);
  await waitFor(() => expect(lastBody().values).toMatchObject({ dm_allow: ['111111', '333333'], groups: [] }));
});

it('warns before switching on HXA Connect', async () => {
  openIt();
  fireEvent.click(screen.getByRole('button', { name: 'Switch on' }));
  expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('own HXA identity'));
  await waitFor(() => expect(lastBody().values).toEqual({ name: 'zylos-hxa-connect', action: 'enable' }));
});

it('keeps an agent’s full model ID when saving other Claude settings', async () => {
  state.settings = { ...DATA, claude: { model: 'claude-opus-5-5', auto_updates: false } };
  openIt();
  expect(screen.getByLabelText('Model')).toHaveValue('claude-opus-5-5');
  fireEvent.click(screen.getAllByRole('button', { name: 'Save' })[1]);
  await waitFor(() => expect(lastBody().values).toEqual({ model: 'claude-opus-5-5', auto_updates: false }));
});

it('saves the persona', async () => {
  openIt();
  fireEvent.change(screen.getByLabelText('Persona'), { target: { value: '# New persona' } });
  const saves = screen.getAllByRole('button', { name: 'Save' }); fireEvent.click(saves[saves.length - 1]);
  await waitFor(() => expect(lastBody()).toEqual({ host: 'ct310', section: 'identity', values: { text: '# New persona' } }));
});

it('shows a refusal', async () => {
  state.api.mockRejectedValue(new Error('use a time zone name such as Asia/Singapore'));
  openIt();
  fireEvent.change(screen.getByLabelText('Time zone'), { target: { value: 'Mars' } });
  fireEvent.click(screen.getAllByRole('button', { name: 'Save' })[3]);
  expect(await screen.findByText('use a time zone name such as Asia/Singapore')).toBeInTheDocument();
});
