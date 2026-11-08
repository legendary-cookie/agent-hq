import { afterEach, expect, it, vi } from 'vitest';
import { render, screen, waitFor, cleanup } from '@testing-library/react';
import { createElement } from 'react';
import { api, ApiError } from './client';
import { App } from '../app/App';
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it('sends token header', async () => {
  localStorage.setItem('hq-token', 'abc');
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
  vi.stubGlobal('fetch', fetcher);
  await api('/api/health');
  expect(new Headers(fetcher.mock.calls[0][1].headers).get('X-HQ-Token')).toBe('abc');
});
it('throws ApiError with status', async () => {
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockResolvedValue({ ok: false, status: 403, json: async () => ({ error: 'forbidden' }) }),
  );
  await expect(api('/api/health')).rejects.toMatchObject({ status: 403 });
  expect(new ApiError(403)).toBeInstanceOf(Error);
});
it('shows token help on 403', async () => {
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockResolvedValue({ ok: false, status: 403, json: async () => ({ error: 'forbidden' }) }),
  );
  vi.stubGlobal(
    'EventSource',
    class {
      addEventListener() {}
      close() {}
    },
  );
  render(createElement(App));
  await waitFor(() => expect(screen.getByLabelText('Password')).toBeInTheDocument());
});
it('a 403 naming a permission keeps you signed in and carries the reason', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({ ok: false, status: 403, json: async () => ({ error: 'You don’t have permission to change accounts.', permission: 'accounts.manage' }) }),
  );
  const forbidden = vi.fn();
  window.addEventListener('hq-forbidden', forbidden);
  await expect(api('/api/accounts', { method: 'POST', body: '{}' })).rejects.toMatchObject({ status: 403, permission: 'accounts.manage', message: 'You don’t have permission to change accounts.' });
  window.removeEventListener('hq-forbidden', forbidden);
  expect(forbidden).not.toHaveBeenCalled();
});
it('a page the role cannot view says so instead of showing it', async () => {
  const { resetMe } = await import('./me');
  resetMe();
  localStorage.setItem('hq-token', 'viewer-token');
  location.hash = '#/router';
  vi.stubGlobal('EventSource', class { addEventListener() {} close() {} });
  vi.stubGlobal('fetch', vi.fn(async (path: string) => {
    if (path === '/api/me') return { ok: true, status: 200, json: async () => ({ username: 'sam', name: 'Sam', role: 'viewer', role_label: 'Viewer', permissions: ['view.overview'], admin: false }) };
    if (path === '/api/router') return { ok: false, status: 403, json: async () => ({ error: 'You don’t have permission to view the router.', permission: 'view.router' }) };
    return { ok: true, status: 200, json: async () => (path === '/api/limits' ? [] : path === '/api/alerts' ? { alerts: [] } : {}) };
  }));
  render(createElement(App));
  expect(await screen.findByText(/You don’t have access to this page/)).toBeInTheDocument();
  expect(screen.queryByLabelText('Password')).toBeNull();
  location.hash = '';
  resetMe();
});
it('useApi clears data when path changes', async () => {
  const { renderHook } = await import('@testing-library/react');
  const { useApi } = await import('./client');
  vi.stubGlobal(
    'EventSource',
    class {
      addEventListener() {}
      close() {}
    },
  );
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockImplementation((path: string) =>
        path === '/a'
          ? Promise.resolve({ ok: true, json: async () => ({ name: 'a' }) })
          : new Promise(() => {}),
      ),
  );
  const { result, rerender } = renderHook(
    ({ path }: { path: string | null }) =>
      useApi<{
        name: string;
      }>(path),
    { initialProps: { path: '/a' as string | null } },
  );
  await waitFor(() => expect(result.current.data?.name).toBe('a'));
  rerender({ path: '/b' });
  expect(result.current.data).toBeNull();
  rerender({ path: null });
  expect(result.current.data).toBeNull();
  expect(result.current.loading).toBe(false);
});
it('concurrent identical requests share one fetch', async () => {
  let resolve!: (value: unknown) => void;
  const fetcher = vi.fn().mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  vi.stubGlobal('fetch', fetcher);
  const first = api('/shared');
  const second = api('/shared');
  expect(fetcher).toHaveBeenCalledTimes(1);
  resolve({ ok: true, json: async () => ({ ok: true }) });
  expect(await Promise.all([first, second])).toEqual([{ ok: true }, { ok: true }]);
  await api('/shared');
  expect(fetcher).toHaveBeenCalledTimes(2);
});
it('live false does not refetch on change', async () => {
  const { renderHook, act } = await import('@testing-library/react');
  const { useApi } = await import('./client');
  let changed: (() => void) | undefined;
  vi.stubGlobal(
    'EventSource',
    class {
      addEventListener(name: string, callback: () => void) {
        if (name === 'changed') changed = callback;
      }
      close() {}
    },
  );
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
  vi.stubGlobal('fetch', fetcher);
  renderHook(() => useApi('/settings-test', { live: false }));
  renderHook(() => useApi('/live-test'));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  act(() => changed?.());
  await new Promise((resolve) => setTimeout(resolve, 1100));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3));
  expect(fetcher.mock.calls.filter(([path]) => path === '/settings-test')).toHaveLength(1);
});
it('refresh after change does not reuse in-flight request', async () => {
  const { renderHook, act } = await import('@testing-library/react');
  const { useApi } = await import('./client');
  let changed!: () => void;
  vi.stubGlobal(
    'EventSource',
    class {
      addEventListener(_: string, cb: () => void) {
        changed = cb;
      }
      close() {}
    },
  );
  const fetcher = vi.fn().mockImplementation(() => new Promise(() => {}));
  vi.stubGlobal('fetch', fetcher);
  renderHook(() => useApi('/pending-change'));
  expect(fetcher).toHaveBeenCalledTimes(1);
  act(() => changed());
  await new Promise((resolve) => setTimeout(resolve, 1100));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
});
