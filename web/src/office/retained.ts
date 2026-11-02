import { Application, Container, Graphics, Text } from 'pixi.js';
import { HARNESS_COLORS, STATUS_COLORS, harnessLabel } from './colors';
import { drawDesk, drawLabel } from './draw';
import { approach, bob } from './motion';
import { pickNpc } from './pick';
import { reconcile, staticKey } from './render-cache';
import type { Npc, Scene } from './scene';
import type { Point, Skin, Theme } from './skin';
import { layoutIsometric } from './skins/isometric/layout';
import { layoutTopdown } from './skins/topdown/layout';
import { layoutTower, towerWalkPath } from './skins/tower/layout';

type Kind = 'isometric' | 'topdown' | 'tower';
type Layout =
  | ReturnType<typeof layoutIsometric>
  | ReturnType<typeof layoutTopdown>
  | ReturnType<typeof layoutTower>;
type Person = {
  container: Container;
  body: Graphics;
  bubble: Graphics;
  puff: Graphics;
  signature: string;
  at?: Point;
};
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
export class RetainedSkin implements Skin {
  private app = new Application();
  private host: HTMLElement | null = null;
  private width = 800;
  private height = 500;
  private key = '';
  private previousScene: Scene | null = null;
  private previousInputs = '';
  private time = 0;
  private layout: Layout | null = null;
  private floor = new Container();
  private paths = new Graphics();
  private objects = new Container();
  private labels = new Container();
  private hover = new Container();
  private hoverBackground = new Graphics();
  private hoverText = new Text({ text: '', style: { fontSize: 11 } });
  private people = new Map<string, Person>();
  private staticObjects: Container[] = [];
  private targets: {
    id: string;
    at: Point;
    depth: number;
  }[] = [];
  private hovered: string | null = null;
  private selected: string | null = null;
  private hoverSignature = '';
  constructor(private kind: Kind) {}
  private move = (event: MouseEvent) => {
    const rect = this.host!.getBoundingClientRect();
    this.hovered = this.pick(event.clientX - rect.left, event.clientY - rect.top);
  };
  private leave = () => {
    this.hovered = null;
  };
  async mount(host: HTMLElement) {
    this.host = host;
    await this.app.init({ backgroundAlpha: 0, antialias: this.kind !== 'topdown', resizeTo: host });
    host.appendChild(this.app.canvas);
    this.objects.sortableChildren = true;
    this.hover.addChild(this.hoverBackground, this.hoverText);
    this.app.stage.addChild(this.floor, this.paths, this.objects, this.labels, this.hover);
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
  private rebuild(scene: Scene, theme: Theme) {
    this.floor.removeChildren().forEach((child) => child.destroy({ children: true }));
    this.labels.removeChildren().forEach((child) => child.destroy({ children: true }));
    for (const object of this.staticObjects) {
      this.objects.removeChild(object);
      object.destroy({ children: true });
    }
    this.staticObjects = [];
    const size = { width: this.width, height: this.height };
    const layout =
      this.kind === 'isometric'
        ? layoutIsometric(scene, size)
        : this.kind === 'topdown'
          ? layoutTopdown(scene, size)
          : layoutTower(scene, size);
    this.layout = layout;
    const floor = new Graphics();
    if ('tiles' in layout) {
      for (const tile of layout.tiles)
        floor
          .poly(tile.shape.flatMap((p) => [p.x, p.y]))
          .fill(tile.alternate ? theme.floor : theme.floorAlt)
          .stroke({ color: theme.wall, width: 0.5, alpha: 0.2 });
      for (const room of layout.rooms) {
        const color = room.harness
          ? (HARNESS_COLORS[room.harness] ?? HARNESS_COLORS.unknown)
          : '#a89b8c';
        floor
          .poly(room.shape.flatMap((p) => [p.x, p.y]))
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
    } else {
      for (const room of layout.rooms) {
        const source = scene.rooms.find((item) => item.id === room.id);
        const [a, , c] = room.shape;
        floor
          .rect(a.x, a.y, c.x - a.x, c.y - a.y)
          .fill(theme.floor)
          .stroke({ color: theme.wall, width: 2 });
        if (this.kind === 'tower') {
          floor
            .rect(
              a.x + 4 * layout.scale,
              a.y + 4 * layout.scale,
              c.x - a.x - 8 * layout.scale,
              c.y - a.y - 8 * layout.scale,
            )
            .fill({
              color: HARNESS_COLORS[source?.harness ?? 'unknown'] ?? HARNESS_COLORS.unknown,
              alpha: 0.09,
            });
        } else if (source?.harness) {
          floor
            .rect(a.x + 2, a.y + 2, Math.max(0, c.x - a.x - 4), Math.max(0, c.y - a.y - 4))
            .fill({ color: HARNESS_COLORS[source.harness] ?? HARNESS_COLORS.unknown, alpha: 0.12 });
        }
      }
      if ('liftX' in layout && layout.rooms.length)
        floor
          .rect(
            layout.liftX - 20 * layout.scale,
            layout.liftTop,
            40 * layout.scale,
            layout.liftBottom - layout.liftTop,
          )
          .fill(theme.floorAlt)
          .stroke({ color: theme.wall, width: 2 });
    }
    this.floor.addChild(floor);
    const addObject = (g: Graphics, at: Point, scale: number) => {
      g.position.set(at.x, at.y);
      g.scale.set(scale);
      g.zIndex = at.y;
      this.staticObjects.push(g);
      this.objects.addChild(g);
    };
    for (const desk of layout.desks) {
      const g = new Graphics();
      if (this.kind === 'topdown') {
        g.rect(-17, -8, 34, 16).fill(theme.floorAlt).stroke({ color: theme.wall, width: 2 });
        g.rect(-8, -6, 16, 7).fill(theme.wall);
      } else {
        drawDesk(g);
      }
      if (scene.npcs.some((npc) => npc.deskId === desk.id && npc.state === 'idle')) g.alpha = 0.5;
      addObject(g, desk.at, this.kind === 'isometric' ? 1 : layout.scale);
    }
    if ('furniture' in layout && layout.furniture) {
      const sofa = new Graphics();
      sofa.roundRect(-23, -16, 46, 15, 5).fill(theme.wall);
      sofa.roundRect(-21, -8, 42, 10, 4).fill(theme.muted);
      addObject(sofa, layout.furniture.sofa, layout.scale);
      const table = new Graphics();
      table.ellipse(0, 0, 13, 7).fill(theme.wall);
      table.ellipse(0, -3, 13, 7).fill(theme.floorAlt);
      addObject(table, layout.furniture.table, layout.scale);
    }
    if ('coffeeTable' in layout && layout.coffeeTable) {
      const table = new Graphics();
      table.rect(-17, -10, 34, 20).fill(theme.floorAlt).stroke({ color: theme.wall, width: 2 });
      table.rect(-4, -5, 8, 8).fill(theme.muted);
      addObject(table, layout.coffeeTable, layout.scale);
    }
    for (const room of layout.rooms) {
      const label = new Container();
      const count = scene.npcs.filter((npc) => npc.targetRoomId === room.id).length;
      const labelTheme = theme as Theme & {
        labelBg?: string;
        labelText?: string;
      };
      drawLabel(
        label,
        this.kind === 'isometric' ? room.label : `${room.label} · ${count}`,
        this.kind === 'isometric' ? (labelTheme.labelBg ?? theme.floorAlt) : theme.floorAlt,
        this.kind === 'isometric' ? (labelTheme.labelText ?? theme.muted) : theme.text,
      );
      label.position.set(room.labelAt.x, room.labelAt.y - (this.kind === 'isometric' ? 10 : 0));
      this.labels.addChild(label);
    }
  }
  private addPerson(id: string) {
    const container = new Container();
    const body = new Graphics();
    const bubble = new Graphics();
    bubble.circle(11, -39, 7).fill(STATUS_COLORS.waiting);
    bubble.rect(10, -43, 2, 5).fill('#ffffff');
    bubble.circle(11, -36, 1).fill('#ffffff');
    const puff = new Graphics();
    puff.circle(11, -38, 5).fill({ color: STATUS_COLORS.error, alpha: 0.75 });
    puff.circle(16, -42, 3).fill({ color: STATUS_COLORS.error, alpha: 0.4 });
    container.addChild(body, bubble, puff);
    this.objects.addChild(container);
    this.people.set(id, { container, body, bubble, puff, signature: '' });
  }
  private updatePerson(person: Person, npc: Npc) {
    const signature = JSON.stringify([npc.color, npc.state, this.selected === npc.id]);
    if (signature !== person.signature) {
      person.body.clear();
      person.body.ellipse(0, 0, 11, 4).stroke({ color: STATUS_COLORS[npc.state], width: 2 });
      person.body.roundRect(-7, -22, 14, 20, 4).fill(npc.color);
      person.body.circle(0, -28, 7).fill(npc.color);
      if (this.selected === npc.id)
        person.body.ellipse(0, 0, 14, 6).stroke({ color: '#0071e3', width: 2 });
      person.signature = signature;
    }
    person.bubble.visible = npc.state === 'waiting';
    person.puff.visible = npc.state === 'error';
  }
  render(scene: Scene, dt: number, theme: Theme) {
    this.time += dt;
    const inputs = JSON.stringify([this.width, this.height, theme]);
    if (scene !== this.previousScene || inputs !== this.previousInputs) {
      const key = staticKey(scene, { width: this.width, height: this.height }, theme);
      if (key !== this.key || !this.layout) {
        this.rebuild(scene, theme);
        this.key = key;
      }
      this.previousScene = scene;
      this.previousInputs = inputs;
    }
    const layout = this.layout!;
    const changes = reconcile(
      this.people.keys(),
      scene.npcs.map((npc) => npc.id),
    );
    for (const id of changes.remove) {
      const person = this.people.get(id)!;
      this.objects.removeChild(person.container);
      person.container.destroy({ children: true });
      this.people.delete(id);
    }
    for (const id of changes.add) this.addPerson(id);
    const now = Date.now() / 1000;
    let idle = 0;
    this.targets = [];
    for (const npc of scene.npcs) {
      const person = this.people.get(npc.id)!;
      const target =
        npc.targetRoomId === 'break'
          ? layout.breakSpots[idle++]
          : npc.deskId
            ? layout.seats.get(npc.deskId)
            : undefined;
      if (!target && this.kind !== 'isometric') {
        person.container.visible = false;
        continue;
      }
      person.container.visible = true;
      let at = target ?? { x: 0, y: 0 };
      const walk =
        this.kind === 'tower' && !theme.reducedMotion
          ? scene.walks.find(
              (item) =>
                item.npcId === npc.id && now - item.startedAt >= 0 && now - item.startedAt < 3,
            )
          : undefined;
      if (walk && 'liftX' in layout) {
        const path = towerWalkPath(layout, walk.fromRoomId, walk.toRoomId);
        if (path.length) {
          const source = scene.desks.find(
            (desk) => desk.roomId === walk.fromRoomId && desk.id === npc.deskId,
          );
          const destination = scene.desks.find((desk) => desk.roomId === walk.toRoomId);
          path[0] = (source && layout.seats.get(source.id)) || path[0];
          path[3] = (destination && layout.seats.get(destination.id)) || path[3];
          path[1].y = path[0].y;
          path[2].y = path[3].y;
          at = alongPath(path, (now - walk.startedAt) / 3);
        }
      } else if (!theme.reducedMotion && target) at = approach(person.at ?? at, at, 120, dt);
      person.at = at;
      const displayAt = this.kind === 'topdown' ? { x: Math.round(at.x), y: Math.round(at.y) } : at;
      person.container.position.set(displayAt.x, displayAt.y);
      person.container.zIndex = displayAt.y;
      person.container.scale.set(
        (this.kind === 'tower' ? layout.scale : 1) * (this.hovered === npc.id ? 1.1 : 1),
      );
      this.updatePerson(person, npc);
      const offset = !theme.reducedMotion && npc.state === 'working' ? bob(this.time, 0) : 0;
      person.body.y = offset;
      person.bubble.y = offset;
      person.puff.y = offset;
      this.targets.push({ id: npc.id, at: displayAt, depth: displayAt.y });
    }
    this.paths.clear();
    if (this.kind !== 'tower' && !theme.reducedMotion)
      for (const walk of scene.walks) {
        const from = layout.rooms.find((room) => room.id === walk.fromRoomId)?.labelAt;
        const npc = scene.npcs.find((item) => item.id === walk.npcId);
        const to =
          (this.kind === 'isometric' && npc?.deskId && layout.seats.get(npc.deskId)) ||
          layout.rooms.find((room) => room.id === walk.toRoomId)?.labelAt;
        const age = now - walk.startedAt;
        if (!from || !to || age >= 3 || (this.kind === 'topdown' && age < 0)) continue;
        for (let t = 0; t < 1; t += 0.08) {
          const end = Math.min(1, t + 0.04);
          this.paths
            .moveTo(from.x + (to.x - from.x) * t, from.y + (to.y - from.y) * t)
            .lineTo(from.x + (to.x - from.x) * end, from.y + (to.y - from.y) * end)
            .stroke({
              color: theme.muted,
              width: this.kind === 'topdown' ? 2 : 1.5,
              alpha: Math.max(0, 1 - age / 3),
            });
        }
      }
    const npc = scene.npcs.find((item) => item.id === this.hovered);
    const person = npc && this.people.get(npc.id);
    this.hover.visible = !!(npc && person?.container.visible);
    if (npc && person?.at && this.hover.visible) {
      const signature = JSON.stringify([npc.title, npc.harness, theme.floorAlt, theme.text]);
      if (signature !== this.hoverSignature) {
        this.hoverText.text = `${harnessLabel(npc.harness)} · ${npc.title}`;
        this.hoverText.style.fill = theme.text;
        this.hoverText.position.set(-this.hoverText.width / 2, -this.hoverText.height / 2);
        this.hoverBackground
          .clear()
          .roundRect(
            -this.hoverText.width / 2 - 6,
            -this.hoverText.height / 2 - 3,
            this.hoverText.width + 12,
            this.hoverText.height + 6,
            7,
          )
          .fill(theme.floorAlt);
        this.hoverSignature = signature;
      }
      this.hover.position.set(
        person.container.x,
        person.container.y - 58 * (this.kind === 'tower' ? layout.scale : 1),
      );
    }
  }
  destroy() {
    this.host?.removeEventListener('mousemove', this.move);
    this.host?.removeEventListener('mouseleave', this.leave);
    this.app.destroy(true, { children: true });
  }
}
