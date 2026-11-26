import { describe, expect, it } from 'vitest';
import { faintTitle, hiddenLine, wingLine } from '../../src/lib/wing';

describe('why a Planet X peak is hidden (26 Nov 2026)', () => {
  it('the chip says every reason with its count', () => {
    const t = faintTitle({ photometry: 12, star: 3, no_sum: 158, unseen: 90 }, 'tail.');
    expect(t).toContain('158 peaks whose sum along the orbit is not kept here');
    expect(t).toContain('90 peaks whose dot does not stand out of the noise in the sum along the orbit (under 4.8×)');
    expect(t).toContain('12 far peaks (beyond 200 au) on detectors 1–3');
    expect(t).toContain('3 peaks a bright star’s light makes');
    expect(t.endsWith('tail.')).toBe(true);
    expect(faintTitle(undefined, 'x')).toContain('sum along the orbit');
  });
  it('a card says why it is hidden', () => {
    expect(hiddenLine(['unseen'], { z: 3.14 })).toBe(
      'Hidden with the faint peaks: its dot stands 3.1× above the noise of the sum along the orbit (4.8× needed)',
    );
    expect(hiddenLine(['star', 'no_sum'])).toContain('a bright star’s glow most likely makes it; its sum along the orbit is not kept here');
    expect(hiddenLine(undefined)).toBe('');
  });
  it('a far star is said in arcminutes', () => {
    expect(wingLine({ sep: 314.6, k: 1.5 })).toBe('A bright star 5.2′ away: its glow outside the search\'s mask most likely makes this peak');
    expect(wingLine({ sep: 41 })).toContain('A bright star 41″ away: its glow');
    expect(wingLine({ sep: 186, k: 4.5 })).toBe('A bright star 3.1′ away: its ghost, which falls about 3′ from it, most likely makes this peak');
  });
});
