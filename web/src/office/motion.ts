import type { Point, Theme } from './skin';
export function approach(current: Point, target: Point, speed: number, dt: number): Point {
  const dx = target.x - current.x;
  const dy = target.y - current.y;
  const distance = Math.hypot(dx, dy);
  const step = Math.max(0, speed * dt);
  if (distance === 0 || step >= distance) return { ...target };
  return { x: current.x + (dx * step) / distance, y: current.y + (dy * step) / distance };
}
export function bob(t: number, phase: number): number {
  return 2 * Math.sin(t * 6 + phase);
}
export function readTheme(el: HTMLElement): Theme {
  const css = getComputedStyle(el);
  const value = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
  return {
    floor: value('--office-floor', '#f2f2f7'),
    floorAlt: value('--office-floor-alt', '#e5e5ea'),
    wall: value('--office-wall', '#c7c7cc'),
    text: value('--text', '#1d1d1f'),
    muted: value('--muted', '#8e8e93'),
    reducedMotion:
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  };
}
