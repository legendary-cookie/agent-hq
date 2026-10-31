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
  sessionStorage.setItem('hq-token', 'abc');
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
  await waitFor(() => expect(screen.getByText(/Open Agent HQ from the link/)).toBeInTheDocument());
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
    ({ path }: { path: string | null }) => useApi<{ name: string }>(path),
    { initialProps: { path: '/a' as string | null } },
  );
  await waitFor(() => expect(result.current.data?.name).toBe('a'));
  rerender({ path: '/b' });
  expect(result.current.data).toBeNull();
  rerender({ path: null });
  expect(result.current.data).toBeNull();
  expect(result.current.loading).toBe(false);
});
