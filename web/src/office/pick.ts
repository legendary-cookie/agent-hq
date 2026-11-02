import type { Point } from './skin';
export function npcHitArea(at: Point, scale = 1): { x: number; y: number; w: number; h: number } {
  return { x: at.x - 12 * scale, y: at.y - 40 * scale, w: 24 * scale, h: 40 * scale };
}
export function pickNpc(
  npcs: { id: string; at: Point; depth: number; scale?: number }[],
  x: number,
  y: number,
): string | null {
  let found: { id: string; depth: number } | null = null;
  for (const npc of npcs) {
    const a = npcHitArea(npc.at, npc.scale);
    if (
      x >= a.x &&
      x <= a.x + a.w &&
      y >= a.y &&
      y <= a.y + a.h &&
      (!found || npc.depth >= found.depth)
    )
      found = npc;
  }
  return found?.id ?? null;
}
