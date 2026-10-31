import { expect, it } from 'vitest';
import { fmtTokens, fmtUsd, fmtPct, fmtPercentValue, fmtDuration, fmtRelative, fmtDelta } from './format';
it('compact token format',()=>{ expect([999,1200,41200,3100000,303310232,1200000000].map(fmtTokens)).toEqual(['999','1.2k','41.2k','3.1M','303.3M','1.2B']); });
it('formats null as dash',()=>{ expect([fmtTokens(null),fmtUsd(null),fmtPct(null),fmtPercentValue(null),fmtDuration(null),fmtRelative(null,0)]).toEqual(Array(6).fill('—')); });
it('formats unpriced value',()=>{ expect(fmtUsd(0,{unpricedTokens:100})).toBe('unpriced'); expect(fmtUsd(41.2)).toBe('$41.20'); expect(fmtUsd(.62)).toBe('$0.62'); expect(fmtUsd(1200,{estimated:true})).toBe('$1.2k est.'); });
it('formats durations, percentages, relative times and deltas',()=>{ expect(fmtPct(.75)).toBe('75%'); expect(fmtPercentValue(75)).toBe('75%'); expect([45,720,6480,266400].map(fmtDuration)).toEqual(['45 s','12 min','1 h 48 min','3 d 2 h']); expect(fmtRelative(100,100)).toBe('just now'); expect(fmtRelative(0,300)).toBe('5 min ago'); expect(fmtRelative(6480,0)).toBe('in 1 h 48 min'); expect(fmtDelta(112,100)).toEqual({text:'+12%',dir:'up'}); expect(fmtDelta(1,0).dir).toBe('none'); });
