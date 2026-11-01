import { Application, Container, Graphics } from 'pixi.js';
import { HARNESS_COLORS, harnessLabel } from '../../colors';
import { drawDesk, drawLabel, drawPerson } from '../../draw';
import { approach } from '../../motion';
import { pickNpc } from '../../pick';
import type { Scene } from '../../scene';
import type { Point, Skin, Theme } from '../../skin';
import { layoutTower, towerWalkPath } from './layout';

function alongPath(path: Point[], progress: number): Point {
  const lengths = path
    .slice(1)
    .map((point, i) => Math.hypot(point.x - path[i].x, point.y - path[i].y));
  let remaining = lengths.reduce((a, b) => a + b, 0) * Math.max(0, Math.min(1, progress));
  for (let i = 0; i < lengths.length; i++) {
    if (remaining <= lengths[i] && lengths[i] > 0) {
      const t = remaining / lengths[i];
      return {
        x: path[i].x + (path[i + 1].x - path[i].x) * t,
        y: path[i].y + (path[i + 1].y - path[i].y) * t,
      };
    }
    remaining -= lengths[i];
  }
  return path[path.length - 1];
}

export class TowerSkin implements Skin {
  private app = new Application();
  private width = 800;
  private height = 500;
  private host: HTMLElement | null = null;
  private positions = new Map<string, Point>();
  private targets: { id: string; at: Point; depth: number }[] = [];
  private hovered: string | null = null;
  private selected: string | null = null;
  private time = 0;
  private move = (event: MouseEvent) => {
    const rect = this.host!.getBoundingClientRect();
    this.hovered = this.pick(event.clientX - rect.left, event.clientY - rect.top);
  };
  private leave = () => {
    this.hovered = null;
  };
  async mount(host: HTMLElement) {
    this.host = host;
    await this.app.init({ backgroundAlpha: 0, antialias: true, resizeTo: host });
    host.appendChild(this.app.canvas);
    host.addEventListener('mousemove', this.move);
    host.addEventListener('mouseleave', this.leave);
  }
  resize(width: number, height: number) {
    this.width = width;
    this.height = height;
  }
  setSelected(id: string | null) {
    this.selected = id;
  }
  pick(x: number, y: number) {
    return pickNpc(this.targets, x, y);
  }
  render(scene: Scene, dt: number, theme: Theme) {
    this.time += dt;
    this.app.stage.removeChildren().forEach((child) => child.destroy({ children: true }));
    const layout = layoutTower(scene, { width: this.width, height: this.height });
    const background = new Graphics();
    for (const room of layout.rooms) {
      const source = scene.rooms.find((item) => item.id === room.id);
      const [a, b, c] = room.shape;
      background
        .rect(a.x, a.y, b.x - a.x, c.y - a.y)
        .fill(theme.floor)
        .stroke({ color: theme.wall, width: 2 });
      background
        .rect(
          a.x + 4 * layout.scale,
          a.y + 4 * layout.scale,
          b.x - a.x - 8 * layout.scale,
          c.y - a.y - 8 * layout.scale,
        )
        .fill({
          color: HARNESS_COLORS[source?.harness ?? 'unknown'] ?? HARNESS_COLORS.unknown,
          alpha: 0.09,
        });
    }
    if (layout.rooms.length)
      background
        .rect(
          layout.liftX - 20 * layout.scale,
          layout.liftTop,
          40 * layout.scale,
          layout.liftBottom - layout.liftTop,
        )
        .fill(theme.floorAlt)
        .stroke({ color: theme.wall, width: 2 });
    this.app.stage.addChild(background);
    for (const desk of layout.desks) {
      const g = new Graphics();
      drawDesk(g);
      g.position.set(desk.at.x, desk.at.y);
      g.scale.set(layout.scale);
      if (scene.npcs.some((npc) => npc.deskId === desk.id && npc.state === 'idle')) g.alpha = 0.5;
      this.app.stage.addChild(g);
    }
    let idle = 0;
    const activeIds = new Set(scene.npcs.map((npc) => npc.id));
    for (const id of this.positions.keys()) if (!activeIds.has(id)) this.positions.delete(id);
    this.targets = scene.npcs.map((npc) => {
      const target =
        npc.targetRoomId === 'break'
          ? layout.breakSpots[idle++]
          : npc.deskId
            ? layout.seats.get(npc.deskId)
            : undefined;
      let at = target ?? { x: 0, y: 0 };
      const walk = scene.walks.find(
        (item) =>
          item.npcId === npc.id &&
          Date.now() / 1000 - item.startedAt >= 0 &&
          Date.now() / 1000 - item.startedAt < 3,
      );
      if (walk && !theme.reducedMotion) {
        const path = towerWalkPath(layout, walk.fromRoomId, walk.toRoomId);
        if (path.length) {
          const sourceDesk = scene.desks.find(
            (desk) => desk.roomId === walk.fromRoomId && desk.id === npc.deskId,
          );
          const destinationDesk = scene.desks.find((desk) => desk.roomId === walk.toRoomId);
          path[0] = (sourceDesk && layout.seats.get(sourceDesk.id)) || path[0];
          path[3] = (destinationDesk && layout.seats.get(destinationDesk.id)) || path[3];
          path[1].y = path[0].y;
          path[2].y = path[3].y;
          at = alongPath(path, (Date.now() / 1000 - walk.startedAt) / 3);
        }
      } else if (!theme.reducedMotion) at = approach(this.positions.get(npc.id) ?? at, at, 120, dt);
      this.positions.set(npc.id, at);
      const display = new Container();
      const person = new Graphics();
      drawPerson(person, npc.color, npc.state, theme.reducedMotion ? 0 : this.time);
      if (this.selected === npc.id)
        person.ellipse(0, 0, 14, 6).stroke({ color: '#0071e3', width: 2 });
      person.position.set(at.x, at.y);
      person.scale.set(layout.scale * (this.hovered === npc.id ? 1.1 : 1));
      display.addChild(person);
      if (this.hovered === npc.id) {
        const pill = new Container();
        drawLabel(pill, `${harnessLabel(npc.harness)} · ${npc.title}`, theme.floorAlt, theme.text);
        pill.position.set(at.x, at.y - 58 * layout.scale);
        display.addChild(pill);
      }
      this.app.stage.addChild(display);
      return { id: npc.id, at, depth: at.y };
    });
    for (const room of layout.rooms) {
      const label = new Container();
      const count = scene.npcs.filter((npc) => npc.targetRoomId === room.id).length;
      drawLabel(label, `${room.label} · ${count}`, theme.floorAlt, theme.text);
      label.position.set(room.labelAt.x, room.labelAt.y);
      this.app.stage.addChild(label);
    }
  }
  destroy() {
    this.host?.removeEventListener('mousemove', this.move);
    this.host?.removeEventListener('mouseleave', this.leave);
    this.app.destroy(true, { children: true });
  }
}
