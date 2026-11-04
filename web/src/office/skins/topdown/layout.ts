import { harnessLabel } from '../../colors';
import type { Scene } from '../../scene';
import type { Point, SkinLayout } from '../../skin';

export function layoutTopdown(
  scene: Scene,
  viewport: { width: number; height: number },
): SkinLayout & {
  scale: number;
  coffeeTable: Point | null;
} {
  const ordered = [
    ...scene.rooms.filter((room) => room.id !== 'break'),
    ...scene.rooms.filter((room) => room.id === 'break'),
  ];
  const idleCount = scene.npcs.filter((npc) => npc.targetRoomId === 'break').length;
  const rowsNeeded = Math.max(
    1,
    ...ordered.map((room) => Math.ceil(room.capacity / 6)),
    Math.ceil(Math.max(8, idleCount) / 6),
  );
  const side = Math.max(300, 70 + rowsNeeded * 65);
  const gap = 16;
  const columns = Math.min(2, ordered.length);
  const rows = Math.ceil(ordered.length / 2);
  const rawWidth = columns * side + Math.max(0, columns - 1) * gap;
  const rawHeight = rows * side + Math.max(0, rows - 1) * gap;
  const scale =
    rawWidth && rawHeight
      ? Math.max(
          0,
          Math.min(1, (viewport.width - 16) / rawWidth, (viewport.height - 16) / rawHeight),
        )
      : 1;
  // A single rounded side keeps every room square after fitting it to the viewport.
  const size = Math.floor(side * scale);
  const spacing = Math.max(1, Math.round(gap * scale));
  const width = columns * size + Math.max(0, columns - 1) * spacing;
  const height = rows * size + Math.max(0, rows - 1) * spacing;
  const left = Math.round((viewport.width - width) / 2);
  const top = Math.round((viewport.height - height) / 2);
  const actualScale = size / side;
  const point = (index: number, x: number, y: number): Point => ({
    x: left + (index % 2) * (size + spacing) + Math.round(x * actualScale),
    y: top + Math.floor(index / 2) * (size + spacing) + Math.round(y * actualScale),
  });
  const rooms = ordered.map((room, index) => {
    const origin = point(index, 0, 0);
    return {
      id: room.id,
      label: (room.id.startsWith('host:') || room.id.startsWith('team:')) ? room.label : harnessLabel(room.harness ?? 'break'),
      labelAt: point(index, side / 2, 22),
      shape: [
        origin,
        { x: origin.x + size, y: origin.y },
        { x: origin.x + size, y: origin.y + size },
        { x: origin.x, y: origin.y + size },
      ],
    };
  });
  const desks = scene.desks.flatMap((desk) => {
    const index = ordered.findIndex((room) => room.id === desk.roomId);
    if (index < 0) return [];
    const at = point(
      index,
      (((desk.index % 6) + 0.5) * side) / 6,
      68 + Math.floor(desk.index / 6) * 65,
    );
    return [{ id: desk.id, at, depth: at.y }];
  });
  const seats = new Map(
    desks.map((desk) => [desk.id, { x: desk.at.x, y: desk.at.y + Math.round(22 * actualScale) }]),
  );
  const breakIndex = ordered.findIndex((room) => room.id === 'break');
  const breakSpots =
    breakIndex < 0
      ? []
      : Array.from({ length: Math.max(8, idleCount) }, (_, i) =>
          point(breakIndex, (((i % 6) + 0.5) * side) / 6, 92 + Math.floor(i / 6) * 65),
        );
  return {
    rooms,
    desks,
    seats,
    breakSpots,
    bounds: { ...viewport },
    scale: actualScale,
    coffeeTable: breakIndex < 0 ? null : point(breakIndex, side / 2, side - 34),
  };
}
