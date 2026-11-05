/** The 3D office. It draws what `update` says is true, and adds only idle motion of its own. */
import * as THREE from 'three';
import { CORRIDOR_Z, computeRooms, displayName, homeRoom, limited, timeOfDay, words } from './model';
import type { Placed, Room, Settings } from './model';
import { THEMES } from './themes';
import type { Theme } from './themes';

export type Pick3D = { type: 'agent'; host: string } | { type: 'room'; id: string } | null;
export interface EngineEvents {
  onPick(item: Pick3D): void;
  onMove(host: string, room: string): void;
  onToast(text: string): void;
}
export interface OfficeEngine {
  update(agents: Placed[]): void;
  configure(settings: Settings): void;
  fly(from: string, to: string): void;
  select(host: string | null): void;
  camera?: (view: { az: number; el: number } | 'in' | 'out') => void;
  dispose(): void;
}

interface Actor {
  data: Placed; index: number; host: string; phase: number; x: number; z: number; path: [number, number][]; step: number; heading: number;
  where: Placed['where']; at: string; room: string; seat: { x: number; z: number }; spot: number | null; faceSeat?: string;
  node: THREE.Group; parts: Parts; prop: Prop | null; pendingProp: boolean; folder: THREE.Group | null; landed: number; taskKey: string;
  tag: HTMLDivElement; say: HTMLDivElement; zs: THREE.Sprite[];
  target?: [number, number]; nextWander?: number;
  lastPhone?: number | null; ringUntil?: number; trouble: { battery: THREE.Sprite; smoke: THREE.Sprite[] };
}
interface Parts { eyes: THREE.Mesh[]; legs: THREE.Mesh[]; arms: THREE.Mesh[]; body?: THREE.Mesh; tail?: THREE.Mesh; tip?: THREE.Mesh }
// A prop's pieces differ by kind, and each is only read by the branch that made it.
interface Prop { group: THREE.Group; kind: string; [piece: string]: any } // eslint-disable-line @typescript-eslint/no-explicit-any
type Desk = Room['desks'][number] & { screen?: THREE.Mesh; phone?: { group: THREE.Group; face: THREE.Mesh; waves: THREE.Sprite }; lamp?: THREE.Sprite };
const VIEW = 64;
const CAMERA_HOME = { az: .62, el: .82, size: VIEW, tx: 0, tz: -.5 };
const reduced = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
const glow = (color: string, power = .55) => ({ emissive: new THREE.Color(color), emissiveIntensity: power });

export function createOffice3D(canvas: HTMLCanvasElement, overlay: HTMLElement, stage: HTMLElement, first: Settings, events: EngineEvents): OfficeEngine {
  let settings = first;
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  const scene = new THREE.Scene();
  const camera = new THREE.OrthographicCamera(-20, 20, 12, -12, .1, 300);
  const hemi = new THREE.HemisphereLight('#ffffff', '#d9c9b5', 1);
  const sun = new THREE.DirectionalLight('#fff4e0', 1);
  sun.position.set(18, 38, 22);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -36, right: 36, top: 30, bottom: -30, near: 1, far: 110 });
  sun.shadow.bias = -.0006;
  sun.shadow.radius = 3;
  const lamp = new THREE.PointLight('#ffd9a0', 0, 90);
  lamp.position.set(0, 16, 2);
  const office = new THREE.Group();
  scene.add(hemi, sun, lamp, office);

  const cam = { ...CAMERA_HOME };
  const goal = { ...CAMERA_HOME };
  let pixelsPerUnit = 20, clock = 0;
  let rooms: Record<string, Room> = {};
  let actors: Actor[] = [];
  let incoming: Placed[] = [];
  let selected: string | null = null;
  let buildKey = '';
  const pulse: { node: THREE.Mesh | THREE.Group; kind: string; y?: number; seed?: number; top?: number; bottom?: number }[] = [];
  const bubbles = new Map<Actor, number>();
  const flights: { node: THREE.Mesh; trail: THREE.Mesh[]; from: Actor; to: Actor; t: number; seconds: number }[] = [];
  const roomTags = new Map<string, HTMLDivElement>();
  const theme = (): Theme => THEMES[settings.theme] ?? THEMES.toybox;

  // Shapes
  const materials = new Map<string, THREE.MeshStandardMaterial>();
  const mat = (color: string, opts: THREE.MeshStandardMaterialParameters = {}) => {
    const key = color + JSON.stringify(opts);
    let found = materials.get(key);
    if (!found) { found = new THREE.MeshStandardMaterial({ color: new THREE.Color(color), roughness: .78, metalness: 0, ...opts }); materials.set(key, found); }
    return found;
  };
  const boxGeo = new THREE.BoxGeometry(1, 1, 1);
  const sphereGeo = new THREE.SphereGeometry(1, 20, 14);
  const cylGeo = new THREE.CylinderGeometry(1, 1, 1, 18);
  const box = (parent: THREE.Object3D, w: number, h: number, d: number, color: string, x = 0, y = 0, z = 0, opts?: THREE.MeshStandardMaterialParameters) => {
    const mesh = new THREE.Mesh(boxGeo, mat(color, opts));
    mesh.scale.set(w, h, d);
    mesh.position.set(x, y + h / 2, z);
    mesh.castShadow = !opts?.transparent;
    mesh.receiveShadow = true;
    parent.add(mesh);
    return mesh;
  };
  const ball = (parent: THREE.Object3D, r: number, color: string, x = 0, y = 0, z = 0, sy = 1, opts?: THREE.MeshStandardMaterialParameters) => {
    const mesh = new THREE.Mesh(sphereGeo, mat(color, opts));
    mesh.scale.set(r, r * sy, r);
    mesh.position.set(x, y, z);
    mesh.castShadow = true;
    parent.add(mesh);
    return mesh;
  };
  const cyl = (parent: THREE.Object3D, r: number, h: number, color: string, x = 0, y = 0, z = 0) => {
    const mesh = new THREE.Mesh(cylGeo, mat(color));
    mesh.scale.set(r, h, r);
    mesh.position.set(x, y + h / 2, z);
    mesh.castShadow = true;
    parent.add(mesh);
    return mesh;
  };
  const plant = (x: number, z: number, big = 1) => {
    const t = theme();
    cyl(office, .32 * big, .5 * big, t.pot, x, 0, z);
    for (const [dx, dy, dz, r] of [[0, 1, 0, .48], [-.28, .78, .08, .32], [.28, .8, -.08, .32], [.04, .72, .3, .28]]) ball(office, r * big, t.plant, x + dx * big, dy * big, z + dz * big);
  };

  // Rooms
  function walls(room: Room) {
    const t = theme();
    const h = { low: .75, mid: 1.5, high: 2.6 }[settings.wallHeight] ?? .75;
    const material = t.wallOpacity ? { transparent: true, opacity: t.wallOpacity } : {};
    const thick = .16, gap = 1.9;
    const x0 = room.x0, x1 = room.x0 + room.w, z0 = room.z0, z1 = room.z0 + room.d, mx = room.doorX;
    const line = (w: number, d: number, x: number, z: number) => box(office, w, h, d, t.wall, x, 0, z, material);
    const door = (zPos: number) => {
      line(mx - gap / 2 - x0, thick, (x0 + mx - gap / 2) / 2, zPos);
      line(x1 - mx - gap / 2, thick, (x1 + mx + gap / 2) / 2, zPos);
    };
    if (room.top) { door(z1); line(room.w, thick, x0 + room.w / 2, z0); } else { line(room.w, thick, x0 + room.w / 2, z1); door(z0); }
    line(thick, room.d, x0, z0 + room.d / 2);
    line(thick, room.d, x1, z0 + room.d / 2);
  }
  function desk(slot: Desk) {
    const t = theme();
    const group = new THREE.Group();
    group.position.set(slot.x, 0, slot.z);
    office.add(group);
    box(group, .75, .1, .7, t.chair, 0, .42, -.15);
    box(group, .75, .8, .1, t.chair, 0, .46, -.5);
    cyl(group, .06, .42, t.tech, 0, 0, -.15);
    box(group, 1.9, .1, .9, t.woodTop, 0, .72, .72);
    for (const [dx, dz] of [[-.85, .36], [.85, .36], [-.85, 1.08], [.85, 1.08]]) box(group, .09, .72, .09, t.wood, dx, 0, dz);
    box(group, .62, .04, .42, t.tech, 0, .82, .72);
    box(group, .62, .24, .03, t.tech, 0, .84, .95);
    const screen = box(group, .54, .18, .012, t.screen, 0, .87, .93, glow(t.screen, .1));
    screen.material = (screen.material as THREE.MeshStandardMaterial).clone();
    screen.castShadow = false;
    slot.screen = screen;
    if (!slot.owner) return;
    // A desk phone that rings when a person messages the agent.
    const phone = new THREE.Group();
    phone.position.set(-.68, .82, .62);
    box(phone, .2, .05, .3, '#2d2438');
    const face = box(phone, .16, .012, .24, '#9fe6ff', 0, .05, 0, glow('#9fe6ff', .05));
    face.material = (face.material as THREE.MeshStandardMaterial).clone();
    face.castShadow = false;
    const waves = new THREE.Sprite(new THREE.SpriteMaterial({ map: iconTexture('ring'), transparent: true, depthWrite: false, opacity: 0 }));
    waves.position.set(0, .35, 0);
    waves.scale.set(.6, .6, 1);
    phone.add(waves);
    group.add(phone);
    slot.phone = { group: phone, face, waves };
    // A warm desk lamp glow, shown at night while the agent works.
    const lamp = new THREE.Sprite(new THREE.SpriteMaterial({ map: iconTexture('glow'), transparent: true, depthWrite: false, opacity: 0, blending: THREE.AdditiveBlending }));
    lamp.position.set(0, 1.4, .7);
    lamp.scale.set(3.4, 3.4, 1);
    group.add(lamp);
    slot.lamp = lamp;
  }

  // Small drawn icons, made once and shared: ring waves, a warm glow, an empty battery and a smoke puff.
  const icons = new Map<string, THREE.CanvasTexture>();
  function iconTexture(kind: 'ring' | 'glow' | 'battery' | 'smoke') {
    const found = icons.get(kind);
    if (found) return found;
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 64;
    const ctx = canvas.getContext('2d');
    if (ctx) {
      ctx.lineCap = 'round';
      if (kind === 'ring') {
        ctx.strokeStyle = '#4fd38a';
        ctx.lineWidth = 5;
        for (const r of [14, 24]) {
          ctx.beginPath(); ctx.arc(32, 40, r, Math.PI * 1.2, Math.PI * 1.45); ctx.stroke();
          ctx.beginPath(); ctx.arc(32, 40, r, Math.PI * 1.55, Math.PI * 1.8); ctx.stroke();
        }
      } else if (kind === 'glow') {
        const g = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
        g.addColorStop(0, 'rgba(255,214,150,.55)');
        g.addColorStop(1, 'rgba(255,214,150,0)');
        ctx.fillStyle = g;
        ctx.fillRect(0, 0, 64, 64);
      } else if (kind === 'battery') {
        ctx.fillStyle = 'rgba(255,255,255,.95)';
        ctx.beginPath(); ctx.roundRect(6, 18, 48, 28, 6); ctx.fill();
        ctx.strokeStyle = '#2d2438'; ctx.lineWidth = 4;
        ctx.beginPath(); ctx.roundRect(8, 20, 42, 24, 5); ctx.stroke();
        ctx.fillStyle = '#2d2438'; ctx.fillRect(51, 27, 6, 10);
        ctx.fillStyle = '#e5484d'; ctx.fillRect(13, 25, 7, 14);
      } else {
        const g = ctx.createRadialGradient(32, 32, 0, 32, 32, 30);
        g.addColorStop(0, 'rgba(120,120,128,.85)');
        g.addColorStop(1, 'rgba(120,120,128,0)');
        ctx.fillStyle = g;
        ctx.beginPath(); ctx.arc(32, 32, 30, 0, Math.PI * 2); ctx.fill();
      }
    }
    const texture = new THREE.CanvasTexture(canvas);
    icons.set(kind, texture);
    return texture;
  }
  function makeTrouble() {
    const sprite = (kind: 'battery' | 'smoke') => {
      const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: iconTexture(kind), transparent: true, depthWrite: false, opacity: kind === 'battery' ? 1 : 0 }));
      s.visible = false;
      office.add(s);
      return s;
    };
    return { battery: sprite('battery'), smoke: [sprite('smoke'), sprite('smoke'), sprite('smoke')] };
  }
  type Seating = Room & { seats?: [number, number, string?][]; spots?: [number, number][] };
  function buildRoom(room: Seating) {
    const t = theme();
    const floor = box(office, room.w, .08, room.d, settings.roomColors[room.id] || t.floors[room.tint % t.floors.length], room.x0 + room.w / 2, 0, room.z0 + room.d / 2);
    floor.userData.room = room.id;
    floor.castShadow = false;
    walls(room);
    const cx = room.x0 + room.w / 2, cz = room.z0 + room.d / 2;
    if (room.id === 'meet') {
      box(office, room.w * .56, .1, 3.2, t.woodTop, cx, .78, cz);
      cyl(office, .35, .78, t.wood, cx - room.w * .2, 0, cz);
      cyl(office, .35, .78, t.wood, cx + room.w * .2, 0, cz);
      box(office, 3.2, 1.3, .08, t.tech, cx, .9, room.z0 + .25);
      box(office, 2.9, 1.05, .02, t.screen, cx, 1.02, room.z0 + .31, glow(t.screen, .35)).castShadow = false;
      room.seats = [-.28, 0, .28].flatMap((f) => [[cx + room.w * f * .8, cz - 2.3, 'south'], [cx + room.w * f * .8, cz + 2.3, 'north']] as [number, number, string][]);
      for (const [sx, sz] of room.seats) box(office, .7, .42, .7, t.chair, sx, 0, sz);
      plant(room.x0 + 1, room.z0 + 1);
    }
    if (room.id === 'lounge') {
      box(office, room.w * .72, .06, 4.2, t.rug, cx, .08, cz + .8);
      box(office, room.w * .62, .5, 1.1, t.sofa, cx, .06, room.z0 + 1.4);
      box(office, room.w * .62, .8, .35, t.sofa, cx, .06, room.z0 + .95);
      box(office, 1.5, .08, .8, t.woodTop, cx, .4, cz + 1);
      cyl(office, .12, .4, t.wood, cx, 0, cz + 1);
      box(office, .9, 1.5, .7, t.tech, room.x0 + room.w - 1, 0, room.z0 + .8);
      box(office, .5, .3, .05, t.screen, room.x0 + room.w - 1, 1, room.z0 + 1.16, glow(t.screen, .4));
      const steam = ball(office, .1, '#ffffff', room.x0 + room.w - 1, 1.75, room.z0 + 1, 1, { transparent: true, opacity: .5 });
      steam.material = (steam.material as THREE.MeshStandardMaterial).clone();
      steam.castShadow = false;
      pulse.push({ node: steam, kind: 'steam', y: 1.75 });
      plant(room.x0 + 1, room.z0 + room.d - 1, 1.15);
      room.seats = [[cx - room.w * .22, room.z0 + 2.6], [cx + room.w * .22, room.z0 + 2.6], [cx - room.w * .28, cz + 2.2], [cx + room.w * .28, cz + 2.2]];
    }
    if (room.id === 'archives') {
      const folders = ['#e8695f', '#f1b84b', '#58b368', '#4f8fe6', '#a673d9', '#ef8ab0'];
      for (let col = 0; col < 2; col += 1) {
        const sx = room.x0 + room.w * (col ? .72 : .28);
        box(office, 2.4, 2.2, .7, t.shelf, sx, 0, room.z0 + 1.1);
        for (let shelf = 0; shelf < 3; shelf += 1) for (let i = 0; i < 6; i += 1) box(office, .26, .5, .15, folders[(i * 3 + shelf + col) % 6], sx - .95 + i * .38, .22 + shelf * .68, room.z0 + 1.48);
      }
      box(office, room.w * .5, .6, .9, t.shelf, cx, 0, room.z0 + room.d - 2.4);
      const cloud = new THREE.Group();
      cloud.position.set(cx, 3.5, room.z0 + 2.2);
      for (const [dx, dy, r] of [[0, .12, .6], [-.62, 0, .42], [.62, 0, .46], [.28, .34, .38]]) ball(cloud, r, '#ffffff', dx, dy, 0, 1, glow('#ffffff', .12));
      office.add(cloud);
      pulse.push({ node: cloud, kind: 'float', y: 3.5 });
      room.spots = [[cx - 1.6, room.z0 + 2.4], [cx + 1.6, room.z0 + 2.4], [cx, room.z0 + 3.2]];
    }
    if (room.id === 'systems') {
      for (let i = 0; i < 2; i += 1) {
        const rx = room.x0 + room.w * (i ? .72 : .28);
        box(office, 1, 2.3, 2.6, t.tech, rx, 0, cz);
        for (let row = 0; row < 5; row += 1) {
          const led = box(office, .04, .08, .08, t.screen, rx + .52, .35 + row * .38, cz - .6 + (row % 3) * .55, glow(t.screen, 1));
          led.material = (led.material as THREE.MeshStandardMaterial).clone();
          led.castShadow = false;
          pulse.push({ node: led, kind: 'led', seed: row + i * 7 });
        }
      }
    }
    if (room.id === 'mgmt') plant(room.x0 + room.w - 1, room.z0 + 1);
    if (room.id === 'staff') { plant(room.x0 + 1, room.z0 + room.d - 1, 1.2); plant(room.x0 + room.w - 1, room.z0 + room.d - 1, 1.2); }
    for (const slot of room.desks) desk(slot);
  }

  // Characters
  const bodyColor = (host: string, index: number) => { const t = theme(); return settings.agentColors[host] || t.bodies[index % t.bodies.length]; };
  function makeCharacter(a: Placed): [THREE.Group, Parts] {
    const color = bodyColor(a.host, a.index);
    const group = new THREE.Group();
    const face = '#2d2438';
    const parts: Parts = { eyes: [], legs: [], arms: [] };
    const eye = (x: number, y: number, z: number, r = .075) => { const e = ball(group, r, face, x, y, z); e.castShadow = false; parts.eyes.push(e); };
    const feet = (spread: number, y: number, w: number, c = face) => { for (const side of [-1, 1]) parts.legs.push(box(group, w, .14, w * 1.25, c, side * spread, y, .04)); };
    const chosen = settings.agentChars[a.host] || settings.character;
    const kind = chosen === 'mix' ? ['blob', 'bot', 'person', 'critter'][a.index % 4] : chosen;
    if (kind === 'bot') {
      feet(.18, 0, .17, '#4b4b55');
      parts.body = box(group, .62, .5, .46, color, 0, .18, 0);
      box(group, .66, .42, .5, '#e7e9ee', 0, .72, 0);
      box(group, .5, .24, .03, '#20242e', 0, .82, .25).castShadow = false;
      for (const x of [-.12, .12]) { const e = ball(group, .055, '#6ff0ff', x, .94, .275, 1, glow('#6ff0ff', .8)); parts.eyes.push(e); e.castShadow = false; }
      cyl(group, .018, .2, '#4b4b55', 0, 1.14, 0);
      parts.tip = ball(group, .055, color, 0, 1.37, 0, 1, glow(color, .5));
      parts.tip.material = (parts.tip.material as THREE.MeshStandardMaterial).clone();
      for (const side of [-1, 1]) parts.arms.push(box(group, .12, .34, .12, '#b8bcc7', side * .4, .28, 0));
    } else if (kind === 'person') {
      const skin = ['#f2c9a5', '#e0ac82', '#c68a5f', '#8f5e3f', '#f6d5b8'][a.index % 5];
      const hair = ['#3a2a22', '#1f1a1a', '#8a5a33', '#c9973f', '#5a3f6f'][a.index % 5];
      for (const side of [-1, 1]) parts.legs.push(box(group, .15, .34, .17, '#3f4658', side * .12, 0, 0));
      parts.body = box(group, .46, .46, .3, color, 0, .32, 0);
      for (const side of [-1, 1]) parts.arms.push(box(group, .11, .38, .13, color, side * .3, .38, 0));
      ball(group, .27, skin, 0, 1.0, 0);
      ball(group, .285, hair, 0, 1.09, -.05, .82);
      eye(-.1, 1, .25, .04);
      eye(.1, 1, .25, .04);
    } else if (kind === 'critter') {
      feet(.2, 0, .18, color);
      parts.body = ball(group, .4, color, 0, .46, 0, 1.05);
      ball(group, .34, color, 0, .96, .06);
      for (const side of [-1, 1]) {
        const ear = new THREE.Mesh(new THREE.ConeGeometry(.12, .24, 8), mat(color));
        ear.position.set(side * .2, 1.3, .03);
        ear.castShadow = true;
        group.add(ear);
      }
      eye(-.13, 1, .35, .058);
      eye(.13, 1, .35, .058);
      ball(group, .04, '#ff7a93', 0, .9, .39, .7).castShadow = false;
      parts.tail = box(group, .09, .55, .09, color, 0, .3, -.42);
    } else {
      feet(.2, .02, .2);
      parts.body = ball(group, .46, color, 0, .62, 0, 1.2);
      eye(-.16, .78, .4);
      eye(.16, .78, .4);
      for (const side of [-1, 1]) ball(group, .07, '#ff7a93', side * .28, .65, .35, .6, { transparent: true, opacity: .55 }).castShadow = false;
      ball(group, .08, '#ffffff', -.18, .98, .32, .6, { transparent: true, opacity: .5 }).castShadow = false;
      const hat = a.index % 5;
      if (hat === 1) { cyl(group, .02, .24, face, 0, 1.15, 0); ball(group, .07, '#ff7a93', 0, 1.42, 0); }
      if (hat === 2) box(group, .34, .12, .1, '#ff6f91', .22, 1.1, .1);
      if (hat === 3) { ball(group, .34, '#6c7bd9', 0, 1.07, 0, .55); box(group, .36, .05, .3, '#6c7bd9', 0, 1, .28); }
      if (hat === 4) ball(group, .11, '#68b583', .05, 1.22, 0, .6);
    }
    group.traverse((node) => { node.userData.agent = a.host; });
    return [group, parts];
  }

  // Little "z"s that drift up from a sleeping agent. One shared drawing, one fading material each.
  let zTexture: THREE.CanvasTexture | null = null;
  function makeZs() {
    if (!zTexture) {
      const canvas = document.createElement('canvas');
      canvas.width = canvas.height = 64;
      const ctx = canvas.getContext('2d');
      if (ctx) {
        ctx.font = '700 52px Fredoka, sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.lineWidth = 7;
        ctx.strokeStyle = 'rgba(45,36,56,.75)';
        ctx.strokeText('z', 32, 34);
        ctx.fillStyle = '#ffffff';
        ctx.fillText('z', 32, 34);
      }
      zTexture = new THREE.CanvasTexture(canvas);
    }
    return [0, 1, 2].map(() => {
      const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: zTexture, transparent: true, depthWrite: false, opacity: 0 }));
      sprite.visible = false;
      office.add(sprite);
      return sprite;
    });
  }

  // A folded paper plane with its nose pointing forward: two wings over a centre keel.
  let planeGeo: THREE.BufferGeometry | null = null;
  function makePlane(size = 1) {
    if (!planeGeo) {
      const nose = [0, .02, .62], left = [-.46, .08, -.42], right = [.46, .08, -.42], fold = [0, .03, -.3], keel = [0, -.17, -.42];
      planeGeo = new THREE.BufferGeometry();
      planeGeo.setAttribute('position', new THREE.Float32BufferAttribute([nose, left, fold, nose, fold, right, nose, fold, keel, nose, keel, fold].flat(), 3));
      planeGeo.computeVertexNormals();
    }
    const mesh = new THREE.Mesh(planeGeo, mat('#ffffff', { side: THREE.DoubleSide, flatShading: true, ...glow('#ffffff', .12) }));
    mesh.castShadow = true;
    mesh.scale.setScalar(size);
    return mesh;
  }

  // Task animations: what's on the desk shows the kind of work.
  function makeProp(a: Actor) {
    if (a.prop) { office.remove(a.prop.group); a.prop = null; }
    const task = a.data.task;
    if (!task || a.data.shown !== 'working' || a.where !== 'desk') return;
    const group = new THREE.Group();
    const onDesk = task.kind === 'orders';
    group.position.set(a.seat.x + (onDesk ? 0 : 1.05), onDesk ? 0 : .15, a.seat.z + (onDesk ? 0 : .1));
    const prop: Prop = { group, kind: task.kind };
    if (task.kind === 'search') {
      const globe = new THREE.Group();
      globe.position.set(0, 2.05, .4);
      ball(globe, .36, '#5fb8ef', 0, 0, 0, 1, glow('#5fb8ef', .25));
      for (const [dx, dy, dz, r] of [[.2, .12, .24, .13], [-.22, -.05, .22, .11], [.04, -.22, .26, .1], [-.05, .24, -.24, .12]]) ball(globe, r, '#6fcf7f', dx, dy, dz);
      const lens = new THREE.Group();
      lens.add(new THREE.Mesh(new THREE.TorusGeometry(.17, .03, 8, 22), mat('#3a3a42')));
      box(lens, .05, .22, .05, '#3a3a42', .16, -.32, 0).rotation.z = .6;
      lens.add(new THREE.Mesh(new THREE.CircleGeometry(.15, 18), mat('#ffffff', { transparent: true, opacity: .4, side: THREE.DoubleSide })));
      group.add(globe, lens);
      prop.globe = globe;
      prop.lens = lens;
      prop.cards = [0, 1, 2].map((i) => {
        const card = box(group, .34, .22, .02, '#ffffff', 0, 0, 0, glow('#ffffff', .2));
        card.position.set(-.1 + i * .5, 1.35, .4); // Beside the globe, clear of the agent's head.
        return card;
      });
    } else if (task.kind === 'draft') {
      const sheet = new THREE.Group();
      sheet.position.set(0, 1.42, .5);
      sheet.rotation.x = .75;
      box(sheet, .62, .02, .78, '#ffffff', 0, 0, 0, glow('#ffffff', .15));
      prop.lines = [0, 1, 2, 3].map((i) => {
        const line = box(sheet, .46, .012, .04, '#5a5a66', 0, .022, -.24 + i * .16);
        line.castShadow = false;
        return line;
      });
      const pencil = new THREE.Group();
      box(pencil, .05, .34, .05, '#f1b84b', 0, .03, 0);
      const point = new THREE.Mesh(new THREE.ConeGeometry(.028, .09, 8), mat('#3a3a42'));
      point.position.y = -.015;
      point.rotation.x = Math.PI;
      pencil.add(point);
      pencil.rotation.z = -.6;
      sheet.add(pencil);
      group.add(sheet);
      prop.pencil = pencil;
      prop.stack = [0, 1, 2].map((i) => box(group, .4, .03, .52, '#ffffff', -.32, .68 + i * .04, .62));
    } else if (task.kind === 'read') {
      const book = new THREE.Group();
      book.position.set(0, 1.5, .6);
      book.rotation.x = .6;
      box(book, .82, .02, .54, '#b5553f', 0, -.022, 0);
      for (const side of [-1, 1]) {
        box(book, .38, .03, .48, '#fbf6ea', side * .2, 0, 0);
        for (let i = 0; i < 4; i += 1) box(book, .26, .004, .022, '#8a8478', side * .2, .031, -.15 + i * .1).castShadow = false;
      }
      const leaf = box(book, .38, .01, .47, '#ffffff', 0, .036, 0);
      // Its own shape, moved so the page turns on the spine rather than its middle.
      const hinged = new THREE.BoxGeometry(1, 1, 1);
      hinged.translate(.5, 0, 0);
      leaf.geometry = hinged;
      group.add(book);
      prop.leaf = leaf;
      prop.book = book;
    } else if (task.kind === 'orders') {
      prop.boxes = [0, 1, 2].map((i) => {
        const parcel = box(group, .34, .28, .34, '#c9975f', -.55 + i * .55, .82, .72);
        box(parcel, .2, .02, 1.02, '#e8c38f', 0, .5, 0).castShadow = false;
        return parcel;
      });
      const tick = new THREE.Group();
      box(tick, .07, .16, .05, '#3fbf6f', -.06, -.05, 0, glow('#3fbf6f', .6)).rotation.z = .8;
      box(tick, .07, .3, .05, '#3fbf6f', .05, -.05, 0, glow('#3fbf6f', .6)).rotation.z = -.6;
      group.add(tick);
      prop.tick = tick;
    } else if (task.kind === 'message') {
      prop.plane = makePlane(.72);
      group.add(prop.plane);
    } else if (task.kind === 'schedule') {
      const face = new THREE.Group();
      face.position.set(0, 1.95, .55);
      const dial = new THREE.Mesh(new THREE.CylinderGeometry(.34, .34, .06, 26), mat('#ffffff', glow('#ffffff', .2)));
      dial.rotation.x = Math.PI / 2;
      dial.castShadow = true;
      face.add(dial, new THREE.Mesh(new THREE.TorusGeometry(.34, .03, 8, 26), mat('#5b5470')));
      const hour = new THREE.Group(), minute = new THREE.Group();
      box(hour, .03, .17, .02, '#2d2438', 0, 0, .05);
      box(minute, .022, .26, .02, '#2d2438', 0, 0, .06);
      face.add(hour, minute);
      group.add(face);
      Object.assign(prop, { face, hour, minute });
    } else if (task.kind === 'command') {
      // A little terminal whose lines scroll as commands run
      const term = new THREE.Group();
      term.position.set(0, 1.55, .5);
      term.rotation.x = -.25;
      box(term, .8, .58, .04, '#1d2230', 0, -.29, 0);
      prop.rows = [0, 1, 2, 3].map(() => {
        const row = box(term, .5, .04, .012, '#6fe0a0', 0, 0, .03, glow('#6fe0a0', .7));
        row.castShadow = false;
        return row;
      });
      group.add(term);
    } else {
      // Dots that take turns, for thinking
      prop.dots = [-1, 0, 1].map((i) => ball(group, .1, '#ffffff', i * .28, 1.9, .3, 1, glow('#ffffff', .25)));
    }
    office.add(group);
    a.prop = prop;
  }
  function animateProp(a: Actor, t: number) {
    const p = a.prop;
    if (!p) return;
    const s = Math.max(0, t * (reduced ? .25 : 1) + a.phase);
    if (p.kind === 'search') {
      p.globe.rotation.y = s * .8;
      p.globe.position.y = 2.05 + Math.sin(s * 1.3) * .05;
      p.lens.position.set(Math.cos(s * 1.4) * .62, 2.05 + Math.sin(s * 2) * .18, .4 + Math.sin(s * 1.4) * .62);
      p.lens.lookAt(camera.position);
      p.cards.forEach((card: THREE.Mesh, i: number) => {
        const cycle = (s * .45 + i * .33) % 1;
        card.position.y = 1.3 + cycle * .55;
        const grow = Math.max(.01, Math.sin(cycle * Math.PI));
        card.scale.set(.34 * grow, .22 * grow, .02);
      });
    } else if (p.kind === 'draft') {
      const progress = (s * .22) % 1;
      const active = Math.floor(progress * 4);
      p.lines.forEach((line: THREE.Mesh, i: number) => {
        const fill = i < active ? 1 : i === active ? (progress * 4) % 1 : 0;
        line.scale.x = Math.max(.001, .46 * fill);
        line.position.x = -.23 + .23 * fill;
      });
      p.pencil.position.set(-.23 + .46 * ((progress * 4) % 1), .04 + Math.abs(Math.sin(s * 22)) * .012, -.24 + active * .16);
      p.stack.forEach((sheet: THREE.Mesh, i: number) => { sheet.visible = i <= Math.floor(s * .22) % 3; });
    } else if (p.kind === 'read') {
      const cycle = (s * .32) % 1;
      const move = Math.min(1, cycle / .55);
      p.leaf.rotation.z = move * move * (3 - 2 * move) * Math.PI;
      p.leaf.visible = cycle < .98;
      p.book.position.y = 1.5 + Math.sin(s * .8) * .025;
    } else if (p.kind === 'orders') {
      const active = Math.floor(s * .6) % 3;
      const hop = (s * .6) % 1;
      p.boxes.forEach((parcel: THREE.Mesh, i: number) => { parcel.position.y = .96 + (i === active ? Math.sin(hop * Math.PI) * .22 : 0); });
      p.tick.position.set(p.boxes[active].position.x, 1.35 + Math.sin(hop * Math.PI) * .25, .72);
      p.tick.scale.setScalar(Math.min(1, hop * 3));
    } else if (p.kind === 'message') {
      const cycle = (s * .3) % 1;
      const x = cycle * 2.2 - .4, y = .85 + Math.sin(cycle * Math.PI * .75) * 1.5, z = .55 + Math.sin(cycle * 5) * .22;
      p.plane.position.set(x, y, z);
      p.plane.lookAt(p.group.localToWorld(new THREE.Vector3(x + .4, y + Math.cos(cycle * Math.PI * .75) * .55, z + Math.cos(cycle * 5) * .2)));
      p.plane.rotateZ(Math.sin(s * 3) * .25);
      p.plane.scale.setScalar(.72 * (cycle > .88 ? Math.max(.01, (1 - cycle) * 8) : Math.min(1, cycle * 8 + .01)));
    } else if (p.kind === 'schedule') {
      p.minute.rotation.z = -s * 3;
      p.hour.rotation.z = -s * .4;
      p.face.position.y = 1.95 + Math.sin(s * 1.4) * .05;
      p.face.rotation.y = Math.sin(s * .7) * .3;
    } else if (p.kind === 'command') {
      const shift = (s * 1.4) % 1;
      p.rows.forEach((row: THREE.Mesh, i: number) => {
        const place = (i + shift) % 4;
        row.position.y = -.5 + place * .11;
        row.position.x = -.14 + ((i * 7) % 3) * .03;
        row.scale.x = .28 + ((i * 5) % 4) * .07;
        row.visible = place > .1;
      });
    } else if (p.kind === 'think') {
      p.dots.forEach((dot: THREE.Mesh, i: number) => {
        const size = .07 + Math.max(0, Math.sin(s * 3 - i * .9)) * .05;
        dot.scale.setScalar(size);
      });
    }
  }

  // Movement
  function exitRoute(room?: Room): [number, number][] {
    if (!room) return [];
    if (room.top) return [[room.doorX, room.z0 + room.d - .4], [room.doorX, CORRIDOR_Z]];
    if (room.z0 <= -3 + .1) return [[room.doorX, room.z0 + .4], [room.doorX, CORRIDOR_Z]];
    const aisle = room.z0 - .5, side = room.x0 - .5;
    return [[room.doorX, room.z0 + .4], [room.doorX, aisle], [side, aisle], [side, CORRIDOR_Z]];
  }
  function walkTo(a: Actor, x: number, z: number, roomId: string, instant: boolean) {
    const from = rooms[a.at], to = rooms[roomId];
    if (instant || !settings.walk || reduced) { a.x = x; a.z = z; a.path = []; a.at = roomId; return; }
    a.path = from === to ? [[x, z]] : [...exitRoute(from), ...exitRoute(to).reverse(), [x, z]];
    a.at = roomId;
  }
  const seated = (a: Actor) => !a.path.length;
  const SPACE = 1.6; // how close two standing agents may get

  /** A spot in the room clear of its furniture and at least SPACE from everyone else heading there. */
  function freeSpot(a: Actor, room: Room): [number, number] {
    const cx = room.x0 + room.w / 2, cz = room.z0 + room.d / 2;
    // Areas each room's furniture fills: [x0, z0, x1, z1].
    const blocked: number[][] = room.id === 'lounge'
      ? [[room.x0, room.z0, room.x0 + room.w, room.z0 + 2.2], [cx - 1.1, cz + .4, cx + 1.1, cz + 1.6], [room.x0, room.z0 + room.d - 2, room.x0 + 2, room.z0 + room.d]]
      : room.id === 'archives'
        ? [[room.x0, room.z0, room.x0 + room.w, room.z0 + 2], [cx - room.w * .27, room.z0 + room.d - 3.1, cx + room.w * .27, room.z0 + room.d]]
        : [[cx - room.w * .3, cz - 1.9, cx + room.w * .3, cz + 1.9], [room.x0, room.z0, room.x0 + room.w, room.z0 + 1.2]];
    const others = actors.filter((o) => o !== a && o.at === room.id).map((o) => o.target ?? [o.x, o.z]);
    let best: [number, number] = [cx, cz + 2], bestGap = -1;
    for (let i = 0; i < 40; i += 1) {
      const x = room.x0 + .9 + Math.random() * (room.w - 1.8);
      const z = room.z0 + .9 + Math.random() * (room.d - 1.8);
      if (blocked.some(([x0, z0, x1, z1]) => x > x0 && x < x1 && z > z0 && z < z1)) continue;
      const gap = Math.min(99, ...others.map(([ox, oz]) => Math.hypot(ox - x, oz - z)));
      if (gap >= SPACE) return [x, z];
      if (gap > bestGap) { best = [x, z]; bestGap = gap; }
    }
    return best; // A crowded room: the roomiest place found.
  }

  /** Move one agent to where the data now says it is. */
  function sync(a: Actor, data: Placed, instant: boolean) {
    const key = data.task ? `${data.task.kind}|${data.task.title}|${data.shown}` : data.shown;
    const moved = a.where !== data.where;
    const changed = key !== a.taskKey;
    a.data = data;
    a.taskKey = key;
    a.tag.textContent = displayName(data, settings);
    // A newer message from a person rings the desk phone. The first answer only sets the starting point.
    const heard = data.last_from_person ?? null;
    if (!instant && heard && a.lastPhone !== undefined && (a.lastPhone === null || heard > a.lastPhone)) a.ringUntil = clock + 5;
    a.lastPhone = heard ?? a.lastPhone ?? null;
    if (moved) {
      a.where = data.where;
      if (a.folder) { a.node.remove(a.folder); a.folder = null; }
      if (data.where === 'desk') {
        a.spot = null;
        walkTo(a, a.seat.x, a.seat.z, a.room, instant);
        a.pendingProp = !instant;
      } else {
        const room = rooms[data.where === 'meeting' ? 'meet' : data.where === 'lounge' ? 'lounge' : 'archives'] as Seating | undefined;
        if (room && data.where === 'meeting' && room.seats) {
          // Chairs round the table; nobody shares one. If they're all taken, stand somewhere free.
          const taken = new Set(actors.filter((other) => other !== a && other.where === 'meeting').map((other) => other.spot));
          const spot = room.seats.findIndex((_, i) => !taken.has(i));
          const seat = spot >= 0 ? room.seats[spot] : [...freeSpot(a, room), undefined];
          a.spot = spot >= 0 ? spot : null;
          a.faceSeat = (seat as [number, number, string?])[2];
          a.target = [seat[0] as number, seat[1] as number];
          walkTo(a, seat[0] as number, seat[1] as number, room.id, instant);
        } else if (room) {
          // The lounge and archives have no fixed places: each agent picks a free spot, then wanders now and then.
          const [x, z] = freeSpot(a, room);
          a.spot = null;
          a.faceSeat = undefined;
          a.target = [x, z];
          a.nextWander = clock + 20 + Math.random() * 25;
          walkTo(a, x, z, room.id, instant);
        }
        if (data.where === 'archive') {
          a.folder = new THREE.Group();
          box(a.folder, .42, .32, .05, '#f1b84b', 0, 0, 0);
          a.folder.position.set(0, .68, .52);
          a.node.add(a.folder);
        }
      }
      if (a.prop) { office.remove(a.prop.group); a.prop = null; }
    }
    if ((changed || moved) && seated(a) && a.where === 'desk') {
      makeProp(a);
      if (!instant && data.shown === 'working') showBubble(a);
    }
  }

  // Name tags and speech bubbles float over the 3D view, so they stay sharp at any zoom.
  const div = (className: string) => { const node = document.createElement('div'); node.className = className; return node; };
  function rebuildTags() {
    overlay.textContent = '';
    roomTags.clear();
    const t = theme();
    const fontFamily = `${t.font}, sans-serif`;
    for (const a of actors) {
      a.tag = div('hqo-tag');
      a.say = div('hqo-say');
      a.say.hidden = true;
      a.tag.textContent = displayName(a.data, settings);
      Object.assign(a.tag.style, { background: t.tag, color: t.tagInk, fontFamily });
      Object.assign(a.say.style, { background: settings.theme === 'clay' ? '#141416' : '#ffffff', color: settings.theme === 'clay' ? '#ffffff' : '#2d2438', fontFamily });
      overlay.append(a.tag, a.say);
    }
    for (const room of Object.values(rooms)) {
      const tag = div('hqo-room-tag');
      tag.textContent = room.name;
      Object.assign(tag.style, { background: t.tag, color: t.tagInk, fontFamily });
      overlay.append(tag);
      roomTags.set(room.id, tag);
    }
  }
  const showBubble = (a: Actor, life = 6) => { if (settings.bubbles !== 'off') bubbles.set(a, clock + life); };
  function chooseBubble() {
    if (settings.bubbles === 'off') return;
    if (bubbles.size >= (settings.bubbles === 'all' ? 9 : 5)) return;
    const pool = actors.filter((a) => seated(a) && !bubbles.has(a) && (settings.bubbles === 'all' || ['working', 'attention', 'archive', 'meeting'].includes(a.data.shown)));
    if (pool.length) showBubble(pool[Math.floor(Math.random() * pool.length)]);
  }
  const projected = new THREE.Vector3();
  const toScreen = (x: number, y: number, z: number, w: number, h: number): [number, number] => {
    projected.set(x, y, z).project(camera);
    return [(projected.x + 1) / 2 * w, (1 - projected.y) / 2 * h];
  };
  function updateOverlay() {
    const w = stage.clientWidth, h = stage.clientHeight;
    const scale = Math.max(.72, Math.min(1.2, VIEW / cam.size));
    const occupied: number[][] = [];
    const names = new Map(actors.map((a) => [a.host, displayName(a.data, settings)]));
    for (const a of actors) {
      // Zoomed out, a department's room label already names its agent.
      const showName = settings.names && pixelsPerUnit > 8 && (a.data.group !== 'Department' || pixelsPerUnit > 32);
      a.tag.hidden = !showName;
      if (showName) {
        const [sx, sy] = toScreen(a.x, 2.15, a.z, w, h);
        a.tag.style.transform = `translate(${sx}px, ${sy}px) translate(-50%, -100%) scale(${scale})`;
      }
      const on = bubbles.has(a) || selected === a.host;
      a.say.hidden = !on;
      if (on) {
        const text = words(a.data, settings, names);
        if (a.say.textContent !== text) a.say.textContent = text;
        const [sx, sy] = toScreen(a.x, settings.names ? 3.05 : 2.3, a.z, w, h);
        a.say.style.transform = `translate(${sx}px, ${sy}px) translate(-50%, -100%)`;
        const area = [sx - 80, sy - 26, sx + 80, sy];
        const hit = occupied.some((b) => area[0] < b[2] && b[0] < area[2] && area[1] < b[3] && b[1] < area[3]);
        if (hit && selected !== a.host) a.say.hidden = true;
        else occupied.push(area);
      }
    }
    for (const room of Object.values(rooms)) {
      const tag = roomTags.get(room.id);
      if (!tag) continue;
      tag.hidden = !settings.roomLabels;
      if (settings.roomLabels) {
        const [sx, sy] = toScreen(room.x0 + room.w / 2, 1.3, room.z0, w, h);
        tag.style.transform = `translate(${sx}px, ${sy}px) translate(-50%, -50%) scale(${Math.min(1, scale)})`;
      }
    }
  }

  // Building
  function build() {
    const t = theme();
    pulse.length = 0;
    flights.splice(0).forEach((flight) => { office.remove(flight.node); flight.trail.forEach((dot) => office.remove(dot)); });
    bubbles.clear();
    while (office.children.length) office.remove(office.children[0]);
    materials.forEach((m) => m.dispose());
    materials.clear();
    planeGeo = null;
    rooms = computeRooms(incoming, settings);
    box(office, 51, .5, 34, t.slab, 0, -.5, -.5).castShadow = false;
    box(office, 49, .05, 3, t.corridor, 0, 0, CORRIDOR_Z).castShadow = false;
    Object.values(rooms).forEach(buildRoom);
    actors = incoming.map((data) => {
      const room = homeRoom(data, settings);
      const slot = Object.values(rooms).flatMap((r) => r.desks).find((d) => d.owner === data.host) ?? { x: 0, z: 0 };
      const home = Object.values(rooms).find((r) => r.desks.some((d) => d.owner === data.host));
      const [node, parts] = makeCharacter(data);
      node.scale.setScalar(1.4);
      node.position.set(slot.x, .04, slot.z);
      office.add(node);
      return {
        data, index: data.index, host: data.host, phase: data.index * 1.7 % 6, x: slot.x, z: slot.z, path: [], step: 0, heading: 0, where: 'desk', at: home?.id ?? room, room: home?.id ?? room,
        seat: slot, spot: null, node, parts, prop: null, pendingProp: false, folder: null, landed: 0, taskKey: '', tag: div('hqo-tag'), say: div('hqo-say'), zs: makeZs(), trouble: makeTrouble(),
      } as Actor;
    });
    rebuildTags();
    for (const a of actors) sync(a, a.data, true);
    applyTime();
  }
  let light: 'day' | 'golden' | 'night' = 'day';
  function applyTime() {
    const t = theme();
    light = timeOfDay(settings.time);
    const looks = { day: { hemi: .72, sun: .42, color: '#fff4e0', dark: 0 }, golden: { hemi: .5, sun: .6, color: '#ffb46b', dark: .18 }, night: { hemi: .44, sun: .2, color: '#a8bbff', dark: .62 } };
    const look = looks[light] ?? looks.day;
    hemi.intensity = look.hemi;
    sun.intensity = look.sun;
    sun.color.set(look.color);
    lamp.intensity = light === 'night' ? .55 : 0;
    scene.background = new THREE.Color(t.bg).lerp(new THREE.Color('#0a0d1c'), look.dark);
  }
  const structure = (s: Settings, list: Placed[]) => JSON.stringify([s.theme, s.character, s.wallHeight, s.seating, s.hidden, s.roomColors, s.roomNames, s.agentRoom, s.agentColors, s.agentChars, list.map((a) => a.host)]);

  // Each frame
  function simulate(dt: number) {
    clock += dt;
    nextBubble -= dt;
    // Agents on a break stroll to another free spot now and then.
    if (settings.walk && !reduced) {
      for (const a of actors) {
        if ((a.where === 'lounge' || a.where === 'archive') && seated(a) && a.nextWander && clock > a.nextWander) {
          const room = rooms[a.at];
          if (room) {
            const [x, z] = freeSpot(a, room);
            a.target = [x, z];
            a.path = [[x, z]];
          }
          a.nextWander = clock + 25 + Math.random() * 35;
        }
      }
    }
    if (nextBubble <= 0) { chooseBubble(); nextBubble = 1.1; }
    for (const [a, until] of bubbles) if (clock > until || a.path.length) bubbles.delete(a);
    for (const a of actors) {
      if (!a.path.length) continue;
      const [tx, tz] = a.path[0];
      const dx = tx - a.x, dz = tz - a.z;
      const dist = Math.hypot(dx, dz);
      const stride = 4.2 * dt;
      a.step += dt * 12;
      a.heading = Math.atan2(dx, dz);
      if (dist <= stride) { a.x = tx; a.z = tz; a.path.shift(); } else { a.x += dx / dist * stride; a.z += dz / dist * stride; }
      if (!a.path.length && a.pendingProp) { a.pendingProp = false; if (a.where === 'desk') makeProp(a); }
    }
    for (const flight of [...flights]) {
      flight.t += dt / flight.seconds;
      if (flight.t >= 1) {
        office.remove(flight.node);
        flight.trail.forEach((dot) => office.remove(dot));
        flight.to.landed = clock + .55;
        flights.splice(flights.indexOf(flight), 1);
        continue;
      }
      const at = (t: number) => {
        const e = t * t * (3 - 2 * t);
        return new THREE.Vector3(flight.from.x + (flight.to.x - flight.from.x) * e, 1.9 + Math.sin(t * Math.PI) * 4.2, flight.from.z + (flight.to.z - flight.from.z) * e);
      };
      flight.node.position.copy(at(flight.t));
      flight.node.lookAt(at(Math.min(1, flight.t + .02)));
      flight.node.rotateZ(Math.sin(flight.t * Math.PI * 4) * .3);
      flight.trail.forEach((dot, i) => {
        const behind = flight.t - (i + 1) * .028;
        dot.visible = behind > 0;
        if (behind > 0) { dot.position.copy(at(behind)); dot.scale.setScalar(.075 * (1 - i / 8)); }
      });
    }
  }
  let dragging: Actor | null = null;
  function animate(t: number) {
    for (const a of actors) {
      if (dragging === a) continue;
      const node = a.node, walking = a.path.length > 0, shown = a.data.shown;
      node.position.x = a.x;
      node.position.z = a.z;
      const working = shown === 'working' && !walking;
      const hop = reduced ? 0 : walking ? Math.abs(Math.sin(a.step)) * .16 : working ? Math.abs(Math.sin(t * 6 + a.phase)) * .025 : Math.sin(t * 1.3 + a.phase) * .012;
      const landed = !reduced && a.landed > clock ? Math.sin((a.landed - clock) / .55 * Math.PI) * .3 : 0;
      node.position.y = .04 + hop + landed;
      const turn = walking ? a.heading : a.where === 'meeting' && a.faceSeat === 'north' ? Math.PI : 0;
      node.rotation.y += (turn - node.rotation.y) * .18;
      // Idle agents nap at their desks; quiet ones, with nothing for days, sleep more deeply.
      const sleeping = settings.naps !== false && (shown === 'idle' || shown === 'quiet') && a.where === 'desk' && !walking;
      const deep = shown === 'quiet';
      const breath = sleeping && !reduced ? Math.sin(t * (deep ? 1.1 : 1.5) + a.phase) : 0;
      node.scale.set(1.4 * (1 + breath * .012), 1.4 * (1 + breath * .03), 1.4 * (1 + breath * .012));
      node.rotation.z = walking && !reduced ? Math.sin(a.step) * .06 : sleeping ? (deep ? .16 : .08) + breath * .015 : shown === 'quiet' ? .1 : 0;
      node.rotation.x = sleeping ? (deep ? .22 : .12) + (reduced ? 0 : Math.max(0, Math.sin(t * .45 + a.phase * 2)) * .06) : 0;
      a.zs.forEach((z, i) => {
        z.visible = sleeping && (!reduced || i === 0);
        if (!z.visible) return;
        const cycle = reduced ? .5 : ((t * (deep ? .22 : .32) + i / 3 + a.phase) % 1);
        const size = (deep ? .38 : .28) + cycle * (deep ? .4 : .3);
        z.position.set(a.x + .35 + cycle * .55 + Math.sin(cycle * 6 + i) * .08, 2.0 + cycle * (deep ? 1.5 : 1.2), a.z + .1);
        z.scale.set(size, size, 1);
        (z.material as THREE.SpriteMaterial).opacity = Math.sin(cycle * Math.PI) * .95;
      });
      const { legs, arms, eyes, tail, tip } = a.parts;
      legs.forEach((leg, i) => { leg.position.z = walking ? Math.sin(a.step + i * Math.PI) * .14 + .04 : .04; });
      arms.forEach((arm, i) => { arm.rotation.x = walking ? Math.sin(a.step + i * Math.PI) * .7 : working ? -1.1 + Math.sin(t * 16 + i * 3) * .1 : sleeping ? .25 : 0; });
      const blink = Math.sin(t * .9 + a.phase * 3) > .985 || shown === 'quiet' || sleeping;
      eyes.forEach((eye) => { eye.scale.y = (blink ? .15 : 1) * eye.scale.x; });
      if (tail) tail.rotation.x = -.5 + Math.sin(t * 4 + a.phase) * .2;
      if (tip) (tip.material as THREE.MeshStandardMaterial).emissiveIntensity = .3 + Math.abs(Math.sin(t * 3 + a.phase)) * .7;
      if (shown === 'attention') node.rotation.y = Math.sin(t * 3) * .18;
      animateProp(a, t);
    }
    for (const room of Object.values(rooms)) {
      for (const slot of room.desks as Desk[]) {
        if (!slot.screen) continue;
        const owner = actors.find((a) => a.host === slot.owner);
        const on = owner && owner.data.shown === 'working' && owner.where === 'desk' && seated(owner);
        (slot.screen.material as THREE.MeshStandardMaterial).emissiveIntensity = on ? .85 + Math.sin(t * 4 + slot.x) * .2 : .03;
        // At night a working desk keeps its lamp on; the rest of the floor goes dark.
        if (slot.lamp) (slot.lamp.material as THREE.SpriteMaterial).opacity = light === 'night' && on ? .9 : light === 'golden' && on ? .35 : 0;
        if (slot.phone && owner) {
          const ringing = (owner.ringUntil ?? 0) > clock;
          const buzz = ringing && !reduced ? Math.sin(t * 42) * .12 * (Math.sin(t * 6) > 0 ? 1 : 0) : 0;
          slot.phone.group.rotation.y = buzz;
          slot.phone.group.position.y = .82 + Math.abs(buzz) * .15;
          (slot.phone.face.material as THREE.MeshStandardMaterial).emissiveIntensity = ringing ? .6 + Math.abs(Math.sin(t * 6)) * .8 : .05;
          const pulse = ringing ? (t * 1.6) % 1 : 0;
          (slot.phone.waves.material as THREE.SpriteMaterial).opacity = ringing ? 1 - pulse : 0;
          slot.phone.waves.scale.setScalar(.5 + pulse * .5);
        }
      }
    }
    for (const a of actors) {
      // Held up by a usage limit: an empty battery over its head. A recent API error: smoke from its laptop.
      const { battery, smoke } = a.trouble;
      const stuck = limited(a.data) && !a.path.length;
      battery.visible = stuck;
      if (stuck) {
        battery.position.set(a.x + .55, 2.55 + (reduced ? 0 : Math.sin(t * 2 + a.phase) * .06), a.z);
        battery.scale.set(.7, .7, 1);
      }
      const fuming = !!a.data.api_error_at && a.where === 'desk' && seated(a);
      smoke.forEach((puff, i) => {
        puff.visible = fuming && (!reduced || i === 0);
        if (!puff.visible) return;
        const cycle = reduced ? .4 : (t * .5 + i / 3) % 1;
        puff.position.set(a.seat.x + Math.sin(cycle * 5 + i) * .15, 1.1 + cycle * 1.3, a.seat.z + .95);
        puff.scale.setScalar(.25 + cycle * .55);
        (puff.material as THREE.SpriteMaterial).opacity = Math.sin(cycle * Math.PI) * .75;
      });
    }
    for (const p of pulse) {
      if (p.kind === 'led') ((p.node as THREE.Mesh).material as THREE.MeshStandardMaterial).emissiveIntensity = Math.sin(t * (2 + (p.seed ?? 0) * .4) + (p.seed ?? 0)) > 0 ? 1.4 : .05;
      if (p.kind === 'float') p.node.position.y = (p.y ?? 0) + Math.sin(t * 1.1) * .12;
      if (p.kind === 'steam') { const c = (t * .5) % 1; p.node.position.y = (p.y ?? 0) + c * .9; ((p.node as THREE.Mesh).material as THREE.MeshStandardMaterial).opacity = .5 * (1 - c); }
    }
  }
  function applyCamera() {
    for (const key of Object.keys(cam) as (keyof typeof cam)[]) cam[key] += (goal[key] - cam[key]) * .14;
    const aspect = stage.clientWidth / Math.max(stage.clientHeight, 1);
    // The whole building fits in a narrow or a wide stage.
    const sizeY = Math.max(cam.size * 1.1 / aspect, cam.size * .66);
    pixelsPerUnit = stage.clientHeight / sizeY;
    camera.left = -sizeY * aspect / 2;
    camera.right = sizeY * aspect / 2;
    camera.top = sizeY / 2;
    camera.bottom = -sizeY / 2;
    camera.updateProjectionMatrix();
    const r = 80;
    camera.position.set(cam.tx + r * Math.sin(cam.az) * Math.cos(cam.el), r * Math.sin(cam.el), cam.tz + r * Math.cos(cam.az) * Math.cos(cam.el));
    camera.lookAt(cam.tx, 0, cam.tz);
  }
  const resize = () => renderer.setSize(stage.clientWidth, stage.clientHeight, false);
  resize();
  const observer = new ResizeObserver(resize);
  observer.observe(stage);

  // Pointer: drag to turn, scroll to zoom, click to select, drag an agent to move it.
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  const ground = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
  let down: { x: number; y: number; moved: boolean; hit: Pick3D; pan: boolean } | null = null;
  const aim = (event: PointerEvent) => {
    const area = canvas.getBoundingClientRect();
    pointer.set(((event.clientX - area.left) / area.width) * 2 - 1, -((event.clientY - area.top) / area.height) * 2 + 1);
    raycaster.setFromCamera(pointer, camera);
  };
  const hitTest = (event: PointerEvent): Pick3D => {
    aim(event);
    for (const hit of raycaster.intersectObjects(office.children, true)) {
      if (hit.object.userData.agent) return { type: 'agent', host: hit.object.userData.agent };
      if (hit.object.userData.room) return { type: 'room', id: hit.object.userData.room };
    }
    return null;
  };
  const groundPoint = (event: PointerEvent) => {
    aim(event);
    const point = new THREE.Vector3();
    return raycaster.ray.intersectPlane(ground, point) ? point : null;
  };
  const onDown = (event: PointerEvent) => {
    down = { x: event.clientX, y: event.clientY, moved: false, hit: hitTest(event), pan: event.shiftKey || event.button === 2 };
    canvas.setPointerCapture(event.pointerId);
  };
  const onMove = (event: PointerEvent) => {
    if (!down) { canvas.classList.toggle('pointing', !!hitTest(event)); return; }
    if (!down.moved && Math.hypot(event.clientX - down.x, event.clientY - down.y) < 5) return;
    down.moved = true;
    canvas.classList.add('dragging');
    const hit = down.hit;
    const actor = hit?.type === 'agent' && !down.pan ? actors.find((a) => a.host === hit.host) : null;
    if (actor && actor.where === 'desk' && seated(actor)) {
      dragging = actor;
      const point = groundPoint(event);
      if (point) { actor.node.position.set(point.x, 1, point.z); actor.x = point.x; actor.z = point.z; }
      return;
    }
    if (down.pan) {
      const k = cam.size / canvas.clientWidth;
      const cos = Math.cos(goal.az), sin = Math.sin(goal.az);
      goal.tx -= (event.movementX * cos + event.movementY * sin) * k;
      goal.tz -= (-event.movementX * sin + event.movementY * cos) * k;
    } else {
      goal.az -= event.movementX * .006;
      goal.el = Math.max(.3, Math.min(1.5, goal.el + event.movementY * .004));
    }
  };
  const onUp = (event: PointerEvent) => {
    canvas.classList.remove('dragging');
    const was = down;
    down = null;
    if (dragging) {
      const a = dragging;
      dragging = null;
      const point = groundPoint(event);
      const target = point && Object.values(rooms).find((r) => point.x > r.x0 && point.x < r.x0 + r.w && point.z > r.z0 && point.z < r.z0 + r.d);
      a.x = a.seat.x;
      a.z = a.seat.z;
      if (target && (target.id === 'mgmt' || target.id === 'staff' || target.dept)) {
        if (target.id !== a.room) events.onMove(a.host, target.id);
      } else events.onToast('Agents can sit in Management, the Staff room or a department');
      return;
    }
    if (was && !was.moved) events.onPick(was.hit);
  };
  const onWheel = (event: WheelEvent) => {
    event.preventDefault();
    goal.size = Math.max(14, Math.min(110, goal.size * (1 + Math.sign(event.deltaY) * .09)));
  };
  const noMenu = (event: Event) => event.preventDefault();
  canvas.addEventListener('pointerdown', onDown);
  canvas.addEventListener('pointermove', onMove);
  canvas.addEventListener('pointerup', onUp);
  canvas.addEventListener('wheel', onWheel, { passive: false });
  canvas.addEventListener('contextmenu', noMenu);

  let nextBubble = .3, frameId = 0, last = performance.now();
  const loop = (now: number) => {
    // The first frame's timestamp can be earlier than `last`; time must never run backwards.
    const dt = Math.max(0, Math.min(.05, (now - last) / 1000));
    last = now;
    simulate(dt);
    animate(clock);
    applyCamera();
    renderer.render(scene, camera);
    updateOverlay();
    // On "auto", the light follows the real clock; checked once a minute.
    if (settings.time === 'auto' && now - lastLightCheck > 60000) {
      lastLightCheck = now;
      if (timeOfDay('auto') !== light) applyTime();
    }
    frameId = requestAnimationFrame(loop);
  };
  let lastLightCheck = 0;
  frameId = requestAnimationFrame(loop);

  return {
    update(list) {
      incoming = list;
      const key = structure(settings, list);
      if (key !== buildKey) { buildKey = key; build(); return; }
      const byHost = new Map(actors.map((a) => [a.host, a]));
      for (const data of list) { const a = byHost.get(data.host); if (a) sync(a, data, false); }
    },
    configure(next) {
      settings = next;
      const key = structure(settings, incoming);
      if (key !== buildKey) { buildKey = key; if (incoming.length) build(); }
      else { rebuildTags(); applyTime(); }
      if (settings.bubbles === 'off') bubbles.clear();
    },
    fly(fromHost, toHost) {
      const from = actors.find((a) => a.host === fromHost), to = actors.find((a) => a.host === toHost);
      if (!from || !to || from === to || flights.length > 12) return;
      const node = makePlane(1);
      office.add(node);
      // A short trail of dots follows the plane through the air.
      const trail = Array.from({ length: 7 }, () => {
        const dot = ball(office, .055, '#ffffff', 0, -5, 0, 1, glow('#ffffff', .4));
        dot.castShadow = false;
        return dot;
      });
      flights.push({ node, trail, from, to, t: 0, seconds: 1.9 + Math.hypot(from.x - to.x, from.z - to.z) * .045 });
    },
    select(host) { selected = host; },
    camera(view) {
      if (view === 'in') goal.size = Math.max(14, goal.size * .82);
      else if (view === 'out') goal.size = Math.min(110, goal.size * 1.2);
      else Object.assign(goal, view, { size: VIEW, tx: 0, tz: -.5 });
    },
    dispose() {
      cancelAnimationFrame(frameId);
      observer.disconnect();
      canvas.removeEventListener('pointerdown', onDown);
      canvas.removeEventListener('pointermove', onMove);
      canvas.removeEventListener('pointerup', onUp);
      canvas.removeEventListener('wheel', onWheel);
      canvas.removeEventListener('contextmenu', noMenu);
      overlay.textContent = '';
      materials.forEach((m) => m.dispose());
      boxGeo.dispose();
      sphereGeo.dispose();
      cylGeo.dispose();
      renderer.dispose();
    },
  };
}
