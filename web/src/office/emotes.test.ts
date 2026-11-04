import { expect, it } from 'vitest';
import { emote } from './emotes';
it('emote on turn end', () => {
  expect(emote('turn_end', 100, 101)).toBe('✓');
  expect(emote('turn_end', 100, 102)).toBeNull();
});
