import type { Scene } from './scene';
import type { Theme } from './skin';

export function staticKey(
  scene: Scene,
  size: { width: number; height: number },
  theme: Theme,
): string {
  const occupancy = scene.rooms.map((room) => ({
    id: room.id,
    harnessCount: scene.npcs.filter((npc) => npc.harness === room.harness).length,
    targetCount: scene.npcs.filter((npc) => npc.targetRoomId === room.id).length,
  }));
  const idleDesks = scene.npcs
    .filter((npc) => npc.state === 'idle' && npc.deskId)
    .map((npc) => npc.deskId)
    .sort();
  return JSON.stringify([scene.rooms, scene.desks, occupancy, idleDesks, size, theme]);
}

export function reconcile(
  prevIds: Iterable<string>,
  nextIds: Iterable<string>,
): {
  add: string[];
  remove: string[];
  keep: string[];
} {
  const prev = new Set(prevIds);
  const next = new Set(nextIds);
  return {
    add: [...next].filter((id) => !prev.has(id)),
    remove: [...prev].filter((id) => !next.has(id)),
    keep: [...next].filter((id) => prev.has(id)),
  };
}
