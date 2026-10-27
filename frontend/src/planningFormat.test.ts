import {it,expect} from 'vitest';
import {formatEstimate} from './planningFormat';
it('does not present small positive estimates as zero seconds',()=>{expect(formatEstimate(.03)).toBe('<0.1 s');expect(formatEstimate(4.075)).toBe('4.1 s');});
it('does not invent a duration when capacity is unavailable',()=>{expect(formatEstimate(null)).toBe('Unavailable');expect(formatEstimate(Infinity)).toBe('Unavailable');});
