import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { SignIn } from './SignIn';

function respond(login: { status: number; body: object }) {
  const fetchMock = vi.fn(async (path: string, _init?: RequestInit) =>
    path === '/api/brand'
      ? { ok: true, status: 200, json: async () => ({ brand: 'Acme HQ' }) }
      : { ok: login.status === 200, status: login.status, json: async () => login.body },
  );
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

async function submit(password = 'a long password', username = 'boss') {
  render(<SignIn />);
  fireEvent.change(screen.getByLabelText('Username'), { target: { value: username } });
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: password } });
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
}

beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('shows the app name', async () => {
  respond({ status: 401, body: {} });
  render(<SignIn />);
  expect(await screen.findByRole('heading', { name: 'Sign in to Acme HQ' })).toBeInTheDocument();
});

it('cannot submit an empty password or username', () => {
  const fetchMock = respond({ status: 200, body: {} });
  render(<SignIn />);
  expect(screen.getByRole('button', { name: 'Sign in' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'a long password' } });
  expect(screen.getByRole('button', { name: 'Sign in' })).toBeDisabled();
  expect(fetchMock).not.toHaveBeenCalledWith('/api/login', expect.anything());
});

it('the username field comes before the password and is filled in by password managers', () => {
  respond({ status: 401, body: {} });
  render(<SignIn />);
  const username = screen.getByLabelText('Username');
  expect(username).toHaveAttribute('autocomplete', 'username');
  expect(username.compareDocumentPosition(screen.getByLabelText('Password')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

it('sends the username with the password', async () => {
  const fetchMock = respond({ status: 401, body: { error: 'Wrong username or password.' } });
  await submit('a long password', ' Sam ');
  await screen.findByRole('alert');
  const call = fetchMock.mock.calls.find(([path]) => path === '/api/login')!;
  expect(JSON.parse(String(call[1]!.body))).toEqual({ username: 'sam', password: 'a long password' });
});

it('wrong password shows the message, clears the field and stores nothing', async () => {
  respond({ status: 401, body: { error: 'That password is not right.' } });
  await submit();
  expect(await screen.findByRole('alert')).toHaveTextContent('That password is not right.');
  expect(screen.getByLabelText('Password')).toHaveValue('');
  expect(screen.getByLabelText('Username')).toHaveValue('boss');
  expect(localStorage.getItem('hq-token')).toBeNull();
});

it('lockout says how long to wait', async () => {
  respond({ status: 429, body: { error: 'Too many attempts.', retry_after: 61 } });
  await submit();
  expect(await screen.findByRole('alert')).toHaveTextContent('Try again in 2 min.');
});

it('a working password stores the session and remembers the username', async () => {
  respond({ status: 200, body: { token: 'session', run_key: 'key' } });
  vi.stubGlobal('location', { hash: '', reload: vi.fn() });
  await submit();
  await waitFor(() => expect(localStorage.getItem('hq-token')).toBe('session'));
  expect(localStorage.getItem('hq-run-key')).toBe('key');
  expect(localStorage.getItem('hq-last-user')).toBe('boss');
});

it('a network failure is explained', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('offline'); }));
  await submit();
  expect(await screen.findByRole('alert')).toHaveTextContent('Can’t reach the server');
});
