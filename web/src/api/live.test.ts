import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { subscribeChanges } from './live';
class MockSource {
  static instances: MockSource[] = [];
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners: Record<string, () => void> = {};
  constructor(public url: string) {
    MockSource.instances.push(this);
  }
  addEventListener(k: string, f: () => void) {
    this.listeners[k] = f;
  }
  close() {}
}
beforeEach(() => {
  vi.useFakeTimers();
  MockSource.instances = [];
  vi.stubGlobal('EventSource', MockSource);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
it('debounces change notices', () => {
  const cb = vi.fn();
  const stop = subscribeChanges(cb);
  const s = MockSource.instances[0];
  s.listeners.changed();
  s.listeners.changed();
  vi.advanceTimersByTime(999);
  expect(cb).not.toHaveBeenCalled();
  vi.advanceTimersByTime(1);
  expect(cb).toHaveBeenCalledTimes(1);
  stop();
});
it('reconnects SSE and refetches', () => {
  const cb = vi.fn();
  const stop = subscribeChanges(cb);
  MockSource.instances[0].onerror!();
  vi.advanceTimersByTime(1000);
  expect(MockSource.instances).toHaveLength(2);
  MockSource.instances[1].onopen!();
  expect(cb).toHaveBeenCalledTimes(1);
  stop();
});
it('multiple subscribers share one EventSource', () => {
  const first = vi.fn();
  const second = vi.fn();
  const stopFirst = subscribeChanges(first);
  const stopSecond = subscribeChanges(second);
  expect(MockSource.instances).toHaveLength(1);
  const source = MockSource.instances[0];
  const close = vi.spyOn(source, 'close');
  source.listeners.changed();
  vi.advanceTimersByTime(1000);
  expect(first).toHaveBeenCalledTimes(1);
  expect(second).toHaveBeenCalledTimes(1);
  stopFirst();
  expect(close).not.toHaveBeenCalled();
  stopSecond();
  expect(close).toHaveBeenCalledTimes(1);
});

it('SSE failures probe health and surface 403', async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: false, status: 403, json: async () => ({}) });
  vi.stubGlobal('fetch', fetcher);
  const forbidden = vi.fn();
  window.addEventListener('hq-forbidden', forbidden);
  const stop = subscribeChanges(() => {});
  MockSource.instances[0].onerror!();
  vi.advanceTimersByTime(1000);
  MockSource.instances[1].onerror!();
  vi.advanceTimersByTime(2000);
  MockSource.instances[2].onerror!();
  await vi.waitFor(() => expect(forbidden).toHaveBeenCalledTimes(1));
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0][0]).toBe('/api/health');
  window.removeEventListener('hq-forbidden', forbidden);
  stop();
});
