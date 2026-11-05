import { expect, it } from 'vitest';
import { bubbleText } from './bubbles';
it('bubble fades after 6s', () => {
  expect(bubbleText('Edit app.py', 100, 105, false)).toBe('Edit app.py');
  expect(bubbleText('Edit app.py', 100, 106, false)).toBeNull();
  expect(bubbleText('Edit app.py', 100, 110, true)).toBe('Edit app.py');
});
