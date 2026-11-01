import { Application, Container, Graphics, Text } from 'pixi.js';
import { drawDesk, drawLabel, drawPerson } from '../../draw';
import { approach } from '../../motion';
import { pickNpc } from '../../pick';
import type { Scene } from '../../scene';
import type { Point, Skin, Theme } from '../../skin';
import { layoutIsometric } from './layout';

export class IsometricSkin implements Skin {
  private app = new Application();
  private width = 800;
  private height = 500;
  private positions = new Map<string, Point>();
  private targets: { id: string; at: Point; depth: number }[] = [];
  private hovered: string | null = null;
  private selected: string | null = null;
  private time = 0;
  private host: HTMLElement | null = null;
  private move = (event: MouseEvent) => {
    const rect = this.host!.getBoundingClientRect();
    this.hovered = this.pick(event.clientX - rect.left, event.clientY - rect.top);
  };
  async mount(host: HTMLElement) {
    this.host = host;
    await this.app.init({ backgroundAlpha: 0, antialias: true, resizeTo: host });
    host.appendChild(this.app.canvas);
    host.addEventListener('mousemove', this.move);
    host.addEventListener('mouseleave', this.leave);
  }
  private leave = () => {
    this.hovered = null;
  };
  setSelected(id: string | null) {
    this.selected = id;
  }
  resize(width: number, height: number) {
    this.width = width;
    this.height = height;
  }
  pick(x: number, y: number) {
    return pickNpc(this.targets, x, y);
  }
  render(scene: Scene, dt: number, theme: Theme) {
    this.time += dt;
    this.app.stage.removeChildren().forEach((child) => child.destroy({ children: true }));
    const layout = layoutIsometric(scene, { width: this.width, height: this.height });
    const floor = new Graphics();
    layout.tiles.forEach((tile) => {
      floor
        .poly(tile.shape.flatMap((p) => [p.x, p.y]))
        .fill(tile.alternate ? theme.floor : theme.floorAlt)
        .stroke({ color: theme.wall, width: 0.5, alpha: 0.2 });
    });
    layout.rooms.forEach((room) => {
      floor
        .moveTo(room.shape[3].x, room.shape[3].y)
        .lineTo(room.shape[0].x, room.shape[0].y)
        .lineTo(room.shape[1].x, room.shape[1].y)
        .stroke({ color: theme.wall, width: 2, alpha: 0.6 });
      const label = new Container();
      drawLabel(label, room.label);
      label.position.set(room.labelAt.x, room.labelAt.y - 18);
      this.app.stage.addChild(label);
    });
    this.app.stage.addChildAt(floor, 0);
    const objects: { depth: number; display: Container }[] = [];
    for (const desk of layout.desks) {
      const g = new Graphics();
      drawDesk(g);
      g.position.set(desk.at.x, desk.at.y);
      objects.push({ depth: desk.at.y, display: g });
    }
    let idle = 0;
    this.targets = scene.npcs.map((npc) => {
      const target =
        npc.targetRoomId === 'break'
          ? layout.breakSpots[idle++ % layout.breakSpots.length]
          : npc.deskId
            ? layout.seats.get(npc.deskId)
            : undefined;
      const at = target
        ? theme.reducedMotion
          ? target
          : approach(this.positions.get(npc.id) ?? target, target, 120, dt)
        : { x: 0, y: 0 };
      this.positions.set(npc.id, at);
      const g = new Graphics();
      drawPerson(g, npc.color, npc.state, theme.reducedMotion ? 0 : this.time);
      if (this.selected === npc.id) g.ellipse(0, 0, 14, 6).stroke({ color: '#0071e3', width: 2 });
      g.position.set(at.x, at.y);
      if (this.hovered === npc.id) g.scale.set(1.1);
      const display = new Container();
      display.addChild(g);
      if (this.hovered === npc.id) {
        const title = new Text({ text: npc.title, style: { fontSize: 11, fill: theme.text } });
        title.position.set(at.x - title.width / 2, at.y - 58);
        display.addChild(title);
      }
      objects.push({ depth: at.y, display });
      return { id: npc.id, at, depth: at.y };
    });
    const paths = new Graphics();
    for (const walk of scene.walks) {
      const from = layout.rooms.find((room) => room.id === walk.fromRoomId)?.labelAt;
      const npc = scene.npcs.find((item) => item.id === walk.npcId);
      const to =
        (npc?.deskId && layout.seats.get(npc.deskId)) ||
        layout.rooms.find((room) => room.id === walk.toRoomId)?.labelAt;
      const age = Date.now() / 1000 - walk.startedAt;
      if (!from || !to || age >= 3 || theme.reducedMotion) continue;
      for (let t = 0; t < 1; t += 0.08) {
        const end = Math.min(1, t + 0.04);
        paths
          .moveTo(from.x + (to.x - from.x) * t, from.y + (to.y - from.y) * t)
          .lineTo(from.x + (to.x - from.x) * end, from.y + (to.y - from.y) * end)
          .stroke({ color: theme.muted, width: 1.5, alpha: Math.max(0, 1 - age / 3) });
      }
    }
    this.app.stage.addChild(paths);
    objects
      .sort((a, b) => a.depth - b.depth)
      .forEach((item) => this.app.stage.addChild(item.display));
  }
  destroy() {
    this.host?.removeEventListener('mousemove', this.move);
    this.host?.removeEventListener('mouseleave', this.leave);
    this.app.destroy(true, { children: true });
  }
}
