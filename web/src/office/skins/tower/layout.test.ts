import { expect, it } from 'vitest';
import type { Scene } from '../../scene';
import { layoutTower, towerWalkPath } from './layout';

const scene: Scene = {
  rooms: [
    { id: 'codex', label: 'codex', harness: 'codex', capacity: 30 },
    { id: 'claude_code', label: 'claude_code', harness: 'claude_code', capacity: 6 },
    { id: 'break', label: 'Break room', harness: null, capacity: 0 },
  ],
  desks: Array.from({ length: 31 }, (_, i) => ({
    id: String(i),
    roomId: i < 25 ? 'codex' : 'claude_code',
    index: i < 25 ? i : i - 25,
  })),
  npcs: [],
  walks: [],
};
const viewport = { width: 800, height: 500 };
const limits = (shape: { x: number; y: number }[]) => ({
  left: Math.min(...shape.map((p) => p.x)),
  right: Math.max(...shape.map((p) => p.x)),
  top: Math.min(...shape.map((p) => p.y)),
  bottom: Math.max(...shape.map((p) => p.y)),
});
it('rooms do not overlap', () => {
  const rooms = layoutTower(scene, viewport).rooms.map((room) => limits(room.shape));
  for (let i = 1; i < rooms.length; i++)
    expect(rooms[i].top).toBeGreaterThanOrEqual(rooms[i - 1].bottom);
});
it('desks inside their room', () => {
  const layout = layoutTower(scene, viewport);
  for (const desk of layout.desks) {
    const source = scene.desks.find((item) => item.id === desk.id)!;
    const room = limits(layout.rooms.find((item) => item.id === source.roomId)!.shape);
    expect(desk.at.x).toBeGreaterThan(room.left);
    expect(desk.at.x).toBeLessThan(room.right);
    expect(desk.at.y).toBeGreaterThan(room.top);
    expect(desk.at.y).toBeLessThan(room.bottom);
  }
  expect(new Set(layout.desks.map((desk) => `${desk.at.x},${desk.at.y}`)).size).toBe(31);
});
it('layout fits viewport', () => {
  const layout = layoutTower(scene, viewport);
  for (const point of layout.rooms.flatMap((room) => room.shape)) {
    expect(point.x).toBeGreaterThanOrEqual(0);
    expect(point.y).toBeGreaterThanOrEqual(0);
    expect(point.x).toBeLessThanOrEqual(viewport.width);
    expect(point.y).toBeLessThanOrEqual(viewport.height);
  }
});
it('tower walk path goes through lift', () => {
  const layout = layoutTower(scene, viewport);
  const path = towerWalkPath(layout, 'codex', 'claude_code');
  expect(path).toHaveLength(4);
  expect(path[1].x).toBe(path[2].x);
  expect(path[1].x).toBeGreaterThan(path[0].x);
  expect(path[2].x).toBeGreaterThan(path[3].x);
  expect(path[1].y).toBe(path[0].y);
  expect(path[2].y).toBe(path[3].y);
});
