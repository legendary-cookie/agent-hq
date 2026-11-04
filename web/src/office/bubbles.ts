export function bubbleText(text: string | null, eventAt: number, now: number, focused: boolean): string | null {
  return text && (focused || (now >= eventAt && now - eventAt < 6)) ? text.slice(0, 80) : null;
}
