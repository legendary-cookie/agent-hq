/** The flat floor plan. It takes the same data as the 3D office and needs no graphics card. */
import type { EngineEvents, OfficeEngine } from './engine3d';
import { displayName, homeRoom, limited, words } from './model';
import type { Placed, Settings } from './model';

const W = 1200, H = 770, CORRIDOR = 295;
export interface FlatStyle {
  label: string; figure: 'blob' | 'token'; font: string; page: string; corridor: string; corridorLine: string; wall: string; line: string;
  floors: Record<RoomId, string>; desk: string; deskTop: string; chair: string; laptop: string; glow: string; ink: string; labelBg: string; labelInk: string; nameBg: string;
  bubble: string; bubbleInk: string; bubbleLine: string; table: string; tableTop: string; plant: string; pot: string; rack: string; led: string; soft: string; rug: string; bodies: string[]; round: number;
}
type RoomId = 'mgmt' | 'meet' | 'lounge' | 'server' | 'staff' | 'dept';
export const FLAT_STYLES: Record<string, FlatStyle> = {
  cute: {
    label: 'Cute', figure: 'blob', font: 'Fredoka', page: '#fbf3ea', corridor: '#f6ead9', corridorLine: '#efdfc9', wall: '#7c6a8e', line: 'rgba(124,106,142,.08)',
    floors: { mgmt: '#fde3e4', meet: '#dff1e4', lounge: '#fff0c9', server: '#dfe6f7', staff: '#e7e2fb', dept: '#d9f1f2' },
    desk: '#d9a877', deskTop: '#ecc293', chair: '#8d7aa8', laptop: '#5b5470', glow: '#bff3d9', ink: '#4a3f5c', labelBg: 'rgba(255,255,255,.88)', labelInk: '#4a3f5c', nameBg: 'rgba(255,255,255,.82)', bubble: '#ffffff', bubbleInk: '#4a3f5c', bubbleLine: 'rgba(74,63,92,.16)', table: '#b98a5e', tableTop: '#d9a877', plant: '#68b583', pot: '#d98c6a', rack: '#59557a', led: '#7cf0b2', soft: '#f39fae', rug: '#f7c98b',
    bodies: ['#ff9fb2', '#8fd3f4', '#ffc978', '#a8e6a3', '#c5a3ff', '#ffb38a', '#7fdccb', '#f5a3e0', '#9fb4ff', '#e6d36f'], round: 14,
  },
  studio: {
    label: 'Studio', figure: 'token', font: 'Geist Sans', page: '#ffffff', corridor: '#fafafa', corridorLine: '#f1f1f3', wall: '#141416', line: 'rgba(20,20,22,.035)',
    floors: { mgmt: '#ffffff', meet: '#f7f7f8', lounge: '#ffffff', server: '#f7f7f8', staff: '#ffffff', dept: '#ffffff' },
    desk: '#d9d9dd', deskTop: '#eeeef0', chair: '#c4c4ca', laptop: '#8b8b93', glow: '#141416', ink: '#141416', labelBg: 'rgba(255,255,255,0)', labelInk: '#6f6f77', nameBg: 'rgba(255,255,255,0)', bubble: '#141416', bubbleInk: '#ffffff', bubbleLine: 'rgba(0,0,0,0)', table: '#d9d9dd', tableTop: '#eeeef0', plant: '#c4c4ca', pot: '#d9d9dd', rack: '#8b8b93', led: '#16a34a', soft: '#e4e4e7', rug: '#f4f4f6',
    bodies: ['#141416'], round: 4,
  },
  night: {
    label: 'Night shift', figure: 'blob', font: 'Fredoka', page: '#0d1020', corridor: '#111529', corridorLine: '#171c35', wall: '#39406e', line: 'rgba(255,255,255,.02)',
    floors: { mgmt: '#1a1a38', meet: '#14233a', lounge: '#231d36', server: '#10182e', staff: '#171a36', dept: '#122333' },
    desk: '#2c3157', deskTop: '#3a4070', chair: '#23274a', laptop: '#191c38', glow: '#6ff0ff', ink: '#e9ebff', labelBg: 'rgba(16,20,44,.85)', labelInk: '#c4c9ef', nameBg: 'rgba(16,20,44,.8)', bubble: '#eef0ff', bubbleInk: '#191c38', bubbleLine: 'rgba(0,0,0,0)', table: '#2c3157', tableTop: '#3a4070', plant: '#2f8a6b', pot: '#4a4f7f', rack: '#262b52', led: '#6ff0ff', soft: '#463f7c', rug: '#2b2c5a',
    bodies: ['#ff8fb0', '#6fd3ff', '#ffc46f', '#7fe6a0', '#b794ff', '#ff9f7a', '#5fe0cc', '#f08fe0', '#8fa4ff', '#e6d05f'], round: 14,
  },
  blueprint: {
    label: 'Blueprint', figure: 'token', font: 'Geist Sans', page: '#123a7a', corridor: '#123a7a', corridorLine: '#123a7a', wall: '#d7e6ff', line: 'rgba(215,230,255,.07)',
    floors: { mgmt: '#144185', meet: '#144185', lounge: '#144185', server: '#144185', staff: '#144185', dept: '#144185' },
    desk: 'rgba(215,230,255,.35)', deskTop: 'rgba(215,230,255,.12)', chair: 'rgba(215,230,255,.3)', laptop: 'rgba(215,230,255,.6)', glow: '#ffffff', ink: '#ffffff', labelBg: 'rgba(0,0,0,0)', labelInk: '#d7e6ff', nameBg: 'rgba(0,0,0,0)', bubble: '#ffffff', bubbleInk: '#123a7a', bubbleLine: 'rgba(0,0,0,0)', table: 'rgba(215,230,255,.35)', tableTop: 'rgba(215,230,255,.12)', plant: 'rgba(215,230,255,.3)', pot: 'rgba(215,230,255,.3)', rack: 'rgba(215,230,255,.25)', led: '#ffffff', soft: 'rgba(215,230,255,.2)', rug: 'rgba(215,230,255,.06)',
    bodies: ['#ffffff'], round: 2,
  },
};
const ROOMS: Record<RoomId, { name: string; x: number; y: number; w: number; h: number; door: number }> = {
  mgmt: { name: 'Management', x: 30, y: 30, w: 330, h: 240, door: 195 },
  meet: { name: 'Meeting room', x: 380, y: 30, w: 320, h: 240, door: 540 },
  lounge: { name: 'Lounge', x: 720, y: 30, w: 220, h: 240, door: 830 },
  server: { name: 'Systems', x: 960, y: 30, w: 210, h: 240, door: 1065 },
  staff: { name: 'Staff room', x: 30, y: 320, w: 650, h: 420, door: 355 },
  dept: { name: 'Departments', x: 700, y: 320, w: 470, h: 420, door: 935 },
};
const isTop = (room: { y: number }) => room.y < 200;
const SEATS = {
  meeting: [[480, 118], [540, 118], [600, 118], [480, 222], [540, 222], [600, 222]],
  lounge: [[770, 158], [828, 162], [886, 158], [790, 226], [870, 226]],
};
function deskSlots() {
  const out: Record<'mgmt' | 'staff' | 'dept', [number, number][]> = { mgmt: [], staff: [], dept: [] };
  for (const y of [112, 208]) for (const x of [90, 195, 300]) out.mgmt.push([x, y]);
  for (const y of [392, 510, 628]) for (let i = 0; i < 6; i += 1) out.staff.push([92 + i * 105, y]);
  for (const y of [392, 510, 628]) for (let i = 0; i < 4; i += 1) out.dept.push([772 + i * 108, y]);
  return out;
}
const reduced = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

interface Actor { data: Placed; x: number; y: number; path: [number, number][]; seat: [number, number]; home: 'mgmt' | 'staff' | 'dept'; room: RoomId; where: Placed['where']; spot: number; phase: number; step: number; blinkAt: number; blinkUntil: number; landed: number; target?: [number, number]; nextWander?: number; lastPhone?: number | null; ringUntil?: number }

export function createOfficeFlat(canvas: HTMLCanvasElement, stage: HTMLElement, first: Settings, events: EngineEvents): OfficeEngine {
  const context = canvas.getContext('2d');
  if (!context) throw new Error('2D drawing isn’t available');
  const ctx = context;
  let settings = first;
  let actors: Actor[] = [];
  let selected: string | null = null;
  let time = 0, scale = 1, nextBubble = .4, frame = 0, last = performance.now();
  let hostsKey = '';
  const bubbles = new Map<Actor, { until: number; born: number }>();
  const planes: { from: Actor; to: Actor; t: number }[] = [];
  const style = () => FLAT_STYLES[settings.flatStyle] ?? FLAT_STYLES.cute;
  const roomName = (id: RoomId) => settings.roomNames[id === 'server' ? 'systems' : id] || ROOMS[id].name;

  function resize() {
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const fit = Math.min(stage.clientWidth / W, stage.clientHeight / H) || 1;
    const width = W * fit, height = H * fit;
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    scale = fit * ratio;
  }
  resize();
  const observer = new ResizeObserver(resize);
  observer.observe(stage);

  const rr = (x: number, y: number, w: number, h: number, r: number) => { ctx.beginPath(); ctx.roundRect(x, y, w, h, Math.min(r, w / 2, h / 2)); };
  const display = (a: Actor) => displayName(a.data, settings);
  const seated = (a: Actor) => !a.path.length;

  function seatAll(list: Placed[]) {
    const slots = deskSlots();
    const old = new Map(actors.map((a) => [a.data.host, a]));
    actors = list.map((data, index) => {
      const room = homeRoom(data, settings);
      let home: Actor['home'] = room === 'mgmt' ? 'mgmt' : room.startsWith('d-') ? 'dept' : 'staff';
      if (!slots[home].length) home = slots.staff.length ? 'staff' : slots.dept.length ? 'dept' : 'mgmt';
      const seat = slots[home].shift() ?? [ROOMS[home].x + 40 + (index % 8) * 30, ROOMS[home].y + 60];
      const before = old.get(data.host);
      return { data, x: seat[0], y: seat[1], path: [], seat, home, room: home, where: 'desk', spot: 0, phase: index * 1.7 % 6, step: 0, blinkAt: before?.blinkAt ?? 0, blinkUntil: 0, landed: 0 };
    });
    bubbles.clear();
    planes.length = 0;
  }
  function walkTo(a: Actor, x: number, y: number, room: RoomId, instant: boolean) {
    if (instant || !settings.walk || reduced) { a.x = x; a.y = y; a.path = []; a.room = room; return; }
    const from = ROOMS[a.room], to = ROOMS[room];
    const path: [number, number][] = [];
    if (from !== to) path.push([from.door, isTop(from) ? from.y + from.h - 8 : from.y + 8], [from.door, CORRIDOR], [to.door, CORRIDOR], [to.door, isTop(to) ? to.y + to.h - 8 : to.y + 8]);
    path.push([x, y]);
    a.path = path;
    a.room = room;
  }
  function sync(a: Actor, data: Placed, instant: boolean) {
    const taskBefore = a.data.task?.kind + '|' + a.data.task?.title;
    a.data = data;
    // A newer message from a person rings the desk phone. The first answer only sets the starting point.
    const heard = data.last_from_person ?? null;
    if (!instant && heard && a.lastPhone !== undefined && (a.lastPhone === null || heard > a.lastPhone)) a.ringUntil = time + 5;
    a.lastPhone = heard ?? a.lastPhone ?? null;
    // The floor plan has no archives room, so an agent fetching files stays at its desk.
    const where = data.where === 'archive' ? 'desk' : data.where;
    if (where !== a.where) {
      a.where = where;
      if (where === 'desk') walkTo(a, a.seat[0], a.seat[1], a.home, instant);
      else if (where === 'meeting') {
        // Chairs round the table; nobody shares one. If they're all taken, stand somewhere free.
        const taken = new Set(actors.filter((other) => other !== a && other.where === 'meeting').map((other) => other.spot));
        const spot = SEATS.meeting.findIndex((_, i) => !taken.has(i));
        a.spot = spot;
        a.target = spot >= 0 ? [SEATS.meeting[spot][0], SEATS.meeting[spot][1]] : freeSpot(a, 'meet');
        walkTo(a, a.target[0], a.target[1], 'meet', instant);
      } else {
        // The lounge has no fixed places: each agent picks a free spot, then wanders now and then.
        a.spot = -1;
        a.target = freeSpot(a, 'lounge');
        a.nextWander = time + 20 + Math.random() * 25;
        walkTo(a, a.target[0], a.target[1], 'lounge', instant);
      }
    }
    if (!instant && data.task && taskBefore !== data.task.kind + '|' + data.task.title) showBubble(a);
  }
  /** A free place in the lounge or meeting room, away from the furniture and at least 36 px from anyone else. */
  function freeSpot(a: Actor, room: 'lounge' | 'meet'): [number, number] {
    const area = room === 'lounge' ? { x0: 744, x1: 922, y0: 168, y1: 250 } : { x0: 400, x1: 680, y0: 205, y1: 255 };
    const others = actors.filter((o) => o !== a && o.room === room).map((o) => o.target ?? [o.x, o.y]);
    let best: [number, number] = [(area.x0 + area.x1) / 2, area.y1], bestGap = -1;
    for (let i = 0; i < 40; i += 1) {
      const x = area.x0 + Math.random() * (area.x1 - area.x0), y = area.y0 + Math.random() * (area.y1 - area.y0);
      const gap = Math.min(999, ...others.map(([ox, oy]) => Math.hypot(ox - x, oy - y)));
      if (gap >= 36) return [x, y];
      if (gap > bestGap) { best = [x, y]; bestGap = gap; }
    }
    return best;
  }
  function bubbleBox(a: Actor, text: string) {
    ctx.font = `500 13px ${style().font}, sans-serif`;
    const w = Math.min(230, ctx.measureText(text).width + 22);
    return { x: a.x - w / 2, y: a.y - 62, w, h: 26 };
  }
  const nameMap = () => new Map(actors.map((a) => [a.data.host, display(a)]));
  function showBubble(a: Actor, life = 6) {
    if (settings.bubbles === 'off' && a.data.host !== selected) return;
    const names = nameMap();
    const area = bubbleBox(a, words(a.data, settings, names));
    for (const other of bubbles.keys()) {
      if (other === a) continue;
      const b = bubbleBox(other, words(other.data, settings, names));
      if (area.x < b.x + b.w + 6 && b.x < area.x + area.w + 6 && area.y < b.y + b.h + 6 && b.y < area.y + area.h + 6) return;
    }
    bubbles.set(a, { until: time + life, born: bubbles.get(a)?.born ?? time });
  }
  function chooseBubble() {
    if (settings.bubbles === 'off' || bubbles.size >= (settings.bubbles === 'all' ? 9 : 5)) return;
    const pool = actors.filter((a) => seated(a) && !bubbles.has(a) && (settings.bubbles === 'all' || ['working', 'attention', 'meeting'].includes(a.data.shown)));
    if (pool.length) showBubble(pool[Math.floor(Math.random() * pool.length)]);
  }
  function simulate(dt: number) {
    time += dt;
    nextBubble -= dt;
    if (nextBubble <= 0) { chooseBubble(); nextBubble = 1.1; }
    if (settings.walk && !reduced) {
      for (const a of actors) {
        if (a.where === 'lounge' && seated(a) && a.nextWander && time > a.nextWander) {
          a.target = freeSpot(a, 'lounge');
          a.path = [a.target];
          a.nextWander = time + 25 + Math.random() * 35;
        }
      }
    }
    for (const a of actors) {
      if (a.path.length) {
        const [tx, ty] = a.path[0];
        const dx = tx - a.x, dy = ty - a.y;
        const dist = Math.hypot(dx, dy);
        const stride = 95 * dt;
        a.step += dt * 11;
        if (dist <= stride) { a.x = tx; a.y = ty; a.path.shift(); } else { a.x += (dx / dist) * stride; a.y += (dy / dist) * stride; }
      }
      if (time > a.blinkAt) { a.blinkAt = time + 2.5 + Math.random() * 4; a.blinkUntil = time + .13; }
    }
    for (const [a, entry] of bubbles) {
      if ((time > entry.until && a.data.host !== selected) || a.path.length) bubbles.delete(a);
    }
    for (const plane of [...planes]) {
      plane.t += dt / 1.9;
      if (plane.t >= 1) { plane.to.landed = time + .5; planes.splice(planes.indexOf(plane), 1); }
    }
  }
  function drawRoom(id: RoomId) {
    const s = style(), room = ROOMS[id];
    ctx.fillStyle = s.floors[id];
    rr(room.x, room.y, room.w, room.h, s.round);
    ctx.fill();
    ctx.save();
    ctx.clip();
    ctx.strokeStyle = s.line;
    ctx.lineWidth = 1;
    for (let x = room.x; x < room.x + room.w; x += 40) { ctx.beginPath(); ctx.moveTo(x, room.y); ctx.lineTo(x, room.y + room.h); ctx.stroke(); }
    for (let y = room.y; y < room.y + room.h; y += 40) { ctx.beginPath(); ctx.moveTo(room.x, y); ctx.lineTo(room.x + room.w, y); ctx.stroke(); }
    ctx.restore();
    ctx.strokeStyle = s.wall;
    ctx.lineWidth = s.figure === 'token' ? 2 : 5;
    rr(room.x, room.y, room.w, room.h, s.round);
    ctx.stroke();
    const doorY = isTop(room) ? room.y + room.h : room.y;
    ctx.strokeStyle = s.corridor;
    ctx.lineWidth = s.figure === 'token' ? 4 : 8;
    ctx.beginPath();
    ctx.moveTo(room.door - 26, doorY);
    ctx.lineTo(room.door + 26, doorY);
    ctx.stroke();
  }
  function plant(x: number, y: number) {
    const s = style();
    ctx.fillStyle = s.pot;
    rr(x - 9, y, 18, 15, 4);
    ctx.fill();
    ctx.fillStyle = s.plant;
    for (const [dx, dy, r] of [[0, -9, 10], [-8, -3, 7], [8, -3, 7]]) { ctx.beginPath(); ctx.arc(x + dx, y + dy, r, 0, 7); ctx.fill(); }
  }
  function furniture() {
    const s = style();
    ctx.fillStyle = s.soft;
    rr(610, 44, 76, 26, 4);
    ctx.fill();
    ctx.fillStyle = s.ink;
    ctx.globalAlpha = .35;
    for (let i = 0; i < 3; i += 1) { rr(618, 50 + i * 6, 34 + (i * 13) % 24, 2.5, 1); ctx.fill(); }
    ctx.globalAlpha = 1;
    ctx.fillStyle = s.rug;
    rr(748, 178, 164, 62, 20);
    ctx.fill();
    ctx.fillStyle = s.soft;
    rr(752, 112, 156, 44, 14);
    ctx.fill();
    ctx.fillStyle = s.desk;
    rr(892, 62, 34, 42, 6);
    ctx.fill();
    ctx.fillStyle = s.laptop;
    rr(899, 70, 20, 12, 3);
    ctx.fill();
    if (s.figure === 'blob') {
      ctx.strokeStyle = s.ink;
      ctx.globalAlpha = .35;
      ctx.lineWidth = 2;
      for (const dx of [-5, 3]) {
        ctx.beginPath();
        ctx.moveTo(909 + dx, 58);
        ctx.quadraticCurveTo(905 + dx + Math.sin(time * 2 + dx) * 2, 50, 909 + dx, 44);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }
    plant(742, 80);
    plant(342, 62);
    plant(48, 250);
    plant(660, 716);
    plant(1150, 716);
    for (let i = 0; i < 3; i += 1) {
      const x = 985 + i * 60, y = 88;
      ctx.fillStyle = s.rack;
      rr(x, y, 42, 128, 5);
      ctx.fill();
      for (let row = 0; row < 7; row += 1) {
        ctx.fillStyle = Math.sin(time * (1.1 + row * .31 + i) + row * 2) > -.2 ? s.led : 'rgba(127,127,127,.28)';
        ctx.beginPath();
        ctx.arc(x + 9, y + 12 + row * 16, 2.3, 0, 7);
        ctx.fill();
        ctx.fillStyle = 'rgba(127,127,127,.3)';
        rr(x + 16, y + 10 + row * 16, 18, 3.5, 1);
        ctx.fill();
      }
    }
  }
  function meetingTable() {
    const s = style();
    ctx.fillStyle = s.table;
    rr(446, 150, 188, 46, 16);
    ctx.fill();
    ctx.fillStyle = s.tableTop;
    rr(446, 146, 188, 44, 16);
    ctx.fill();
  }
  function drawDesk(x: number, y: number, on: boolean) {
    const s = style();
    ctx.fillStyle = s.desk;
    rr(x - 34, y + 16, 68, 25, 6);
    ctx.fill();
    ctx.fillStyle = s.deskTop;
    rr(x - 34, y + 12, 68, 21, 6);
    ctx.fill();
    ctx.fillStyle = s.laptop;
    rr(x - 13, y + 4, 26, 18, 3);
    ctx.fill();
    if (on) {
      ctx.fillStyle = s.glow;
      ctx.globalAlpha = .75 + Math.sin(time * 3 + x) * .2;
      ctx.beginPath();
      ctx.arc(x, y + 13, 2.6, 0, 7);
      ctx.fill();
      ctx.globalAlpha = 1;
    }
  }
  function drawChair(x: number, y: number) {
    ctx.fillStyle = style().chair;
    rr(x - 15, y - 28, 30, 36, 10);
    ctx.fill();
  }
  function accessory(kind: number, x: number, top: number, color: string) {
    const s = style();
    ctx.fillStyle = s.ink;
    ctx.strokeStyle = s.ink;
    ctx.lineWidth = 1.8;
    if (kind === 1) {
      ctx.beginPath(); ctx.moveTo(x, top + 2); ctx.lineTo(x, top - 7); ctx.stroke();
      ctx.fillStyle = color; ctx.beginPath(); ctx.arc(x, top - 9, 3, 0, 7); ctx.fill();
    } else if (kind === 2) {
      ctx.fillStyle = '#ff6f91';
      ctx.beginPath(); ctx.moveTo(x + 6, top + 4); ctx.lineTo(x + 14, top); ctx.lineTo(x + 14, top + 8); ctx.closePath(); ctx.fill();
      ctx.beginPath(); ctx.moveTo(x + 6, top + 4); ctx.lineTo(x - 2, top); ctx.lineTo(x - 2, top + 8); ctx.closePath(); ctx.fill();
    } else if (kind === 3) {
      ctx.fillStyle = '#6c7bd9';
      ctx.beginPath(); ctx.arc(x, top + 7, 12, Math.PI, 0); ctx.fill();
      rr(x, top + 4, 17, 4, 2); ctx.fill();
    } else if (kind === 4) {
      ctx.beginPath(); ctx.arc(x, top + 10, 14, Math.PI * 1.05, Math.PI * 1.95); ctx.stroke();
      rr(x - 16, top + 9, 4, 8, 2); ctx.fill();
      rr(x + 12, top + 9, 4, 8, 2); ctx.fill();
    } else if (kind === 5) {
      ctx.strokeStyle = '#4fa56f';
      ctx.beginPath(); ctx.moveTo(x, top + 2); ctx.quadraticCurveTo(x + 1, top - 3, x + 5, top - 6); ctx.stroke();
      ctx.fillStyle = '#68b583';
      ctx.beginPath(); ctx.ellipse(x + 7, top - 7, 4.5, 2.6, -.4, 0, 7); ctx.fill();
    }
  }
  function drawBlob(a: Actor) {
    const s = style();
    const walking = a.path.length > 0, shown = a.data.shown;
    const color = settings.agentColors[a.data.host] || s.bodies[a.data.index % s.bodies.length];
    const bob = walking ? Math.abs(Math.sin(a.step)) * 4 : shown === 'working' ? Math.sin(time * 5 + a.phase) * 1.2 : Math.sin(time * 1.4 + a.phase) * .8;
    const hop = a.landed > time && !reduced ? Math.sin((a.landed - time) / .5 * Math.PI) * 8 : 0;
    const x = a.x, base = a.y + 5, top = a.y - 25 - (reduced ? 0 : bob) - hop;
    ctx.fillStyle = 'rgba(40,30,60,.14)';
    ctx.beginPath();
    ctx.ellipse(x, base + 3, 15, 5, 0, 0, 7);
    ctx.fill();
    if (walking) {
      ctx.fillStyle = s.ink;
      const swing = Math.sin(a.step) * 5;
      rr(x - 9 + swing * .4, base - 3, 7, 6, 3); ctx.fill();
      rr(x + 2 - swing * .4, base - 3, 7, 6, 3); ctx.fill();
    }
    ctx.fillStyle = color;
    ctx.globalAlpha = shown === 'quiet' ? .55 : 1;
    rr(x - 15, top, 30, base - top, 15);
    ctx.fill();
    ctx.globalAlpha = 1;
    ctx.fillStyle = 'rgba(255,255,255,.28)';
    ctx.beginPath();
    ctx.ellipse(x - 5, top + 8, 5, 3.2, -.5, 0, 7);
    ctx.fill();
    const eyeY = top + 13;
    // Idle agents nap at their desks (unless switched off); quiet ones always sleep.
    const asleep = shown === 'quiet' || (settings.naps !== false && shown === 'idle' && a.where === 'desk' && !walking);
    ctx.strokeStyle = '#3a2f4a';
    ctx.fillStyle = '#3a2f4a';
    ctx.lineWidth = 1.7;
    ctx.lineCap = 'round';
    for (const dx of [-5.5, 5.5]) {
      ctx.beginPath();
      if (asleep || time < a.blinkUntil) { ctx.arc(x + dx, eyeY, 2.4, Math.PI * .15, Math.PI * .85); ctx.stroke(); } else { ctx.arc(x + dx, eyeY, 2.2, 0, 7); ctx.fill(); }
    }
    ctx.fillStyle = 'rgba(255,110,130,.34)';
    for (const dx of [-10, 10]) { ctx.beginPath(); ctx.ellipse(x + dx, eyeY + 4, 2.8, 1.8, 0, 0, 7); ctx.fill(); }
    ctx.beginPath();
    if (shown === 'attention') ctx.arc(x, eyeY + 8, 2.6, Math.PI * 1.15, Math.PI * 1.85);
    else ctx.arc(x, eyeY + 3, 2.6, Math.PI * .15, Math.PI * .85);
    ctx.stroke();
    accessory(a.data.index % 6, x, top, color);
    if (asleep) {
      // Three z's drift up and fade, slower for a deep sleep.
      ctx.fillStyle = s.ink;
      for (let i = 0; i < (reduced ? 1 : 3); i += 1) {
        const cycle = reduced ? .5 : ((time * (shown === 'quiet' ? .22 : .32) + i / 3 + a.phase) % 1);
        ctx.globalAlpha = Math.sin(cycle * Math.PI) * .6;
        ctx.font = `600 ${9 + cycle * 6}px ${s.font}, sans-serif`;
        ctx.fillText('z', x + 12 + cycle * 12, top - 2 - cycle * 18);
      }
      ctx.globalAlpha = 1;
    }
  }
  function drawToken(a: Actor) {
    const s = style();
    const x = a.x, y = a.y - 10, shown = a.data.shown;
    const ring = shown === 'working' || shown === 'meeting' ? '#16a34a' : shown === 'attention' ? '#dc2626' : null;
    ctx.globalAlpha = shown === 'quiet' ? .4 : 1;
    ctx.fillStyle = s.bodies[0];
    ctx.beginPath();
    ctx.arc(x, y, 14, 0, 7);
    ctx.fill();
    ctx.fillStyle = s.page === '#123a7a' ? '#123a7a' : '#ffffff';
    ctx.font = `600 10px ${s.font}, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(settings.names ? display(a).slice(0, 2).toUpperCase() : String(a.data.index + 1), x, y + .5);
    ctx.textAlign = 'left';
    ctx.textBaseline = 'alphabetic';
    ctx.globalAlpha = 1;
    if (ring) {
      ctx.strokeStyle = ring;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(x, y, 17, 0, 7);
      ctx.stroke();
    }
  }
  /** The ringing phone, the empty battery and the laptop smoke, drawn over everything else. */
  function signals(a: Actor) {
    const s = style();
    if ((a.ringUntil ?? 0) > time) {
      const [px, py] = [a.seat[0] + 22, a.seat[1] + 14];
      const buzz = reduced ? 0 : Math.sin(time * 40) * 1.5 * (Math.sin(time * 6) > 0 ? 1 : 0);
      ctx.fillStyle = s.ink;
      rr(px - 5 + buzz, py - 8, 10, 16, 2);
      ctx.fill();
      ctx.fillStyle = '#9fe6ff';
      rr(px - 3.5 + buzz, py - 6, 7, 10, 1);
      ctx.fill();
      ctx.strokeStyle = '#2fb66f';
      ctx.lineWidth = 1.8;
      const pulse = (time * 1.6) % 1;
      ctx.globalAlpha = 1 - pulse;
      for (const r of [9 + pulse * 6, 14 + pulse * 6]) { ctx.beginPath(); ctx.arc(px, py - 2, r, -Math.PI * .8, -Math.PI * .2); ctx.stroke(); }
      ctx.globalAlpha = 1;
    }
    if (limited(a.data) && seated(a)) {
      const x = a.x - 34, y = a.y - 40 + (reduced ? 0 : Math.sin(time * 2 + a.phase) * 1.5);
      ctx.fillStyle = '#ffffff';
      rr(x - 2, y - 2, 22, 14, 3);
      ctx.fill();
      ctx.strokeStyle = '#2d2438';
      ctx.lineWidth = 1.6;
      rr(x, y, 16, 10, 2);
      ctx.stroke();
      ctx.fillStyle = '#2d2438';
      ctx.fillRect(x + 16, y + 3, 2.5, 4);
      ctx.fillStyle = '#e5484d';
      ctx.fillRect(x + 2.5, y + 2.5, 3, 5);
    }
    if (a.data.api_error_at && a.where === 'desk' && seated(a)) {
      for (let i = 0; i < (reduced ? 1 : 3); i += 1) {
        const cycle = reduced ? .4 : (time * .5 + i / 3) % 1;
        ctx.fillStyle = `rgba(120,120,128,${Math.sin(cycle * Math.PI) * .55})`;
        ctx.beginPath();
        ctx.arc(a.seat[0] + Math.sin(cycle * 5 + i) * 4, a.seat[1] + 4 - cycle * 26, 3 + cycle * 6, 0, 7);
        ctx.fill();
      }
    }
  }
  function attentionMark(a: Actor) {
    if (a.data.shown !== 'attention') return;
    const x = a.x + 16, y = a.y - 28 + Math.sin(time * 4) * 1.5;
    ctx.fillStyle = '#ef4444';
    ctx.beginPath();
    ctx.arc(x, y, 8, 0, 7);
    ctx.fill();
    ctx.fillStyle = '#ffffff';
    ctx.font = `600 12px ${style().font}, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('!', x, y + .5);
    ctx.textAlign = 'left';
    ctx.textBaseline = 'alphabetic';
  }
  function label(text: string, x: number, y: number) {
    const s = style();
    ctx.font = `500 11px ${s.font}, sans-serif`;
    const w = ctx.measureText(text).width + 10;
    ctx.fillStyle = s.nameBg;
    rr(x - w / 2, y - 8, w, 15, 7);
    ctx.fill();
    ctx.fillStyle = s.labelInk;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, x, y);
    ctx.textAlign = 'left';
    ctx.textBaseline = 'alphabetic';
  }
  function roomLabels() {
    if (!settings.roomLabels) return;
    const s = style();
    ctx.font = `600 13px ${s.font}, sans-serif`;
    for (const id of Object.keys(ROOMS) as RoomId[]) {
      const room = ROOMS[id];
      const count = actors.filter((a) => a.room === id && seated(a)).length;
      const text = count && id !== 'server' ? `${roomName(id)} · ${count}` : roomName(id);
      const w = ctx.measureText(text).width + 16;
      ctx.fillStyle = s.labelBg;
      rr(room.x + 10, room.y + 9, w, 22, 8);
      ctx.fill();
      ctx.fillStyle = s.labelInk;
      ctx.textBaseline = 'middle';
      ctx.fillText(text, room.x + 18, room.y + 20.5);
      ctx.textBaseline = 'alphabetic';
    }
  }
  function drawBubble(a: Actor, entry: { born: number }, names: Map<string, string>) {
    const s = style();
    ctx.font = `500 13px ${s.font}, sans-serif`;
    let shown = words(a.data, settings, names);
    while (ctx.measureText(shown).width > 208 && shown.length > 4) shown = `${shown.slice(0, -2)}…`;
    const w = ctx.measureText(shown).width + 22;
    const pop = reduced ? 1 : Math.min(1, (time - entry.born) / .16);
    const x = Math.max(10 + w / 2, Math.min(W - 10 - w / 2, a.x));
    const y = a.y - 49 - (s.figure === 'token' ? -8 : 0);
    ctx.save();
    ctx.translate(x, y);
    ctx.scale(.8 + pop * .2, .8 + pop * .2);
    ctx.globalAlpha = pop;
    ctx.fillStyle = s.bubble;
    ctx.strokeStyle = s.bubbleLine;
    ctx.lineWidth = 1.5;
    rr(-w / 2, -13, w, 26, s.figure === 'blob' ? 13 : 6);
    ctx.fill();
    ctx.stroke();
    const tail = a.x - x;
    ctx.beginPath();
    ctx.moveTo(tail - 6, 12);
    ctx.lineTo(tail, 20);
    ctx.lineTo(tail + 6, 12);
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = s.bubbleInk;
    ctx.textBaseline = 'middle';
    ctx.fillText(shown, -w / 2 + 11, .5);
    ctx.textBaseline = 'alphabetic';
    ctx.restore();
  }
  function drawPlane(plane: { from: Actor; to: Actor; t: number }) {
    const s = style();
    const point = (t: number): [number, number] => {
      const e = t * t * (3 - 2 * t);
      return [plane.from.x + (plane.to.x - plane.from.x) * e, plane.from.y - 26 + (plane.to.y - plane.from.y) * e - Math.sin(t * Math.PI) * 50];
    };
    const [x, y] = point(plane.t);
    const [ax, ay] = point(Math.min(1, plane.t + .03));
    ctx.strokeStyle = s.ink;
    ctx.globalAlpha = .22;
    ctx.setLineDash([3, 6]);
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(plane.from.x, plane.from.y - 26);
    for (let t = .05; t < plane.t; t += .05) { const [px, py] = point(t); ctx.lineTo(px, py); }
    ctx.lineTo(x, y);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(Math.atan2(ay - y, ax - x));
    ctx.lineJoin = 'round';
    ctx.lineWidth = 1.2;
    ctx.strokeStyle = s.bubbleInk;
    ctx.fillStyle = s.bubble;
    for (const side of [-1, 1]) {
      ctx.beginPath();
      ctx.moveTo(13, 0); ctx.lineTo(-11, side * 9); ctx.lineTo(-6, 0); ctx.closePath();
      ctx.fill(); ctx.stroke();
    }
    ctx.globalAlpha = .28;
    ctx.fillStyle = s.bubbleInk;
    ctx.beginPath();
    ctx.moveTo(13, 0); ctx.lineTo(-6, 0); ctx.lineTo(-9, 4); ctx.closePath();
    ctx.fill();
    ctx.restore();
  }
  function draw() {
    const s = style();
    ctx.setTransform(scale, 0, 0, scale, 0, 0);
    ctx.lineCap = 'round';
    ctx.fillStyle = s.page;
    ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = s.corridor;
    ctx.fillRect(0, 270, W, 50);
    ctx.strokeStyle = s.corridorLine;
    ctx.setLineDash([14, 14]);
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(30, CORRIDOR);
    ctx.lineTo(1170, CORRIDOR);
    ctx.stroke();
    ctx.setLineDash([]);
    (Object.keys(ROOMS) as RoomId[]).forEach(drawRoom);
    furniture();
    // Everything that can overlap is drawn from back to front.
    const items: { y: number; draw: () => void }[] = [{ y: 172, draw: meetingTable }];
    const owners = new Map(actors.map((a) => [`${a.seat[0]},${a.seat[1]}`, a]));
    const slots = deskSlots();
    for (const room of ['mgmt', 'staff', 'dept'] as const) {
      for (const [x, y] of slots[room]) {
        const owner = owners.get(`${x},${y}`);
        items.push({ y: y - 28, draw: () => drawChair(x, y) });
        items.push({ y: y + 14, draw: () => drawDesk(x, y, !!owner && owner.where === 'desk' && owner.data.shown === 'working' && seated(owner)) });
      }
    }
    for (const a of actors) items.push({ y: a.y, draw: () => (s.figure === 'blob' ? drawBlob(a) : drawToken(a)) });
    items.sort((p, q) => p.y - q.y).forEach((item) => item.draw());
    if (settings.names) for (const a of actors) label(display(a), a.x, a.where === 'desk' && seated(a) ? a.y + 50 : a.y + 19);
    actors.forEach(attentionMark);
    actors.forEach(signals);
    roomLabels();
    planes.forEach(drawPlane);
    const chosen = actors.find((a) => a.data.host === selected);
    if (chosen) {
      ctx.strokeStyle = s.ink;
      ctx.lineWidth = 2;
      ctx.setLineDash([4, 5]);
      ctx.lineDashOffset = -time * 14;
      ctx.beginPath();
      ctx.arc(chosen.x, chosen.y - 10, 27, 0, 7);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.lineDashOffset = 0;
      if (!bubbles.has(chosen)) bubbles.set(chosen, { until: time + 3, born: time });
    }
    const names = nameMap();
    for (const [a, entry] of bubbles) drawBubble(a, entry, names);
  }
  const loop = (now: number) => {
    const dt = Math.max(0, Math.min(.05, (now - last) / 1000));
    last = now;
    simulate(dt);
    draw();
    frame = requestAnimationFrame(loop);
  };
  frame = requestAnimationFrame(loop);

  const nearest = (event: MouseEvent) => {
    const area = canvas.getBoundingClientRect();
    const x = ((event.clientX - area.left) / area.width) * W, y = ((event.clientY - area.top) / area.height) * H;
    let best: Actor | null = null, bestDistance = 26;
    for (const a of actors) {
      const distance = Math.hypot(a.x - x, a.y - 10 - y);
      if (distance < bestDistance) { best = a; bestDistance = distance; }
    }
    return best as Actor | null;
  };
  const onClick = (event: MouseEvent) => { const hit = nearest(event); events.onPick(hit ? { type: 'agent', host: hit.data.host } : null); };
  const onHover = (event: MouseEvent) => { canvas.style.cursor = nearest(event) ? 'pointer' : 'default'; };
  canvas.addEventListener('click', onClick);
  canvas.addEventListener('mousemove', onHover);
  const seating = () => JSON.stringify([settings.seating, settings.agentRoom]);
  let seatingKey = seating();
  let incoming: Placed[] = [];

  return {
    update(list) {
      incoming = list;
      const key = list.map((a) => a.host).join();
      if (key !== hostsKey) { hostsKey = key; seatAll(list); for (const a of actors) sync(a, a.data, true); return; }
      const byHost = new Map(actors.map((a) => [a.data.host, a]));
      for (const data of list) { const a = byHost.get(data.host); if (a) sync(a, data, false); }
    },
    configure(next) {
      settings = next;
      if (seating() !== seatingKey) { seatingKey = seating(); seatAll(incoming); for (const a of actors) sync(a, a.data, true); }
      if (settings.bubbles === 'off') bubbles.clear();
    },
    fly(from, to) {
      const a = actors.find((item) => item.data.host === from), b = actors.find((item) => item.data.host === to);
      if (a && b && a !== b && planes.length < 12) planes.push({ from: a, to: b, t: 0 });
    },
    select(host) {
      const before = actors.find((a) => a.data.host === selected);
      if (before) bubbles.delete(before);
      selected = host;
    },
    dispose() {
      cancelAnimationFrame(frame);
      observer.disconnect();
      canvas.removeEventListener('click', onClick);
      canvas.removeEventListener('mousemove', onHover);
    },
  };
}
