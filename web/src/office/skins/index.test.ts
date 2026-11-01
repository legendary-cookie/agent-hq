import { expect, it } from 'vitest';
import { parseSkin, SKINS } from './index';
it('parseSkin falls back for unknown values', () => {
  expect(parseSkin('bad', 'tower')).toBe('tower');
  expect(parseSkin('topdown', 'tower')).toBe('topdown');
});
it('SKINS has three skins', () =>
  expect(Object.keys(SKINS)).toEqual(['isometric', 'topdown', 'tower']));
