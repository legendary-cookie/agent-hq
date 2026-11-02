import { expect, it, vi } from 'vitest';
import { bootstrapToken } from './token';
it('stores token from hash and rewrites hash', () => {
  sessionStorage.clear();
  history.replaceState(null, '', '/#token=abc');
  expect(bootstrapToken(location, sessionStorage)).toBe('abc');
  expect(sessionStorage.getItem('hq-token')).toBe('abc');
  expect(location.hash).toBe('#/office');
});
it('falls back to stored token', () => {
  sessionStorage.setItem('hq-token', 'saved');
  history.replaceState(null, '', '/');
  expect(bootstrapToken(location, sessionStorage)).toBe('saved');
});

it('bootstrap exchanges code for token', async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue({ ok: true, json: async () => ({ token: 'exchanged' }) });
  vi.stubGlobal('fetch', fetcher);
  sessionStorage.clear();
  history.replaceState(null, '', '/#code=once');
  expect(await bootstrapToken(location, sessionStorage)).toBe('exchanged');
  expect(fetcher).toHaveBeenCalledWith(
    '/api/exchange',
    expect.objectContaining({ method: 'POST', body: JSON.stringify({ code: 'once' }) }),
  );
  expect(sessionStorage.getItem('hq-token')).toBe('exchanged');
  expect(location.hash).toBe('#/office');
  vi.unstubAllGlobals();
});

it('failed exchange keeps the existing token', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, json: async () => ({}) }));
  sessionStorage.setItem('hq-token', 'working');
  history.replaceState(null, '', '/#code=spent');
  expect(await bootstrapToken(location, sessionStorage)).toBe('working');
  expect(sessionStorage.getItem('hq-token')).toBe('working');
  vi.unstubAllGlobals();
});
