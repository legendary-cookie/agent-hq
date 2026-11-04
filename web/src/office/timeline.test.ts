import { expect, it } from 'vitest';
import { replayTime, replaySessions } from './timeline';
it('timeline replays states at 10x', () => {
  expect(replayTime(100, 2, 10, 200)).toBe(120);
  expect(replaySessions([{id:'a', status:'idle'}], [{session_id:'a',ts:110,kind:'tool_call',tool:'edit',harness:'codex'}], 120)[0].status).toBe('working');
});
