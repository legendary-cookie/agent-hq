import type { DelegationRow, SessionRow } from '../api/types';
import { HARNESS_COLORS } from './colors';

export type NpcState = 'working' | 'active' | 'waiting' | 'idle' | 'error';
export type Room = { id: string; harness: string | null; label: string; capacity: number };
export type Desk = { id: string; roomId: string; index: number };
export type Npc = {
  id: string;
  sessionId: string;
  harness: string;
  title: string;
  model: string | null;
  state: NpcState;
  imported: boolean;
  launchedRunId?: string | null;
  color: string;
  deskId: string | null;
  targetRoomId: string;
  parentId: string | null;
  bubble?: string | null;
  eventAt?: number;
  lastKind?: string | null;
};
export type Walk = { npcId: string; fromRoomId: string; toRoomId: string; startedAt: number };
export type Scene = { rooms: Room[]; desks: Desk[]; npcs: Npc[]; walks: Walk[] };
export type SeatMemory = Map<string, string>;
export const ROOM_SIZE_STEP = 6;
const newestDelegations = new WeakMap<Scene, number>();

export function buildScene(
  sessions: SessionRow[],
  delegations: DelegationRow[],
  memory: SeatMemory,
  now: number,
  prev?: Scene,
): Scene {
  const included = sessions.filter(
    (s) => s.status !== 'done' && s.last_event_at !== null && s.last_event_at >= now - 86400,
  );
  const ids = new Set(included.map((s) => s.id));
  for (const id of memory.keys()) if (!ids.has(id)) memory.delete(id);
  const harnessOf = (s: SessionRow) => s.harness || 'unknown';
  const roomOf = (s: SessionRow) => {
    const goal = (s as SessionRow & { goal_id?: string | null }).goal_id;
    return s.launched_run_id && goal ? `team:${goal}` : s.host ? `host:${s.host}` : harnessOf(s);
  };
  const used = new Set<string>();
  for (const s of included) {
    const desk = memory.get(s.id);
    if (desk && desk.startsWith(`${roomOf(s)}:`) && !used.has(desk)) used.add(desk);
    else memory.delete(s.id);
  }
  const npcs: Npc[] = included.map((s) => {
    const harness = harnessOf(s);
    const room = roomOf(s);
    let desk = memory.get(s.id);
    if (!desk) {
      let index = 0;
      while (used.has(`${room}:${index}`)) index++;
      desk = `${room}:${index}`;
      memory.set(s.id, desk);
      used.add(desk);
    }
    const state: NpcState =
      s.status === 'active' ||
      s.status === 'working' ||
      s.status === 'waiting' ||
      s.status === 'error'
        ? s.status
        : 'idle';
    return {
      id: s.id,
      sessionId: s.id,
      harness,
      title: s.title || s.id,
      model: s.model,
      state,
      imported: Boolean(s.imported),
      launchedRunId: s.launched_run_id,
      color: HARNESS_COLORS[harness] ?? HARNESS_COLORS.unknown,
      deskId: desk,
      targetRoomId: state === 'idle' && !s.host && !room.startsWith('team:') ? 'break' : room,
      parentId: s.parent_id,
      bubble: s.last_tool || s.last_preview,
      eventAt: s.last_event_at ?? 0,
      lastKind: s.last_kind,
    };
  });
  const roomIds = [...new Set(included.map(roomOf))].sort();
  const desks: Desk[] = npcs.map((n) => ({
    id: n.deskId!,
    roomId: n.deskId!.slice(0, n.deskId!.lastIndexOf(':')),
    index: Number(n.deskId!.slice(n.deskId!.lastIndexOf(':') + 1)),
  }));
  const rooms: Room[] = roomIds.map((room) => ({
    id: room,
    harness: room.startsWith('host:') || room.startsWith('team:') ? null : room,
    label: room.startsWith('host:') ? room.slice(5) : room.startsWith('team:') ? `Team ${room.slice(5)}` : room,
    capacity: Math.max(
      ROOM_SIZE_STEP,
      Math.ceil(
        (Math.max(...desks.filter((d) => d.roomId === room).map((d) => d.index)) + 1) /
          ROOM_SIZE_STEP,
      ) * ROOM_SIZE_STEP,
    ),
  }));
  rooms.push({ id: 'break', harness: null, label: 'Break room', capacity: 0 });
  const walks = (prev?.walks ?? []).filter((w) => now - w.startedAt <= 3);
  // Walks animate changes since the previous scene; the first scene shows no history.
  const previousIds = new Set(prev?.npcs.map((n) => n.id) ?? []);
  for (const npc of npcs) {
    const old = prev?.npcs.find((n) => n.id === npc.id);
    if (old && old.targetRoomId !== npc.targetRoomId)
      walks.push({ npcId: npc.id, fromRoomId: old.targetRoomId, toRoomId: npc.targetRoomId, startedAt: now });
    const parent = npcs.find((n) => n.id === npc.parentId);
    if (prev && !previousIds.has(npc.id) && parent)
      walks.push({
        npcId: npc.id,
        fromRoomId: parent.deskId!.slice(0, parent.deskId!.lastIndexOf(':')),
        toRoomId: npc.deskId!.slice(0, npc.deskId!.lastIndexOf(':')),
        startedAt: now,
      });
  }
  const latest = prev ? (newestDelegations.get(prev) ?? -Infinity) : -Infinity;
  const source = included
    .filter((s) => !s.host && harnessOf(s) === 'claude_code')
    .sort((a, b) => (b.last_event_at ?? 0) - (a.last_event_at ?? 0))[0];
  // A delegation is new if it is newer than anything the previous scene saw, however
  // old it is by the time it reaches the page; its walk starts now and lasts 3 s.
  for (const d of delegations) {
    if (
      prev &&
      d.ts > latest &&
      source &&
      ['codex', 'antigravity', 'hermes', 'opencode'].includes(d.provider) &&
      roomIds.includes(d.provider)
    )
      walks.push({
        npcId: source.id,
        fromRoomId: 'claude_code',
        toRoomId: d.provider,
        startedAt: now,
      });
  }
  const scene = { rooms, desks, npcs, walks };
  newestDelegations.set(scene, Math.max(latest, ...delegations.map((d) => d.ts)));
  return scene;
}
