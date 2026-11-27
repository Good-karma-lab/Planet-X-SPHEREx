import { describe, expect, it } from 'vitest';
import { rulerOf, rulerWord } from '../../src/screens/lab/ruler';

describe('ruler', () => {
  it("gives Barnard's shift between two passes a year apart: 10.4″ north, 10.4″/yr", () => {
    const r = rulerOf({ ra: 269.446448, dec: 4.767352, epoch: 2025.68, pass: 0 }, { ra: 269.446224, dec: 4.77023, epoch: 2026.68, pass: 2 });
    expect(r.shift).toBeCloseTo(10.39, 1);
    expect(r.rate).toBeCloseTo(10.39, 1);
    expect(rulerWord(r)).toMatch(/^10\.4″ · 10\.4″\/yr · toward 35\d° \(N\)$/);
  });
  it('gives no rate for two points of the same frame', () => {
    const r = rulerOf({ ra: 10, dec: 0, epoch: 2025.9, pass: 1 }, { ra: 10.01, dec: 0, epoch: 2025.9, pass: 1 });
    expect(r.rate).toBeNull();
    expect(rulerWord(r)).toBe('36.0″ · toward 90° (E)');
  });
  it('says a rate inside a pass per hour', () => {
    const r = rulerOf({ ra: 10, dec: 0, epoch: 2025.9, pass: null }, { ra: 10, dec: 0.01, epoch: 2025.9 + 1 / 365.25, pass: null });
    expect(rulerWord(r)).toBe('36.0″ · 1.50″/h · toward 0° (N)');
  });
  it("the lab's own ruler between a find's passes says the time, not a second rate", () => {
    const r = rulerOf({ ra: 269.446448, dec: 4.767352, epoch: 2025.68, pass: 0 }, { ra: 269.446224, dec: 4.77023, epoch: 2026.68, pass: 2 });
    expect(rulerWord(r, { rate: false })).toMatch(/^10\.4″ in 1\.0 yr · toward 35\d° \(N\)$/);
  });
});
