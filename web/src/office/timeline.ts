export type TimelineEvent = { session_id: string; ts: number; kind: string; tool: string | null; harness: string | null };
export function replayTime(start: number, elapsed: number, speed: number, end: number): number {
  return Math.min(end, start + elapsed * speed);
}
export function replaySessions<T extends { id: string; status: string | null }>(sessions: T[], events: TimelineEvent[], at: number): T[] {
  const latest = new Map<string, TimelineEvent>();
  for (const event of events) if (event.ts <= at && (!latest.has(event.session_id) || latest.get(event.session_id)!.ts <= event.ts)) latest.set(event.session_id, event);
  return sessions.filter(s => latest.has(s.id)).map(s => {
    const event = latest.get(s.id)!;
    const status = event.kind === 'error' ? 'error' : event.kind === 'permission' ? 'waiting' : event.kind === 'turn_end' ? 'idle' : 'working';
    return { ...s, status, last_event_at: event.ts, last_kind: event.kind, last_tool: event.tool };
  });
}
/** Where Replay opens: the first event inside the window, so it never starts on an empty office. */
export function replayStart(events: TimelineEvent[], windowStart: number): number {
  let first = Infinity;
  for (const event of events) if (event.ts >= windowStart && event.ts < first) first = event.ts;
  return Number.isFinite(first) ? first : windowStart;
}
