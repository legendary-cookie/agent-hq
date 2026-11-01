import type { Point } from './skin';
export function npcHitArea(at: Point): { x: number; y: number; w: number; h: number } {
  return { x: at.x - 12, y: at.y - 40, w: 24, h: 40 };
}
export function pickNpc(
  npcs: { id: string; at: Point; depth: number }[],
  x: number,
  y: number,
): string | null {
  let found: { id: string; depth: number } | null = null;
  for (const npc of npcs) {
    const a = npcHitArea(npc.at);
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
