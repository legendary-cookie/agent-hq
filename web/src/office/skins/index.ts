import type { Skin, SkinId } from '../skin';
export const SKINS: Record<SkinId, { label: string; create: () => Promise<Skin> }> = {
  isometric: { label: 'Isometric', create: async () => (await import('./placeholder')).create() },
  topdown: { label: 'Top-down', create: async () => (await import('./placeholder')).create() },
  tower: { label: 'Tower', create: async () => (await import('./placeholder')).create() },
};
export function parseSkin(value: string | null | undefined, fallback: SkinId): SkinId {
  return value === 'isometric' || value === 'topdown' || value === 'tower' ? value : fallback;
}
