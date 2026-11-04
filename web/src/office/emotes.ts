export function emote(kind: string | null, eventAt: number, now: number): string | null {
  if (now < eventAt || now - eventAt >= 2) return null;
  return ({ turn_end: '✓', error: '🔴', permission: '!', approval: '!', idle: '💤' } as Record<string, string>)[kind ?? ''] ?? null;
}
