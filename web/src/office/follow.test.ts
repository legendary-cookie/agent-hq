import { expect, it } from 'vitest';
import { followOffset } from './follow';
it('follow keeps npc centred', () => expect(followOffset({x: 10,y: 20}, {x: 100,y: 80})).toEqual({x: 90,y: 60}));
