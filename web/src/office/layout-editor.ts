import type { Point, SkinId } from './skin';
export type LayoutEdits = { desks: Record<string, Point>; rooms: Record<string, Point & { label?: string }> };
const empty = (): LayoutEdits => ({ desks: {}, rooms: {} });
const key = (skin: SkinId) => `hq-layout-${skin}`;
export function loadLayout(skin: SkinId): LayoutEdits {
  try {
    const value = JSON.parse(localStorage.getItem(key(skin)) ?? 'null');
    if (value && typeof value.desks === 'object' && typeof value.rooms === 'object') return value;
  } catch { /* Storage may be unavailable or invalid. */ }
  return empty();
}
export function saveLayout(skin: SkinId, layout: LayoutEdits): void {
  try { localStorage.setItem(key(skin), JSON.stringify(layout)); } catch { /* Storage unavailable. */ }
}
export function resetLayout(skin: SkinId): void {
  try { localStorage.removeItem(key(skin)); } catch { /* Storage unavailable. */ }
}
