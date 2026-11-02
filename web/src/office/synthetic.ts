import type { SessionRow } from '../api/types';

/** Dev-only: fake sessions for the office performance check (`?synthetic=60`). */
const HARNESSES = ['claude_code', 'codex', 'antigravity', 'gemini_cli', 'opencode', 'hermes'];
const STATES = ['working', 'working', 'waiting', 'idle', 'error'];

export function syntheticCount(search: string): number {
  const raw = Number(new URLSearchParams(search).get('synthetic'));
  if (!Number.isFinite(raw) || raw <= 0) return 0;
  return Math.min(500, Math.floor(raw));
}

export function makeSyntheticSessions(n: number, now: number): SessionRow[] {
  return Array.from({ length: n }, (_, i) => ({
    imported: 0,
    id: `synthetic:${i}`,
    harness: HARNESSES[i % HARNESSES.length],
    provider: null,
    model: 'synthetic',
    account_id: null,
    project: null,
    title: `Synthetic agent ${i + 1}`,
    started_at: now - 600,
    last_event_at: now,
    ended_at: null,
    status: STATES[i % STATES.length],
    parent_id: null,
    source_ref: null,
    last_kind: null,
    last_tool: null,
    last_preview: null,
    tokens: 0,
  }));
}
