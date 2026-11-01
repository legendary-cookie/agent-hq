import { Container, Graphics, Text } from 'pixi.js';
import type { NpcState } from './scene';
import { STATUS_COLORS } from './colors';

export function drawPerson(g: Graphics, color: string, state: NpcState, t: number): void {
  g.clear();
  const offset = state === 'working' ? Math.sin(t * 6) * 2 : 0;
  g.ellipse(0, 0, 11, 4).stroke({ color: STATUS_COLORS[state], width: 2 });
  g.roundRect(-7, -22 + offset, 14, 20, 4).fill(color);
  g.circle(0, -28 + offset, 7).fill('#f3c7a5');
  if (state === 'waiting') {
    g.circle(11, -39, 7).fill(STATUS_COLORS.waiting);
    g.rect(10, -43, 2, 5).fill('#ffffff');
    g.circle(11, -36, 1).fill('#ffffff');
  }
  if (state === 'error') g.circle(11, -38, 6).fill(STATUS_COLORS.error);
}
export function drawDesk(g: Graphics): void {
  g.clear();
  g.roundRect(-15, -8, 30, 12, 2).fill('#b89b7c');
  g.rect(-12, 4, 3, 7).fill('#806c59');
  g.rect(9, 4, 3, 7).fill('#806c59');
}
export function drawLabel(container: Container, text: string): void {
  container.addChild(new Text({ text, style: { fontSize: 12, fill: '#8e8e93' } }));
}
