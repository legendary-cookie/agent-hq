import { harnessLabel } from '../../colors';
import type { Scene } from '../../scene';
import type { Point } from '../../skin';

export const TILE_W = 64;
export const TILE_H = 32;
export function isoToScreen(gx: number, gy: number, origin: Point): Point {
  return { x: origin.x + ((gx - gy) * TILE_W) / 2, y: origin.y + ((gx + gy) * TILE_H) / 2 };
}
export function layoutIsometric(scene: Scene, viewport: { width: number; height: number }) {
  const idleCount = scene.npcs.filter((npc) => npc.targetRoomId === 'break').length;
  const roomsOrdered = [
    ...scene.rooms.filter((room) => room.id !== 'break'),
    ...scene.rooms.filter((room) => room.id === 'break'),
  ];
  const heights = roomsOrdered.map((room) =>
    room.id === 'break'
      ? Math.max(2, Math.ceil(idleCount / 4))
      : Math.max(2, Math.ceil(room.capacity / 6) * 2),
  );
  const rowHeights = Array.from({ length: Math.ceil(roomsOrdered.length / 2) }, (_, i) =>
    Math.max(heights[i * 2] ?? 0, heights[i * 2 + 1] ?? 0),
  );
  const zones = roomsOrdered.map((room, i) => ({
    room,
    x: (i % 2) * 7,
    y: rowHeights.slice(0, Math.floor(i / 2)).reduce((a, b) => a + b + 1, 0),
    rows: heights[i],
  }));
  const width = roomsOrdered.length > 1 ? 13 : roomsOrdered.length ? 6 : 0;
  const height = rowHeights.reduce((a, b) => a + b, 0) + Math.max(0, rowHeights.length - 1);
  const raw = [
    [0, 0],
    [width, 0],
    [width, height],
    [0, height],
  ].map(([x, y]) => isoToScreen(x, y, { x: 0, y: 0 }));
  const minX = Math.min(...raw.map((p) => p.x));
  const maxX = Math.max(...raw.map((p) => p.x));
  const minY = Math.min(...raw.map((p) => p.y));
  const maxY = Math.max(...raw.map((p) => p.y));
  const scale =
    width && height
      ? Math.max(
          0.5,
          Math.min(
            1.6,
            (viewport.width * 0.9) / (maxX - minX),
            (viewport.height * 0.9) / (maxY - minY),
          ),
        )
      : 1;
  const point = (gx: number, gy: number) => {
    const p = isoToScreen(gx, gy, { x: 0, y: 0 });
    return {
      x: (p.x - (minX + maxX) / 2) * scale + viewport.width / 2,
      y: (p.y - (minY + maxY) / 2) * scale + viewport.height / 2,
    };
  };
  const floorShape = width
    ? [point(0, 0), point(width, 0), point(width, height), point(0, height)]
    : [];
  const tiles = Array.from({ length: width * height }, (_, i) => {
    const gx = i % width;
    const gy = Math.floor(i / width);
    return {
      shape: [point(gx, gy), point(gx + 1, gy), point(gx + 1, gy + 1), point(gx, gy + 1)],
      alternate: (gx + gy) % 2 === 0,
    };
  });
  const rooms = zones.map(({ room, x, y, rows }) => ({
    id: room.id,
    label: `${harnessLabel(room.harness ?? 'break')} · ${
      room.id === 'break'
        ? idleCount
        : scene.npcs.filter((npc) => npc.harness === room.harness).length
    }`,
    harness: room.harness,
    labelAt: point(x + 3, y),
    shape: [point(x, y), point(x + 6, y), point(x + 6, y + rows), point(x, y + rows)],
    grid: { x, y, width: 6, height: rows },
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
    ? Array.from({ length: idleCount }, (_, i) =>
        point(zone.x + 0.7 + (i % 4) * 1.1, zone.y + 0.5 + Math.floor(i / 4)),
      )
    : [];
  const furniture = zone
    ? { sofa: point(zone.x + 5.3, zone.y + 0.5), table: point(zone.x + 5.3, zone.y + 1.5) }
    : null;
  return {
    rooms,
    desks,
    seats,
    breakSpots,
    bounds: { width: viewport.width, height: viewport.height },
    tiles,
    floorShape,
    floorGrid: { width, height },
    scale,
    furniture,
  };
}
