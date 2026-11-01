import { expect, it } from 'vitest';
import { isoToScreen, layoutIsometric } from './layout';
import type { Scene } from '../../scene';

const scene: Scene = {
  rooms: [
    ...Array.from({ length: 6 }, (_, i) => ({
      id: String(i),
      label: String(i),
      harness: String(i),
      capacity: 30,
    })),
    { id: 'break', label: 'Break room', harness: null, capacity: 0 },
  ],
  desks: Array.from({ length: 150 }, (_, i) => ({
    id: String(i),
    roomId: String(Math.floor(i / 25)),
    index: i % 25,
  })),
  npcs: [],
  walks: [],
};
it('isoToScreen matches formula', () =>
  expect(isoToScreen(2, 1, { x: 10, y: 20 })).toEqual({ x: 42, y: 68 }));
it('zones do not overlap', () => {
  const rooms = layoutIsometric(scene, { width: 2400, height: 1800 }).rooms;
  expect(new Set(rooms.map((r) => JSON.stringify(r.shape))).size).toBe(rooms.length);
});
it('isometric layout has no overlapping desks', () => {
  const desks = layoutIsometric(scene, { width: 2400, height: 1800 }).desks;
  expect(new Set(desks.map((d) => `${d.at.x},${d.at.y}`)).size).toBe(150);
});
it('layout fits viewport', () => {
  const layout = layoutIsometric(scene, { width: 2400, height: 1800 });
  for (const p of layout.rooms.flatMap((r) => r.shape)) {
    expect(p.x).toBeGreaterThanOrEqual(24);
    expect(p.y).toBeGreaterThanOrEqual(24);
    expect(p.x).toBeLessThanOrEqual(2376);
    expect(p.y).toBeLessThanOrEqual(1776);
  }
});
