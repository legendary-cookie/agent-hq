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
    for (let t = first; t <= last; t += 3600) result.push(t);
    return result;
  }
  const day = new Date(first * 1000);
  while (day.getTime() / 1000 <= last) {
    result.push(day.getTime() / 1000);
    day.setDate(day.getDate() + 1);
  }
  return result;
}
