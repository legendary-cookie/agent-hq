import { describe, expect, it } from 'vitest';
import { makeSyntheticSessions, syntheticCount } from './synthetic';

describe('synthetic sessions (dev-only performance check)', () => {
  it('makes n recent sessions spread over harnesses and states', () => {
    const sessions = makeSyntheticSessions(60, 1000);
    expect(sessions).toHaveLength(60);
    expect(new Set(sessions.map((s) => s.harness)).size).toBeGreaterThanOrEqual(5);
    expect(new Set(sessions.map((s) => s.status))).toEqual(
      new Set(['working', 'waiting', 'idle', 'error']),
    );
    expect(sessions.every((s) => s.last_event_at === 1000)).toBe(true);
  });

  it('reads a bounded count from the query string', () => {
    expect(syntheticCount('?synthetic=60')).toBe(60);
    expect(syntheticCount('?synthetic=99999')).toBe(500);
    expect(syntheticCount('?synthetic=abc')).toBe(0);
    expect(syntheticCount('')).toBe(0);
  });
});
