import type { Scene } from '../../scene';
import type { Point, SkinLayout } from '../../skin';

export const TILE_W = 64;
export const TILE_H = 32;
export function isoToScreen(gx: number, gy: number, origin: Point): Point {
  return { x: origin.x + ((gx - gy) * TILE_W) / 2, y: origin.y + ((gx + gy) * TILE_H) / 2 };
}
export function layoutIsometric(
  scene: Scene,
  viewport: { width: number; height: number },
): SkinLayout & { tiles: { shape: Point[]; alternate: boolean }[] } {
  const heights = scene.rooms.map((room) =>
    room.id === 'break' ? 4 : Math.max(2, Math.ceil(room.capacity / 6) * 2),
  );
  const rowHeights = Array.from({ length: Math.ceil(scene.rooms.length / 2) }, (_, i) =>
    Math.max(heights[i * 2] ?? 0, heights[i * 2 + 1] ?? 0),
  );
  const zones = scene.rooms.map((room, i) => ({
    room,
    x: (i % 2) * 8,
    y: rowHeights.slice(0, Math.floor(i / 2)).reduce((a, b) => a + b + 2, 0),
    rows: heights[i],
  }));
  const raw = zones.flatMap(({ x, y, rows }) =>
    [
      [x, y],
      [x + 6, y],
      [x + 6, y + rows],
      [x, y + rows],
    ].map(([gx, gy]) => isoToScreen(gx, gy, { x: 0, y: 0 })),
  );
  if (!raw.length)
    return { rooms: [], desks: [], seats: new Map(), breakSpots: [], bounds: viewport, tiles: [] };
  const minX = Math.min(...raw.map((p) => p.x));
  const maxX = Math.max(...raw.map((p) => p.x));
  const minY = Math.min(...raw.map((p) => p.y));
  const maxY = Math.max(...raw.map((p) => p.y));
  const scale = Math.max(
    0.5,
    Math.min(1.5, (viewport.width - 48) / (maxX - minX), (viewport.height - 48) / (maxY - minY)),
  );
  const point = (gx: number, gy: number) => {
    const p = isoToScreen(gx, gy, { x: 0, y: 0 });
    return {
      x: (p.x - (minX + maxX) / 2) * scale + viewport.width / 2,
      y: (p.y - (minY + maxY) / 2) * scale + viewport.height / 2,
    };
  };
  const tiles = zones.flatMap(({ x, y, rows }) =>
    Array.from({ length: 6 * rows }, (_, i) => {
      const gx = x + (i % 6);
      const gy = y + Math.floor(i / 6);
      return {
        shape: [point(gx, gy), point(gx + 1, gy), point(gx + 1, gy + 1), point(gx, gy + 1)],
        alternate: (gx + gy) % 2 === 0,
      };
    }),
  );
  const rooms = zones.map(({ room, x, y, rows }) => ({
    id: room.id,
    label: room.label,
    labelAt: point(x + 0.5, y + 0.5),
    shape: [point(x, y), point(x + 6, y), point(x + 6, y + rows), point(x, y + rows)],
  }));
  const desks = scene.desks.flatMap((desk) => {
    const zone = zones.find((z) => z.room.id === desk.roomId);
    if (!zone) return [];
    const gx = zone.x + (desk.index % 6) + 0.5;
    const gy = zone.y + Math.floor(desk.index / 6) * 2 + 1;
    return [{ id: desk.id, at: point(gx, gy), depth: gx + gy }];
  });
  const seats = new Map(
    desks.map((desk) => [desk.id, { x: desk.at.x, y: desk.at.y + 12 * scale }]),
  );
  const zone = zones.find((z) => z.room.id === 'break');
  const breakSpots = zone
    ? Array.from({ length: 8 }, (_, i) =>
        point(zone.x + 1 + (i % 4), zone.y + 1 + Math.floor(i / 4) * 1.5),
      )
    : [];
  return {
    rooms,
    desks,
    seats,
    breakSpots,
    bounds: { width: viewport.width, height: viewport.height },
    tiles,
  };
}
