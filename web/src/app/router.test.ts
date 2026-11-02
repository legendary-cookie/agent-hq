import { expect, it } from 'vitest';
import { parseRoute } from './router';
it('unknown route is office', () => {
  expect(parseRoute('#/other')).toBe('office');
});
