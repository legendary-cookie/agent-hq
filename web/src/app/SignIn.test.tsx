import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { SignIn } from './SignIn';

function respond(login: { status: number; body: object }) {
  const fetchMock = vi.fn(async (path: string) =>
    path === '/api/brand'
      ? { ok: true, status: 200, json: async () => ({ brand: 'Example Co HQ' }) }
      : { ok: login.status === 200, status: login.status, json: async () => login.body },
  );
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

async function submit(password = 'a long password') {
  render(<SignIn />);
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: password } });
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
}

beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('still explains the start-up link for this computer', () => {
  respond({ status: 401, body: {} });
  render(<SignIn />);
  expect(screen.getByText(/open it from the link printed when it starts/)).toBeInTheDocument();
});

it('shows the app name from fleet.json', async () => {
  respond({ status: 401, body: {} });
  render(<SignIn />);
  expect(await screen.findByRole('heading', { name: 'Sign in to Example Co HQ' })).toBeInTheDocument();
});

it('cannot submit an empty password', () => {
  const fetchMock = respond({ status: 200, body: {} });
  render(<SignIn />);
  expect(screen.getByRole('button', { name: 'Sign in' })).toBeDisabled();
  expect(fetchMock).not.toHaveBeenCalledWith('/api/login', expect.anything());
});

it('wrong password shows the message, clears the field and stores nothing', async () => {
  respond({ status: 401, body: { error: 'That password is not right.' } });
  await submit();
  expect(await screen.findByRole('alert')).toHaveTextContent('That password is not right.');
  expect(screen.getByLabelText('Password')).toHaveValue('');
  expect(localStorage.getItem('hq-token')).toBeNull();
});

it('lockout says how long to wait', async () => {
  respond({ status: 429, body: { error: 'Too many attempts.', retry_after: 61 } });
  await submit();
  expect(await screen.findByRole('alert')).toHaveTextContent('Try again in 2 min.');
});

it('a working password stores the session', async () => {
  respond({ status: 200, body: { token: 'session', run_key: 'key' } });
  vi.stubGlobal('location', { hash: '', reload: vi.fn() });
  await submit();
  await waitFor(() => expect(localStorage.getItem('hq-token')).toBe('session'));
  expect(localStorage.getItem('hq-run-key')).toBe('key');
});

it('a network failure is explained', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('offline'); }));
  await submit();
  expect(await screen.findByRole('alert')).toHaveTextContent('Can’t reach the server');
});
