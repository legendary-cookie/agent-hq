const valid = (n: number | null | undefined): n is number => n != null && Number.isFinite(n);
export function fmtTokens(n: number | null): string {
  if (!valid(n)) return '—';
  const a = Math.abs(n);
  for (const [scale, suffix] of [
    [1e9, 'B'],
    [1e6, 'M'],
    [1e3, 'k'],
  ] as const)
    if (a >= scale) return `${(n / scale).toFixed(1).replace(/\.0$/, '')}${suffix}`;
  return String(Math.round(n));
}
export function fmtUsd(
  v: number | null,
  opts?: { unpricedTokens?: number; estimated?: boolean },
): string {
  if (!valid(v)) return '—';
  if (v === 0 && (opts?.unpricedTokens ?? 0) > 0) return 'unpriced';
  return `$${Math.abs(v) >= 1000 ? fmtTokens(v) : v.toFixed(2)}${opts?.estimated ? ' est.' : ''}`;
}
export function fmtPct(v: number | null): string {
  return valid(v) ? `${Math.round(v * 100)}%` : '—';
}
export function fmtPercentValue(v: number | null): string {
  return valid(v) ? `${Math.round(v)}%` : '—';
}
export function fmtDuration(seconds: number | null): string {
  if (!valid(seconds)) return '—';
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} h${m % 60 ? ` ${m % 60} min` : ''}`;
  const d = Math.floor(h / 24);
  return `${d} d${h % 24 ? ` ${h % 24} h` : ''}`;
}
export function fmtRelative(ts: number | null, now: number): string {
  if (!valid(ts) || !valid(now)) return '—';
  const diff = ts - now;
  if (Math.abs(diff) < 60) return 'just now';
  return diff > 0 ? `in ${fmtDuration(diff)}` : `${fmtDuration(-diff)} ago`;
}
export function fmtDelta(
  cur: number | null,
  prev: number | null,
): { text: string; dir: 'up' | 'down' | 'flat' | 'none' } {
  if (!valid(cur) || !valid(prev) || prev === 0) return { text: '—', dir: 'none' };
  const change = Math.round(((cur - prev) / Math.abs(prev)) * 100);
  return {
    text: `${change > 0 ? '+' : ''}${change}%`,
    dir: change > 0 ? 'up' : change < 0 ? 'down' : 'flat',
  };
}
