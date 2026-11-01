import { Application, Container, Graphics } from 'pixi.js';
import { HARNESS_COLORS, harnessLabel } from '../../colors';
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
    for (const room of layout.rooms) {
      const color = room.harness
        ? (HARNESS_COLORS[room.harness] ?? HARNESS_COLORS.unknown)
        : '#a89b8c';
      floor
        .poly(room.shape.flatMap((point) => [point.x, point.y]))
        .fill({ color, alpha: 0.1 })
        .stroke({ color, width: 1.5, alpha: 0.35 });
    }
    if (layout.floorShape.length) {
      const edge = layout.floorShape;
      floor
        .moveTo(edge[3].x, edge[3].y)
        .lineTo(edge[0].x, edge[0].y)
        .lineTo(edge[1].x, edge[1].y)
        .stroke({ color: theme.wall, width: 2, alpha: 0.6 });
    }
    this.app.stage.addChildAt(floor, 0);
    const objects: { depth: number; display: Container }[] = [];
    for (const desk of layout.desks) {
      const g = new Graphics();
      drawDesk(g);
      if (scene.npcs.some((npc) => npc.deskId === desk.id && npc.state === 'idle')) g.alpha = 0.5;
      g.position.set(desk.at.x, desk.at.y);
      objects.push({ depth: desk.at.y, display: g });
    }
    if (layout.furniture) {
      const sofa = new Graphics();
      sofa.roundRect(-23, -16, 46, 15, 5).fill(theme.wall);
      sofa.roundRect(-21, -8, 42, 10, 4).fill(theme.muted);
      sofa.position.set(layout.furniture.sofa.x, layout.furniture.sofa.y);
      sofa.scale.set(layout.scale);
      objects.push({ depth: layout.furniture.sofa.y, display: sofa });
      const table = new Graphics();
      table.ellipse(0, 0, 13, 7).fill(theme.wall);
      table.ellipse(0, -3, 13, 7).fill(theme.floorAlt);
      table.position.set(layout.furniture.table.x, layout.furniture.table.y);
      table.scale.set(layout.scale);
      objects.push({ depth: layout.furniture.table.y, display: table });
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
        const pill = new Container();
        drawLabel(pill, `${harnessLabel(npc.harness)} · ${npc.title}`, theme.floorAlt, theme.text);
        pill.position.set(at.x, at.y - 58);
        display.addChild(pill);
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
    for (const room of layout.rooms) {
      const label = new Container();
      const labelTheme = theme as Theme & { labelBg?: string; labelText?: string };
      drawLabel(
        label,
        room.label,
        labelTheme.labelBg ?? theme.floorAlt,
        labelTheme.labelText ?? theme.muted,
      );
      label.position.set(room.labelAt.x, room.labelAt.y - 10);
      this.app.stage.addChild(label);
    }
  }
  destroy() {
    this.host?.removeEventListener('mousemove', this.move);
    this.host?.removeEventListener('mouseleave', this.leave);
    this.app.destroy(true, { children: true });
  }
}
