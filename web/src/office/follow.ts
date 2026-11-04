import type { Point } from './skin';
export function followOffset(at: Point, centre: Point): Point {
  return { x: centre.x - at.x, y: centre.y - at.y };
}
