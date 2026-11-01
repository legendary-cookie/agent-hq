import { Application, Container, Graphics } from 'pixi.js';
import { HARNESS_COLORS, harnessLabel } from '../../colors';
import { drawLabel, drawPerson } from '../../draw';
import { approach } from '../../motion';
import { pickNpc } from '../../pick';
import type { Scene } from '../../scene';
import type { Point, Skin, Theme } from '../../skin';
import { layoutTopdown } from './layout';

export class TopdownSkin implements Skin {
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
    if (!this.host) return;
    const rect = this.host.getBoundingClientRect();
    this.hovered = this.pick(event.clientX - rect.left, event.clientY - rect.top);
  };
  private leave = () => {
    this.hovered = null;
  };

  async mount(host: HTMLElement) {
    this.host = host;
    await this.app.init({ backgroundAlpha: 0, antialias: false, resizeTo: host });
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
    const layout = layoutTopdown(scene, { width: this.width, height: this.height });
    const floor = new Graphics();
    for (const room of layout.rooms) {
      const source = scene.rooms.find((item) => item.id === room.id);
      const [a, , c] = room.shape;
      floor
        .rect(a.x, a.y, c.x - a.x, c.y - a.y)
        .fill(theme.floor)
        .stroke({ color: theme.wall, width: 2 });
      if (source?.harness) {
        floor
          .rect(a.x + 2, a.y + 2, Math.max(0, c.x - a.x - 4), Math.max(0, c.y - a.y - 4))
          .fill({ color: HARNESS_COLORS[source.harness] ?? HARNESS_COLORS.unknown, alpha: 0.12 });
      }
    }
    this.app.stage.addChild(floor);
    const objects: { depth: number; display: Container }[] = [];
    for (const desk of layout.desks) {
      const g = new Graphics();
      g.rect(-17, -8, 34, 16).fill(theme.floorAlt).stroke({ color: theme.wall, width: 2 });
      g.rect(-8, -6, 16, 7).fill(theme.wall);
      g.position.set(desk.at.x, desk.at.y);
      g.scale.set(layout.scale);
      if (scene.npcs.some((npc) => npc.deskId === desk.id && npc.state === 'idle')) g.alpha = 0.5;
      objects.push({ depth: desk.at.y, display: g });
    }
    if (layout.coffeeTable) {
      const table = new Graphics();
      table.rect(-17, -10, 34, 20).fill(theme.floorAlt).stroke({ color: theme.wall, width: 2 });
      table.rect(-4, -5, 8, 8).fill(theme.muted);
      table.position.set(layout.coffeeTable.x, layout.coffeeTable.y);
      table.scale.set(layout.scale);
      objects.push({ depth: layout.coffeeTable.y, display: table });
    }
    const activeIds = new Set(scene.npcs.map((npc) => npc.id));
    for (const id of this.positions.keys()) if (!activeIds.has(id)) this.positions.delete(id);
    let idle = 0;
    this.targets = scene.npcs.flatMap((npc) => {
      const target =
        npc.targetRoomId === 'break'
          ? layout.breakSpots[idle++]
          : npc.deskId
            ? layout.seats.get(npc.deskId)
            : undefined;
      if (!target) return [];
      const current = theme.reducedMotion
        ? target
        : approach(this.positions.get(npc.id) ?? target, target, 120, dt);
      this.positions.set(npc.id, current);
      const at = { x: Math.round(current.x), y: Math.round(current.y) };
      const display = new Container();
      const person = new Graphics();
      drawPerson(person, npc.color, npc.state, theme.reducedMotion ? 0 : this.time);
      if (this.selected === npc.id)
        person.ellipse(0, 0, 14, 6).stroke({ color: '#0071e3', width: 2 });
      person.position.set(at.x, at.y);
      if (this.hovered === npc.id) person.scale.set(1.1);
      display.addChild(person);
      if (this.hovered === npc.id) {
        const pill = new Container();
        drawLabel(pill, `${harnessLabel(npc.harness)} · ${npc.title}`, theme.floorAlt, theme.text);
        pill.position.set(at.x, at.y - 58);
        display.addChild(pill);
      }
      objects.push({ depth: at.y, display });
      return [{ id: npc.id, at, depth: at.y }];
    });
    const paths = new Graphics();
    for (const walk of scene.walks) {
      const from = layout.rooms.find((room) => room.id === walk.fromRoomId)?.labelAt;
      const to = layout.rooms.find((room) => room.id === walk.toRoomId)?.labelAt;
      const age = Date.now() / 1000 - walk.startedAt;
      if (!from || !to || age < 0 || age >= 3 || theme.reducedMotion) continue;
      for (let t = 0; t < 1; t += 0.08) {
        const end = Math.min(1, t + 0.04);
        paths
          .moveTo(from.x + (to.x - from.x) * t, from.y + (to.y - from.y) * t)
          .lineTo(from.x + (to.x - from.x) * end, from.y + (to.y - from.y) * end)
          .stroke({ color: theme.muted, width: 2, alpha: 1 - age / 3 });
      }
    }
    this.app.stage.addChild(paths);
    objects
      .sort((a, b) => a.depth - b.depth)
      .forEach((object) => this.app.stage.addChild(object.display));
    for (const room of layout.rooms) {
      const count = scene.npcs.filter((npc) => npc.targetRoomId === room.id).length;
      const label = new Container();
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
