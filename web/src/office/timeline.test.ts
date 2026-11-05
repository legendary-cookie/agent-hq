import { expect, it } from 'vitest';
import { replayStart, replayTime, replaySessions } from './timeline';
it('timeline replays states at 10x', () => {
  expect(replayTime(100, 2, 10, 200)).toBe(120);
  expect(replaySessions([{id:'a', status:'idle'}], [{session_id:'a',ts:110,kind:'tool_call',tool:'edit',harness:'codex'}], 120)[0].status).toBe('working');
});
it('replay opens at the first event, not on an empty moment', () => {
  // Found in the real check: Replay opened at the window start, before any event, so the office was empty.
  expect(replayStart([{session_id:'a',ts:150,kind:'assistant',tool:null,harness:null},{session_id:'b',ts:120,kind:'assistant',tool:null,harness:null}], 100)).toBe(120);
  expect(replayStart([], 100)).toBe(100);
  expect(replayStart([{session_id:'a',ts:50,kind:'assistant',tool:null,harness:null}], 100)).toBe(100);
});
