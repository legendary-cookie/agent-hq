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
