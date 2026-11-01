import { expect, it } from 'vitest';
import { approach, bob } from './motion';
it('approach never overshoots', () => {
  expect(approach({ x: 0, y: 0 }, { x: 3, y: 4 }, 10, 1)).toEqual({ x: 3, y: 4 });
});
it('bob stays within 2 px', () => {
  for (let t = 0; t < 100; t++) expect(Math.abs(bob(t, 1))).toBeLessThanOrEqual(2);
});
