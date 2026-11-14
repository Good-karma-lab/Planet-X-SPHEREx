import { describe, expect, it } from 'vitest';
import { MJY_SR_TO_MJY_PIXEL, quickPoints, type Quick } from '../../src/screens/spectrum/quicklook';

function cube(n: number, valid: (i: number) => boolean, hits?: (i: number) => number | null): Quick {
  return {
    label: 'quick look',
    unit: 'MJy/sr',
    lam: Array.from({ length: n }, (_, i) => 0.75 + (i * 4.25) / n),
    flux: Array.from({ length: n }, (_, i) => (valid(i) ? 1 + 0.01 * i : null)),
    nhits: hits ? Array.from({ length: n }, (_, i) => hits(i)) : undefined,
  };
}

describe('quickPoints', () => {
  it('converts MJy/sr to mJy in one 12.9 arcsec pixel', () => {
    expect(MJY_SR_TO_MJY_PIXEL).toBeCloseTo(3.903, 2);
    const p = quickPoints(cube(102, () => true))!;
    expect(p).toHaveLength(102);
    expect(p[0]!.mjy).toBeCloseTo(1 * MJY_SR_TO_MJY_PIXEL, 6);
  });
  it('draws nothing when most planes are blank (a saturated star)', () => {
    expect(quickPoints(cube(102, (i) => i >= 80))).toBeNull(); // 22 valid planes of 102: the 3.8-5 um tail only
  });
  it('keeps the valid planes of a normal source and skips planes without exposures', () => {
    const p = quickPoints(cube(102, (i) => i !== 50 && i !== 51, (i) => (i === 10 ? 0 : 5)))!;
    expect(p).toHaveLength(99);
  });
  it('is null without data or in another unit', () => {
    expect(quickPoints(null)).toBeNull();
    expect(quickPoints({ ...cube(102, () => true), unit: 'Jy' })).toBeNull();
  });
});
