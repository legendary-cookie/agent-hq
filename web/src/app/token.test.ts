import { expect, it } from 'vitest';
import { bootstrapToken } from './token';
it('stores token from hash and rewrites hash', () => { sessionStorage.clear(); history.replaceState(null, '', '/#token=abc'); expect(bootstrapToken(location, sessionStorage)).toBe('abc'); expect(sessionStorage.getItem('hq-token')).toBe('abc'); expect(location.hash).toBe('#/dashboard'); });
it('falls back to stored token', () => { sessionStorage.setItem('hq-token', 'saved'); history.replaceState(null, '', '/'); expect(bootstrapToken(location, sessionStorage)).toBe('saved'); });
