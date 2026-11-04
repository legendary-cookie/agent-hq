import { expect, it } from 'vitest';
import { approach, bob, readTheme } from './motion';
it('approach never overshoots', () => {
  expect(approach({ x: 0, y: 0 }, { x: 3, y: 4 }, 10, 1)).toEqual({ x: 3, y: 4 });
});
it('bob stays within 2 px', () => {
  for (let t = 0; t < 100; t++) expect(Math.abs(bob(t, 1))).toBeLessThanOrEqual(2);
});
it('readTheme reads office variables', () => {
  const el = document.createElement('div');
  el.style.setProperty('--office-floor', '#111111');
  el.style.setProperty('--office-floor-alt', '#222222');
  el.style.setProperty('--office-wall', '#333333');
  el.style.setProperty('--office-label-bg', '#444444');
  el.style.setProperty('--office-label-text', '#555555');
  document.body.appendChild(el);
  expect(readTheme(el)).toMatchObject({
    floor: '#111111',
    floorAlt: '#222222',
    wall: '#333333',
    labelBg: '#444444',
    labelText: '#555555',
  });
  el.remove();
});

it('reduced motion uses fades', async () => {
  const { motionFrame } = await import('./motion');
  expect(motionFrame({x: 0,y: 0}, {x: 10,y: 0}, 0.25, true)).toEqual({at:{x:10,y:0}, alpha:0.5});
});
