import { Container, Graphics, Text } from 'pixi.js';
import type { NpcState } from './scene';
import { STATUS_COLORS } from './colors';

export function drawPerson(g: Graphics, color: string, state: NpcState, _t: number): void {
  g.clear();
  g.ellipse(0, 0, 11, 4).stroke({ color: STATUS_COLORS[state], width: 2 });
  g.roundRect(-7, -22, 14, 20, 4).fill(color);
  g.circle(0, -28, 7).fill(color);
  if (state === 'waiting') {
    g.circle(11, -39, 7).fill(STATUS_COLORS.waiting);
    g.rect(10, -43, 2, 5).fill('#ffffff');
    g.circle(11, -36, 1).fill('#ffffff');
  }
  if (state === 'error') {
    g.circle(11, -38, 5).fill({ color: STATUS_COLORS.error, alpha: 0.75 });
    g.circle(16, -42, 3).fill({ color: STATUS_COLORS.error, alpha: 0.4 });
  }
}
export function drawDesk(g: Graphics): void {
  g.clear();
  g.poly([-16, -5, 0, -13, 16, -5, 0, 3]).fill('#cdbca8');
  g.poly([-16, -5, 0, 3, 0, 8, -16, 0]).fill('#b5a38f');
  g.poly([0, 3, 16, -5, 16, 0, 0, 8]).fill('#a99580');
}
export function drawLabel(
  container: Container,
  text: string,
  surface: string,
  muted: string,
): void {
  const label = new Text({ text, style: { fontSize: 11, fill: muted } });
  const background = new Graphics();
  background
    .roundRect(-label.width / 2 - 6, -label.height / 2 - 3, label.width + 12, label.height + 6, 7)
    .fill(surface);
  label.position.set(-label.width / 2, -label.height / 2);
  container.addChild(background, label);
}
