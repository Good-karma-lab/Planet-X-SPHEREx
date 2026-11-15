import { describe, expect, it } from 'vitest';
import { binSpectrum, type Bin } from '../../src/screens/spectrum/bins';
import { classOf, fitBlackbody, guessObject } from '../../src/screens/spectrum/classify';

const planck = (lam: number, T: number) => lam ** -3 / Math.expm1(14387.77 / (lam * T));
const bins = (T: number, mod: (lam: number, f: number) => number = (_l, f) => f): Bin[] => {
  const out: Bin[] = [];
  for (let k = 0; k < 16; k++) {
    const lo = 0.75 * (5 / 0.75) ** (k / 16);
    const hi = 0.75 * (5 / 0.75) ** ((k + 1) / 16);
    const lam = Math.sqrt(lo * hi);
    const flux = mod(lam, 5000 * planck(lam, T) / planck(1, T) / 1000);
    out.push({ lo, hi, lam, flux, err: flux * 0.03, n: 20, det: 1, detected: true });
  }
  return out;
};

describe('classify', () => {
  it('maps temperatures to classes', () => {
    expect(classOf(4500)).toContain('K-type');
    expect(classOf(3000)).toContain('cool M dwarf');
    expect(classOf(900)).toContain('brown dwarf');
  });
  it('recovers the temperature of a blackbody', () => {
    const f = fitBlackbody(bins(4500))!;
    expect(f.T).toBeGreaterThan(3900);
    expect(f.T).toBeLessThan(5200);
    const g = guessObject({ bins: bins(3000), usable: 300, saturated: false, running: false });
    expect(g.kind).toBe('ok');
    expect(g.head).toMatch(/Spectrum suggests/);
    expect(g.head).toMatch(/M dwarf|M-type/);
  });
  it('leads with the catalogue name', () => {
    const g = guessObject({ bins: bins(3100), usable: 300, saturated: false, running: false, name: "Barnard's Star", sp: 'M4V' });
    expect(g.head.startsWith("Barnard's Star (SIMBAD: M4V) · spectrum suggests ")).toBe(true);
    expect(g.head).toMatch(/about [0-9,]+ K/);
  });
  it('flags a methane dip and says a cold brown dwarf', () => {
    const g = guessObject({
      bins: bins(1000, (lam, f) => (lam > 3.1 && lam < 3.55 ? f * 0.5 : f)),
      usable: 300,
      saturated: false,
      running: false,
    });
    expect(g.features).toMatch(/methane/);
    expect(g.head).toMatch(/brown dwarf/);
  });
  it('says so when saturated or thin', () => {
    expect(guessObject({ bins: [], usable: 0, saturated: true, running: false }).kind).toBe('saturated');
    expect(guessObject({ bins: [], usable: 3, saturated: false, running: false }).kind).toBe('thin');
    expect(guessObject({ bins: [], usable: 2, saturated: false, running: true }).kind).toBe('wait');
    void binSpectrum;
  });
});
