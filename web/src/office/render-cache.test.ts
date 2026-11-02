import { expect, it } from 'vitest';
import { reconcile, staticKey } from './render-cache';
import type { Scene } from './scene';
import type { Theme } from './skin';
const scene: Scene = {
  rooms: [{ id: 'codex', harness: 'codex', label: 'Codex', capacity: 6 }],
  desks: [{ id: 'desk', roomId: 'codex', index: 0 }],
  npcs: [
    {
      imported: false,
      id: 'a',
      sessionId: 'a',
      harness: 'codex',
      title: 'A',
      model: null,
      state: 'working',
      color: '#000',
      deskId: 'desk',
      targetRoomId: 'codex',
      parentId: null,
    },
  ],
  walks: [],
};
const size = { width: 800, height: 500 };
const theme: Theme = {
  floor: '#fff',
  floorAlt: '#eee',
  wall: '#ddd',
  text: '#000',
  muted: '#aaa',
  reducedMotion: false,
};
it('staticKey ignores npc positions and walks', () => {
  const changed = {
    ...scene,
    npcs: scene.npcs.map((npc) => ({ ...npc, x: 50, y: 100 })),
    walks: [{ npcId: 'a', fromRoomId: 'codex', toRoomId: 'break', startedAt: 1 }],
  };
  expect(staticKey(changed, size, theme)).toBe(staticKey(scene, size, theme));
});
it('staticKey changes with size, theme and desks', () => {
  const key = staticKey(scene, size, theme);
  expect(staticKey(scene, { ...size, width: 801 }, theme)).not.toBe(key);
  expect(staticKey(scene, size, { ...theme, floor: '#000' })).not.toBe(key);
  expect(staticKey({ ...scene, desks: [{ ...scene.desks[0], index: 1 }] }, size, theme)).not.toBe(
    key,
  );
});
it('reconcile adds removes and keeps', () => {
  expect(reconcile(['a', 'b'], ['b', 'c'])).toEqual({ add: ['c'], remove: ['a'], keep: ['b'] });
});
