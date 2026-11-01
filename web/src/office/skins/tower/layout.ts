import { harnessLabel } from '../../colors';
import type { Scene } from '../../scene';
import type { Point, SkinLayout } from '../../skin';

export function layoutTower(scene: Scene, viewport: { width: number; height: number }) {
  const ordered = [
    ...scene.rooms.filter((r) => r.id !== 'break'),
    ...scene.rooms.filter((r) => r.id === 'break'),
  ];
  const idleCount = scene.npcs.filter((npc) => npc.targetRoomId === 'break').length;
  const heights = ordered.map((room) =>
    room.id === 'break'
      ? Math.max(100, Math.ceil(Math.max(8, idleCount) / 6) * 60 + 40)
      : Math.max(100, Math.ceil(Math.max(room.capacity, 1) / 6) * 60 + 40),
  );
  const rawWidth = 500;
  const rawHeight = heights.reduce((a, b) => a + b, 0);
  const scale = Math.max(
    0,
    Math.min(
      1.5,
      (viewport.width - 32) / rawWidth,
      (viewport.height - 32) / Math.max(rawHeight, 1),
    ),
  );
  const left = (viewport.width - rawWidth * scale) / 2;
  const top = (viewport.height - rawHeight * scale) / 2;
  const point = (x: number, y: number): Point => ({ x: left + x * scale, y: top + y * scale });
  let y = 0;
  const floors = ordered.map((room, i) => {
    const floor = { room, y, height: heights[i] };
    y += heights[i];
    return floor;
  });
  const rooms = floors.map(({ room, y: floorY, height }) => ({
    id: room.id,
    label: harnessLabel(room.harness ?? 'break'),
    labelAt: point(75, floorY + 16),
    shape: [
      point(0, floorY),
      point(450, floorY),
      point(450, floorY + height),
      point(0, floorY + height),
    ],
  }));
  const desks = scene.desks.flatMap((desk) => {
    const floor = floors.find((item) => item.room.id === desk.roomId);
    if (!floor) return [];
    const at = point(45 + (desk.index % 6) * 67, floor.y + 60 + Math.floor(desk.index / 6) * 60);
    return [{ id: desk.id, at, depth: at.y }];
  });
  const seats = new Map(
    desks.map((desk) => [desk.id, { x: desk.at.x, y: desk.at.y + 12 * scale }]),
  );
  const breakFloor = floors.find((floor) => floor.room.id === 'break');
  const breakSpots = breakFloor
    ? Array.from({ length: Math.max(8, idleCount) }, (_, i) =>
        point(45 + (i % 6) * 67, breakFloor.y + 72 + Math.floor(i / 6) * 60),
      )
    : [];
  return {
    rooms,
    desks,
    seats,
    breakSpots,
    bounds: { ...viewport },
    scale,
    liftX: point(475, 0).x,
    liftTop: top,
    liftBottom: top + rawHeight * scale,
  };
}

export function towerWalkPath(
  layout: SkinLayout & { liftX: number },
  fromRoomId: string,
  toRoomId: string,
): Point[] {
  const from = layout.rooms.find((room) => room.id === fromRoomId);
  const to = layout.rooms.find((room) => room.id === toRoomId);
  if (!from || !to) return [];
  const center = (room: typeof from): Point => ({
    x: (room.shape[0].x + room.shape[1].x) / 2,
    y: (room.shape[0].y + room.shape[2].y) / 2,
  });
  const start = center(from);
  const end = center(to);
  return [start, { x: layout.liftX, y: start.y }, { x: layout.liftX, y: end.y }, end];
}
