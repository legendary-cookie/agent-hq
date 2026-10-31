import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { subscribeChanges } from './live';
class MockSource { static instances: MockSource[]=[]; onopen: (()=>void)|null=null; onerror: (()=>void)|null=null; listeners: Record<string,()=>void>={}; constructor(public url:string) { MockSource.instances.push(this); } addEventListener(k:string, f:()=>void) { this.listeners[k]=f; } close() {} }
beforeEach(()=>{ vi.useFakeTimers(); MockSource.instances=[]; vi.stubGlobal('EventSource',MockSource); });
afterEach(()=>{ vi.useRealTimers(); vi.unstubAllGlobals(); });
it('debounces change notices',()=>{ const cb=vi.fn(); const stop=subscribeChanges(cb); const s=MockSource.instances[0]; s.listeners.changed(); s.listeners.changed(); vi.advanceTimersByTime(999); expect(cb).not.toHaveBeenCalled(); vi.advanceTimersByTime(1); expect(cb).toHaveBeenCalledTimes(1); stop(); });
it('reconnects SSE and refetches',()=>{ const cb=vi.fn(); const stop=subscribeChanges(cb); MockSource.instances[0].onerror!(); vi.advanceTimersByTime(1000); expect(MockSource.instances).toHaveLength(2); MockSource.instances[1].onopen!(); expect(cb).toHaveBeenCalledTimes(1); stop(); });
