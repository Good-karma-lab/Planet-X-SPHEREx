import { describe, expect, it } from 'vitest';
import { ang2pix, faceXY } from '../../src/lib/healpix';
import { facePt, orderFor, pixAt } from '../../src/lib/hips';

const D2R = Math.PI / 180;

describe('HiPS face coordinates', () => {
  it('give the same nested pixel as the integer HEALPix code at every order', () => {
    let seed = 7;
    const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
    for (let k = 0; k < 4000; k++) {
      const ra = rnd() * 360;
      const dec = Math.asin(2 * rnd() - 1) / D2R;
      const f = facePt(ra, Math.sin(dec * D2R));
      for (const n of [0, 3, 5, 9, 12]) {
        expect(pixAt(n, f)).toBe(ang2pix(n, ra, dec));
        const g = faceXY(n, ra, dec);
        expect(Math.floor(f.x * 2 ** n)).toBe(g.ix);
        expect(Math.floor(f.y * 2 ** n)).toBe(g.iy);
      }
    }
  });
  it('stay inside the face at the poles and on face edges', () => {
    for (const [ra, dec] of [
      [0, 90],
      [45, -90],
      [90, 41.8103149],
      [0, 0],
      [359.9999999, 0],
    ]) {
      const f = facePt(ra!, Math.sin(dec! * D2R));
      expect(f.x).toBeGreaterThanOrEqual(0);
      expect(f.x).toBeLessThan(1);
      expect(f.y).toBeGreaterThanOrEqual(0);
      expect(f.y).toBeLessThan(1);
    }
  });
});

describe('tile order for a globe', () => {
  it('reads the Allsky image for the whole globe and deeper tiles as it grows', () => {
    expect(orderFor('dss2', 400)).toBe(-1);
    expect(orderFor('dss2', 1000)).toBe(-1);
    expect(orderFor('dss2', 64000)).toBe(7);
    expect(orderFor('dss2', 1e6)).toBe(9);
  });
  it('reads the Allsky image for a survey without shallow tiles until order 3 is needed', () => {
    expect(orderFor('spx8', 650)).toBe(-1);
    expect(orderFor('spx8', 3500)).toBe(3);
    expect(orderFor('dss2', 400, false)).toBe(-1);
  });
});
