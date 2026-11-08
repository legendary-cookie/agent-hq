import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { AccountsData } from './Accounts';

const state = vi.hoisted(() => ({ data: null as unknown, elevated: null as number | null, api: vi.fn(), reload: vi.fn() }));
vi.mock('../api/client', () => ({
  useApi: (path: string | null) => ({
    data: path === '/api/accounts' ? state.data : path === '/api/control' ? { elevated_until: state.elevated } : null,
    error: null, loading: false, reload: state.reload,
  }),
  api: state.api,
}));
import { Accounts, ordered, passwordProblem, usernameProblem } from './Accounts';

const NOW = Date.now() / 1000;
const CATALOG = [
  { id: 'view.overview', label: 'Overview', group: 'Pages', description: 'Overview and Office' },
  { id: 'view.usage', label: 'Usage', group: 'Pages', description: 'Usage and Insights' },
  { id: 'agents.message', label: 'Message agents', group: 'Actions', description: 'Send messages' },
  { id: 'accounts.manage', label: 'Manage accounts', group: 'Actions', description: 'Users and roles' },
];
const DATA: AccountsData = {
  users: [
    { username: 'boss', name: 'The Boss', role: 'admin', disabled: false, created: NOW - 9e6, last_sign_in: NOW - 120, sessions: 2, you: true },
    { username: 'sam', name: 'Sam Lee', role: 'viewer', disabled: true, created: NOW - 9e5, last_sign_in: null, sessions: 0, you: false },
  ],
  roles: [
    { id: 'admin', label: 'Admin', description: 'Everything', permissions: [], builtin: true, locked: true, users: 1 },
    { id: 'viewer', label: 'Viewer', description: 'Looks only', permissions: ['view.overview'], builtin: true, locked: false, users: 1 },
    { id: 'ops', label: 'Ops', description: 'Runs things', permissions: ['view.overview', 'agents.message'], builtin: false, locked: false, users: 0 },
  ],
  catalog: CATALOG,
};

beforeEach(() => { state.data = DATA; state.elevated = NOW + 600; state.api.mockReset(); state.api.mockResolvedValue({ ok: true }); state.reload.mockReset(); });
afterEach(cleanup);

const row = (text: string) => screen.getAllByRole('row').find((r) => r.textContent?.includes(text))!;
const lastBody = () => JSON.parse(String(state.api.mock.calls[state.api.mock.calls.length - 1][1].body));

it('lists users with their role, status, last sign-in and sessions, and marks you', () => {
  render(<Accounts />);
  expect(row('boss')).toHaveTextContent('you');
  expect(row('boss')).toHaveTextContent('Admin');
  expect(row('boss')).toHaveTextContent('active');
  expect(row('boss')).toHaveTextContent('2 min ago');
  expect(row('sam')).toHaveTextContent('Viewer');
  expect(row('sam')).toHaveTextContent('disabled');
  expect(row('sam')).toHaveTextContent('never');
  // No Delete for yourself.
  expect(screen.queryByRole('button', { name: 'Delete boss' })).toBeNull();
  expect(screen.getByRole('button', { name: 'Delete sam' })).toBeInTheDocument();
});

it('shows each role with its permissions, admin read-only and fully ticked', () => {
  render(<Accounts />);
  const admin = screen.getByRole('region', { name: 'Admin role' });
  expect(within(admin).getByText(/Admin can do everything and can’t be changed/)).toBeInTheDocument();
  for (const box of within(admin).getAllByRole('checkbox')) { expect(box).toBeChecked(); expect(box).toBeDisabled(); }
  const viewer = screen.getByRole('region', { name: 'Viewer role' });
  expect(within(viewer).getByRole('checkbox', { name: 'Overview' })).toBeChecked();
  expect(within(viewer).getByRole('checkbox', { name: 'Usage' })).not.toBeChecked();
  expect(within(viewer).getByRole('group', { name: 'Viewer: Actions' })).toBeInTheDocument();
  expect(within(viewer).getByRole('button', { name: 'Delete Viewer role' })).toBeDisabled();
  expect(within(viewer).getByText(/In use by 1 user/)).toBeInTheDocument();
});

it('adds a user after the password check, with the right body', async () => {
  state.elevated = null;
  render(<Accounts />);
  fireEvent.click(screen.getByRole('button', { name: 'Add user' }));
  const form = screen.getByRole('form', { name: 'Add user' });
  fireEvent.change(within(form).getByLabelText('Username'), { target: { value: 'Jo.Ops' } });
  fireEvent.change(within(form).getByLabelText('Name'), { target: { value: 'Jo' } });
  fireEvent.change(within(form).getByLabelText('Role'), { target: { value: 'ops' } });
  fireEvent.change(within(form).getByLabelText('Password'), { target: { value: 'short' } });
  fireEvent.change(within(form).getByLabelText('Repeat password'), { target: { value: 'short' } });
  fireEvent.click(within(form).getByRole('button', { name: 'Add user' }));
  expect(await within(form).findByRole('alert')).toHaveTextContent('at least 12 characters');
  fireEvent.change(within(form).getByLabelText('Password'), { target: { value: 'a long enough password' } });
  fireEvent.change(within(form).getByLabelText('Repeat password'), { target: { value: 'a long enough password' } });
  fireEvent.click(within(form).getByRole('button', { name: 'Add user' }));
  // The password check comes first.
  expect(await screen.findByRole('dialog', { name: 'Confirm your password' })).toBeInTheDocument();
  expect(state.api).not.toHaveBeenCalledWith('/api/accounts', expect.anything());
  state.api.mockResolvedValueOnce({ elevated_until: NOW + 900 });
  fireEvent.change(screen.getByLabelText('HQ password'), { target: { value: 'my-own-password' } });
  fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
  await waitFor(() => expect(state.api).toHaveBeenLastCalledWith('/api/accounts', expect.objectContaining({ method: 'POST' })));
  expect(lastBody()).toEqual({ action: 'create_user', username: 'jo.ops', name: 'Jo', role: 'ops', password: 'a long enough password' });
  expect(await screen.findByText(/jo.ops added/)).toBeInTheDocument();
  expect(state.reload).toHaveBeenCalled();
});

it('saves a role’s ticked permissions', async () => {
  render(<Accounts />);
  const viewer = screen.getByRole('region', { name: 'Viewer role' });
  const save = within(viewer).getByRole('button', { name: 'Save' });
  expect(save).toBeDisabled();
  fireEvent.click(within(viewer).getByRole('checkbox', { name: 'Usage' }));
  fireEvent.click(save);
  await waitFor(() => expect(state.api).toHaveBeenCalled());
  expect(lastBody()).toEqual({ action: 'update_role', id: 'viewer', permissions: ['view.overview', 'view.usage'] });
});

it('deletes a user only after the inline confirm, and shows the server’s refusal', async () => {
  render(<Accounts />);
  fireEvent.click(screen.getByRole('button', { name: 'Delete sam' }));
  expect(state.api).not.toHaveBeenCalled();
  const confirm = screen.getByRole('alertdialog', { name: 'Delete sam?' });
  state.api.mockRejectedValueOnce(new Error('sam still owns a run'));
  fireEvent.click(within(confirm).getByRole('button', { name: 'Delete user' }));
  expect(await screen.findByText('sam still owns a run')).toBeInTheDocument();
  expect(lastBody()).toEqual({ action: 'delete_user', username: 'sam' });
});

it('edits a user and resets a password with a client-side check', async () => {
  render(<Accounts />);
  fireEvent.click(screen.getByRole('button', { name: 'Edit sam' }));
  const edit = screen.getByRole('form', { name: 'Edit sam' });
  fireEvent.change(within(edit).getByLabelText('Role'), { target: { value: 'ops' } });
  fireEvent.click(within(edit).getByRole('checkbox'));
  fireEvent.click(within(edit).getByRole('button', { name: 'Save' }));
  await waitFor(() => expect(state.api).toHaveBeenCalled());
  expect(lastBody()).toEqual({ action: 'update_user', username: 'sam', role: 'ops', disabled: false });
  fireEvent.click(screen.getByRole('button', { name: 'Reset password for sam' }));
  const reset = screen.getByRole('form', { name: 'New password for sam' });
  fireEvent.change(within(reset).getByLabelText('New password'), { target: { value: 'a long enough password' } });
  fireEvent.change(within(reset).getByLabelText('Repeat it'), { target: { value: 'a long enough passwerd' } });
  fireEvent.click(within(reset).getByRole('button', { name: 'Set password' }));
  expect(await within(reset).findByRole('alert')).toHaveTextContent('don’t match');
  fireEvent.change(within(reset).getByLabelText('Repeat it'), { target: { value: 'a long enough password' } });
  fireEvent.click(within(reset).getByRole('button', { name: 'Set password' }));
  await waitFor(() => expect(lastBody()).toEqual({ action: 'set_password', username: 'sam', password: 'a long enough password' }));
});

it('your own row has no disable switch', () => {
  render(<Accounts />);
  fireEvent.click(screen.getByRole('button', { name: 'Edit boss' }));
  const edit = screen.getByRole('form', { name: 'Edit boss' });
  expect(within(edit).queryByRole('checkbox')).toBeNull();
});

it('creates a role starting from a copy of another', async () => {
  render(<Accounts />);
  fireEvent.click(screen.getByRole('button', { name: 'New role' }));
  const form = screen.getByRole('form', { name: 'New role' });
  fireEvent.change(within(form).getByLabelText('Name'), { target: { value: 'Support' } });
  fireEvent.change(within(form).getByLabelText('Start from'), { target: { value: 'ops' } });
  expect(within(form).getByRole('checkbox', { name: 'Message agents' })).toBeChecked();
  fireEvent.click(within(form).getByRole('checkbox', { name: 'Usage' }));
  fireEvent.click(within(form).getByRole('button', { name: 'Create role' }));
  await waitFor(() => expect(state.api).toHaveBeenCalled());
  expect(lastBody()).toEqual({ action: 'create_role', label: 'Support', description: '', permissions: ['view.overview', 'view.usage', 'agents.message'] });
});

it('deletes an unused role after a confirm', async () => {
  render(<Accounts />);
  const ops = screen.getByRole('region', { name: 'Ops role' });
  fireEvent.click(within(ops).getByRole('button', { name: 'Delete Ops role' }));
  fireEvent.click(within(screen.getByRole('alertdialog', { name: 'Delete Ops role?' })).getByRole('button', { name: 'Delete role' }));
  await waitFor(() => expect(state.api).toHaveBeenCalled());
  expect(lastBody()).toEqual({ action: 'delete_role', id: 'ops' });
});

it('checks usernames and passwords the way the server does', () => {
  expect(usernameProblem('sam')).toBeNull();
  expect(usernameProblem('s')).not.toBeNull();
  expect(usernameProblem('1sam')).not.toBeNull();
  expect(usernameProblem('Sam')).not.toBeNull();
  expect(usernameProblem('a'.repeat(33))).not.toBeNull();
  expect(passwordProblem('x'.repeat(12), 'x'.repeat(12))).toBeNull();
  expect(ordered(new Set(['zzz.custom', 'agents.message', 'view.overview']), CATALOG)).toEqual(['view.overview', 'agents.message', 'zzz.custom']);
});
