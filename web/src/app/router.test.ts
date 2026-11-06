import { expect, it } from 'vitest';
import { parseRoute } from './router';
it('unknown route is overview', () => {
  expect(parseRoute('#/other')).toBe('overview');
  expect(parseRoute('')).toBe('overview');
});
it('every page has an address', () => {
  for (const route of ['agents', 'usage', 'insights', 'sessions', 'office', 'office-3d', 'dashboard', 'limits', 'runs', 'goals', 'settings']) {
    expect(parseRoute(`#/${route}`)).toBe(route);
  }
});
it('the inbox still opens, including from a link to one approval', () => {
  expect(parseRoute('#/inbox')).toBe('office');
  expect(parseRoute('#/inbox?approval=a1')).toBe('office');
});
