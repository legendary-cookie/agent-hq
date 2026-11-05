import { expect, it } from 'vitest';
import { loadLayout, saveLayout, resetLayout } from './layout-editor';
it('layout edit persists and resets', () => {
  saveLayout('topdown', { desks: { a: {x: 1,y: 2} }, rooms: {} });
  expect(loadLayout('topdown').desks.a).toEqual({x:1,y:2});
  resetLayout('topdown');
  expect(loadLayout('topdown').desks).toEqual({});
});
