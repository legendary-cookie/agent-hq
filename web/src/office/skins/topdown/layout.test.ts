import { expect, it } from 'vitest';
import type { Scene } from '../../scene';
import { layoutTopdown } from './layout';

const scene: Scene = {
  rooms: [
    ...Array.from({ length: 6 }, (_, i) => ({
      id: String(i),
      harness: String(i),
      label: String(i),
      capacity: 30,
    })),
    { id: 'break', harness: null, label: 'Break room', capacity: 0 },
  ],
  desks: Array.from({ length: 150 }, (_, i) => ({
    id: String(i),
    roomId: String(Math.floor(i / 25)),
    index: i % 25,
  })),
  npcs: [],
  walks: [],
};
const viewport = { width: 1200, height: 900 };
const rect = (shape: { x: number; y: number }[]) => ({
  left: Math.min(...shape.map((p) => p.x)),
  right: Math.max(...shape.map((p) => p.x)),
  top: Math.min(...shape.map((p) => p.y)),
  bottom: Math.max(...shape.map((p) => p.y)),
});

it('rooms do not overlap', () => {
  const rooms = layoutTopdown(scene, viewport).rooms;
  expect(rooms[rooms.length - 1]?.id).toBe('break');
  for (let i = 0; i < rooms.length; i++) {
    const a = rect(rooms[i].shape);
    expect(a.right - a.left).toBe(a.bottom - a.top);
    for (const room of rooms.slice(i + 1)) {
      const b = rect(room.shape);
      expect(a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top).toBe(
        true,
      );
    }
  }
});

it('desks inside their room', () => {
  const layout = layoutTopdown(scene, viewport);
  expect(new Set(layout.desks.map((desk) => `${desk.at.x},${desk.at.y}`)).size).toBe(
    scene.desks.length,
  );
  for (const desk of scene.desks) {
    const point = layout.desks.find((item) => item.id === desk.id)!.at;
    const bounds = rect(layout.rooms.find((room) => room.id === desk.roomId)!.shape);
    expect(point.x).toBeGreaterThan(bounds.left);
    expect(point.x).toBeLessThan(bounds.right);
    expect(point.y).toBeGreaterThan(bounds.top);
    expect(point.y).toBeLessThan(bounds.bottom);
    const seat = layout.seats.get(desk.id)!;
    expect(seat.y).toBeLessThan(bounds.bottom);
  }
});

it('layout fits viewport', () => {
  const layout = layoutTopdown(scene, viewport);
  for (const point of layout.rooms.flatMap((room) => room.shape)) {
    expect(point.x).toBeGreaterThanOrEqual(0);
    expect(point.y).toBeGreaterThanOrEqual(0);
    expect(point.x).toBeLessThanOrEqual(viewport.width);
    expect(point.y).toBeLessThanOrEqual(viewport.height);
    expect(Number.isInteger(point.x) && Number.isInteger(point.y)).toBe(true);
  }
});
