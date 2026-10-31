import { expect, it } from 'vitest';
import { parseRoute } from './router';
it('unknown route is dashboard', () => { expect(parseRoute('#/other')).toBe('dashboard'); });
