import { expect, it } from 'vitest';
import { pickNpc } from './pick';
it('pick prefers front-most NPC', () =>
  expect(
    pickNpc(
      [
        { id: 'front', at: { x: 0, y: 0 }, depth: 2 },
        { id: 'back', at: { x: 0, y: 0 }, depth: 1 },
      ],
      0,
      -10,
    ),
  ).toBe('front'));
it('pick returns null on empty floor', () =>
  expect(pickNpc([{ id: 'a', at: { x: 0, y: 0 }, depth: 1 }], 100, 100)).toBeNull());
it('hit area scales with sprite', async () => {
  const { npcHitArea } = await import('./pick');
  expect(npcHitArea({ x: 10, y: 20 }, 0.5)).toEqual({ x: 4, y: 0, w: 12, h: 20 });
});
it('close rows at small scale pick the upper npc when clicking its head', () => {
  expect(
    pickNpc(
      [
        { id: 'upper', at: { x: 0, y: 20 }, depth: 20, scale: 0.5 },
        { id: 'lower', at: { x: 0, y: 45 }, depth: 45, scale: 0.5 },
      ],
      0,
      5,
    ),
  ).toBe('upper');
});
