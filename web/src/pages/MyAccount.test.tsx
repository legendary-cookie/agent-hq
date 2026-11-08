import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('../api/client', () => ({ api: state.api, useApi: () => ({ data: null, error: null, loading: false, reload: () => undefined }) }));
vi.mock('../api/me', async (actual) => ({
  ...(await actual<typeof import('../api/me')>()),
  useMe: () => ({ me: { username: 'sam', name: 'Sam Lee', role: 'viewer', role_label: 'Viewer', permissions: [], admin: false }, loaded: true, can: () => false }),
}));
import { MyAccount } from './MyAccount';

beforeEach(() => state.api.mockReset());
afterEach(cleanup);

const fill = (current: string, next: string, repeat: string) => {
  fireEvent.change(screen.getByLabelText('Current password'), { target: { value: current } });
  fireEvent.change(screen.getByLabelText('New password'), { target: { value: next } });
  fireEvent.change(screen.getByLabelText('Repeat new password'), { target: { value: repeat } });
  fireEvent.click(screen.getByRole('button', { name: 'Change password' }));
};

it('shows who you are', () => {
  render(<MyAccount />);
  expect(screen.getByText('sam')).toBeInTheDocument();
  expect(screen.getByText('Viewer')).toBeInTheDocument();
});

it('checks the new password before sending, then changes it', async () => {
  render(<MyAccount />);
  fill('old-password-1', 'short', 'short');
  expect(await screen.findByText(/at least 12 characters/)).toBeInTheDocument();
  expect(state.api).not.toHaveBeenCalled();
  state.api.mockResolvedValueOnce({ ok: true });
  fill('old-password-1', 'a brand new password', 'a brand new password');
  await waitFor(() => expect(state.api).toHaveBeenCalledWith('/api/account/password', { method: 'POST', body: JSON.stringify({ current: 'old-password-1', new: 'a brand new password' }) }));
  expect(await screen.findByText(/Password changed/)).toBeInTheDocument();
  expect(screen.getByLabelText('Current password')).toHaveValue('');
});

it('shows the server’s refusal', async () => {
  state.api.mockRejectedValueOnce(new Error('Your current password is not right.'));
  render(<MyAccount />);
  fill('wrong-password', 'a brand new password', 'a brand new password');
  expect(await screen.findByText('Your current password is not right.')).toBeInTheDocument();
});
