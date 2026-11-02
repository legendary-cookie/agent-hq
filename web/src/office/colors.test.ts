import { expect, it } from 'vitest';
import { HARNESS_COLORS, STATUS_COLORS, harnessLabel } from './colors';

it('harnessLabel maps known harnesses', () => {
  expect(
    [
      'claude_code',
      'codex',
      'antigravity',
      'gemini_cli',
      'opencode',
      'hermes',
      'break',
      'other',
    ].map(harnessLabel),
  ).toEqual([
    'Claude Code',
    'Codex',
    'Antigravity',
    'Gemini CLI',
    'opencode',
    'Hermes',
    'Break room',
    'other',
  ]);
});

it('harness colours differ from status colours', () => {
  const rgb = (hex: string) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  for (const [name, color] of Object.entries(HARNESS_COLORS)) {
    if (name === 'unknown') continue;
    for (const status of Object.values(STATUS_COLORS)) {
      const distance = Math.hypot(...rgb(color).map((value, i) => value - rgb(status)[i]));
      expect(distance).toBeGreaterThan(60);
    }
  }
  expect(HARNESS_COLORS.unknown).toBe('#8e8e93');
});
