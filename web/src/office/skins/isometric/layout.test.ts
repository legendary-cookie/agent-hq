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
it('zones form one floor', () => {
  const layout = layoutIsometric(scene, { width: 2400, height: 1800 });
  expect(layout.floorShape).toHaveLength(4);
  expect(layout.tiles.length).toBe(layout.floorGrid.width * layout.floorGrid.height);
  for (const room of layout.rooms) {
    expect(
      room.grid.x === 0 ||
        room.grid.y === 0 ||
        layout.rooms.some(
          (other) =>
            other !== room &&
            ((Math.abs(room.grid.x - (other.grid.x + other.grid.width)) <= 1 &&
              room.grid.y < other.grid.y + other.grid.height &&
              other.grid.y < room.grid.y + room.grid.height) ||
              (Math.abs(room.grid.y - (other.grid.y + other.grid.height)) <= 1 &&
                room.grid.x < other.grid.x + other.grid.width &&
                other.grid.x < room.grid.x + room.grid.width)),
        ),
    ).toBe(true);
  }
});
it('floor is centred', () => {
  const viewport = { width: 1200, height: 800 };
  const points = layoutIsometric(scene, viewport).floorShape;
  expect(
    (Math.min(...points.map((p) => p.x)) + Math.max(...points.map((p) => p.x))) / 2,
  ).toBeCloseTo(viewport.width / 2, 0);
  expect(
    (Math.min(...points.map((p) => p.y)) + Math.max(...points.map((p) => p.y))) / 2,
  ).toBeCloseTo(viewport.height / 2, 0);
});
it('break spots do not overlap', () => {
  const layout = layoutIsometric(scene, { width: 1600, height: 1200 });
  for (let i = 0; i < layout.breakSpots.length; i++) {
    for (const other of layout.breakSpots.slice(i + 1)) {
      expect(
        Math.hypot(layout.breakSpots[i].x - other.x, layout.breakSpots[i].y - other.y) /
          layout.scale,
      ).toBeGreaterThanOrEqual(28);
    }
  }
});
