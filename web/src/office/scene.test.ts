import { describe, expect, it } from 'vitest';
import type { SessionRow, DelegationRow } from '../api/types';
import { buildScene } from './scene';

const now = 100000;
function session(id: string, overrides: Partial<SessionRow> = {}): SessionRow {
  return {
    id,
    harness: 'codex',
    provider: null,
    model: null,
    account_id: null,
    project: null,
    title: id,
    started_at: now,
    last_event_at: now,
    ended_at: null,
    status: 'working',
    parent_id: null,
    source_ref: null,
    last_kind: null,
    last_tool: null,
    last_preview: null,
    tokens: 0,
    ...overrides,
  };
}
const delegation: DelegationRow = {
  id: 'd',
  ts: now,
  provider: 'codex',
  model: 'm',
  task_kind: 'coding',
  outcome: 'ok',
};
describe('scene', () => {
  it('excludes done and stale sessions', () =>
    expect(
      buildScene(
        [session('a', { status: 'done' }), session('b', { last_event_at: now - 86401 })],
        [],
        new Map(),
        now,
      ).npcs,
    ).toHaveLength(0));
  it('maps status to npc state', () =>
    expect(
      buildScene(
        ['working', 'waiting', 'idle', 'error'].map((status) => session(status, { status })),
        [],
        new Map(),
        now,
      ).npcs.map((n) => n.state),
    ).toEqual(['working', 'waiting', 'idle', 'error']));
  it('one room per harness plus break room last', () =>
    expect(
      buildScene(
        [session('a'), session('b', { harness: 'claude_code' })],
        [],
        new Map(),
        now,
      ).rooms.map((r) => r.id),
    ).toEqual(['claude_code', 'codex', 'break']));
  it('idle npc targets break room but keeps desk', () => {
    const s = buildScene([session('a', { status: 'idle' })], [], new Map(), now);
    expect(s.npcs[0].targetRoomId).toBe('break');
    expect(s.npcs[0].deskId).toBe(s.desks[0].id);
  });
  it('desks are stable across updates', () => {
    const m = new Map<string, string>();
    const a = buildScene([session('a'), session('b')], [], m, now);
    const b = buildScene([session('b'), session('a'), session('c')], [], m, now, a);
    expect(b.npcs.find((n) => n.id === 'a')?.deskId).toBe(a.npcs[0].deskId);
  });
  it('new session takes lowest free desk', () => {
    const m = new Map<string, string>();
    const a = buildScene([session('a'), session('b')], [], m, now);
    const b = buildScene([session('b'), session('c')], [], m, now, a);
    expect(b.npcs[1].deskId).toBe(a.npcs[0].deskId);
  });
  it('room grows for 25 desks', () =>
    expect(
      buildScene(
        Array.from({ length: 25 }, (_, i) => session(String(i))),
        [],
        new Map(),
        now,
      ).rooms[0].capacity,
    ).toBe(30));
  it('subagent creates walk from parent room', () => {
    const m = new Map<string, string>();
    const p = buildScene([session('p', { harness: 'claude_code' })], [], m, now);
    const s = buildScene(
      [session('p', { harness: 'claude_code' }), session('c', { parent_id: 'p' })],
      [],
      m,
      now,
      p,
    );
    expect(s.walks).toContainEqual({
      npcId: 'c',
      fromRoomId: 'claude_code',
      toRoomId: 'codex',
      startedAt: now,
    });
  });
  it('delegation creates walk to provider room', () => {
    const m = new Map<string, string>();
    const sessions = [session('p', { harness: 'claude_code' }), session('c')];
    const prev = buildScene(sessions, [], m, now - 1);
    expect(buildScene(sessions, [delegation], m, now, prev).walks).toContainEqual({
      npcId: 'p',
      fromRoomId: 'claude_code',
      toRoomId: 'codex',
      startedAt: now,
    });
  });
  it('walks expire after 3 s', () => {
    const m = new Map<string, string>();
    const sessions = [session('p', { harness: 'claude_code' }), session('c')];
    const prev = buildScene(sessions, [delegation], m, now);
    expect(buildScene(sessions, [delegation], m, now + 4, prev).walks).toHaveLength(0);
  });
});
