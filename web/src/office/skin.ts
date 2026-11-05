import type { Scene } from './scene';
export type SkinId = 'isometric' | 'topdown' | 'tower';
export type Point = { x: number; y: number };
export interface Skin {
  mount(host: HTMLElement): Promise<void>;
  render(scene: Scene, dt: number, theme: Theme): void;
  pick(x: number, y: number): string | null;
  resize(width: number, height: number): void;
  setSelected?(id: string | null): void;
  setFollow?(id: string | null): void;
  destroy(): void;
}
export type Theme = {
  floor: string;
  floorAlt: string;
  wall: string;
  text: string;
  muted: string;
  reducedMotion: boolean;
};
export type SkinLayout = {
  rooms: { id: string; label: string; labelAt: Point; shape: Point[] }[];
  desks: { id: string; at: Point; depth: number }[];
  seats: Map<string, Point>;
  breakSpots: Point[];
  bounds: { width: number; height: number };
};
