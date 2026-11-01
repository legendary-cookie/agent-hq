/**
 * Every bucket start from the first to the last timestamp, so charts show
 * empty hours/days as zero instead of skipping them. Days step by local
 * midnight (safe across daylight-saving changes); hours step by 3600 s.
 */
export function continuousBuckets(times: number[], bucket: 'hour' | 'day'): number[] {
  if (!times.length) return [];
  const first = Math.min(...times);
  const last = Math.max(...times);
  const result: number[] = [];
  if (bucket === 'hour') {
    const seen = new Set<string>();
    for (let t = first; t <= last; t += 3600) {
      const key = bucketKey(t, 'hour');
      if (!seen.has(key)) result.push(t);
      seen.add(key);
    }
    return result;
  }
  const day = new Date(first * 1000);
  while (day.getTime() / 1000 <= last) {
    result.push(day.getTime() / 1000);
    day.setDate(day.getDate() + 1);
  }
  return result;
}

export function bucketKey(t: number, bucket: 'hour' | 'day'): string {
  const date = new Date(t * 1000);
  const pad = (n: number) => String(n).padStart(2, '0');
  const day = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  return bucket === 'hour' ? `${day}T${pad(date.getHours())}` : day;
}
