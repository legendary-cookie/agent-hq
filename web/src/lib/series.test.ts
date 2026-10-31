import { describe, expect, it } from 'vitest';
import { continuousBuckets } from './series';

describe('continuousBuckets', () => {
  it('fills missing hours between first and last', () => {
    const start = new Date(2026, 9, 31, 0).getTime() / 1000;
    const result = continuousBuckets([start, start + 3 * 3600], 'hour');
    expect(result).toEqual([start, start + 3600, start + 7200, start + 10800]);
  });

  it('steps days by local midnight', () => {
    const first = new Date(2026, 9, 29).getTime() / 1000;
    const last = new Date(2026, 9, 31).getTime() / 1000;
    const result = continuousBuckets([last, first], 'day');
    expect(result.map((t) => new Date(t * 1000).getDate())).toEqual([29, 30, 31]);
    expect(result.every((t) => new Date(t * 1000).getHours() === 0)).toBe(true);
  });

  it('returns empty for no data', () => {
    expect(continuousBuckets([], 'day')).toEqual([]);
  });
});
