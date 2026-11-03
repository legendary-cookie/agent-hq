import { describe, expect, it } from 'vitest';
import { spring } from './spring';

describe('spring', () => {
  it('critically damped settles without overshoot', () => {
    const step = spring(0, 1, 0, {});
    let result = step(0);
    for (let i = 0; i < 300; i++) {
      result = step(0.016);
      expect(result.value).toBeLessThanOrEqual(1);
    }
    expect(result.done).toBe(true);
  });
  it('interrupt keeps current value', () => {
    const first = spring(0, 1, 0, {})(0.1);
    expect(spring(first.value, 0, first.velocity, {})(0).value).toBe(first.value);
  });
});
