import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { isChunkError } from '../../src/lib/newbuild';
import { daysStep, stageOf } from '../../src/screens/lab/loading';
import { BLIND_ARCSEC, isBlindPlace, offPath, placeOnTrack } from '../../src/screens/lab/trackplace';

const pts = [5, 9, 10, 14, 15].map((v, i) => ({ i, mjd: 100 + v, ra: 10 + i * 0.1, dec: 0 }));
const mjds = Array.from({ length: 17 }, (_, k) => 101 + k); // visit n is frame n - 1

describe('a track’s place on the frame shown', () => {
  it('is its own visit on exactly the frames it was found in', () => {
    const exact = mjds.map((_, k) => placeOnTrack(pts, mjds, k)!).map((q, k) => (q.exact ? k + 1 : 0)).filter(Boolean);
    expect(exact).toEqual([5, 9, 10, 14, 15]);
  });
  it('on another frame is the nearest visit’s, and says which', () => {
    const q = placeOnTrack(pts, mjds, 12)!; // visit 13
    expect([q.exact, q.frame + 1, q.point.i]).toEqual([false, 14, 3]);
  });
  it('a visit an orbit or two away never lends its number (visits 1.6 h to 6 h apart)', () => {
    const near = [100, 100.067, 100.25];
    expect(placeOnTrack([{ i: 0, mjd: 100.0672, ra: 1, dec: 1 }], near, 0)!.exact).toBe(false);
    expect(placeOnTrack([{ i: 0, mjd: 100.0672, ra: 1, dec: 1 }], near, 1)!.exact).toBe(true);
    expect(placeOnTrack([{ i: 0, mjd: 100.0672, ra: 1, dec: 1 }], near, 2)!.exact).toBe(false);
  });
  it('without a frame or a track there is none', () => {
    expect(placeOnTrack([], mjds, 0)).toBeNull();
    expect(placeOnTrack(pts, [], 0)).toBeNull();
  });
});

describe('what a task keeps blind', () => {
  const as = 1 / 3600;
  it('the find itself within the tolerance, and nothing else', () => {
    expect(isBlindPlace(10, 0.5 * BLIND_ARCSEC * as, { ra: 10, dec: 0 }, [])).toBe(true);
    expect(isBlindPlace(10, 2 * BLIND_ARCSEC * as, { ra: 10, dec: 0 }, [])).toBe(false);
    expect(isBlindPlace(10, 0, null, [])).toBe(false);
  });
  it('anywhere on a track, between its visits too (a known body on a control’s path)', () => {
    expect(isBlindPlace(10.25, 5 * as, null, pts)).toBe(true);
    expect(isBlindPlace(10.25, 60 * as, null, pts)).toBe(false);
  });
  it('across RA 0', () => {
    expect(offPath(359.9999, 0, [{ ra: 0.0001, dec: 0 }])).toBeCloseTo(0.72, 2);
  });
});

describe('a page left from an earlier build', () => {
  it('knows a lazy part that cannot be loaded, in the three browsers’ words', () => {
    for (const m of ['Failed to fetch dynamically imported module: https://x/a.js', 'error loading dynamically imported module', 'Importing a module script failed.'])
      expect(isChunkError(new TypeError(m))).toBe(true);
    expect(isChunkError(new TypeError("Cannot read properties of null (reading 'x')"))).toBe(false);
    expect(isChunkError(null)).toBe(false);
  });
});

describe('the Days wait', () => {
  it('is the loading card, without the field’s stages', () => {
    expect(daysStep('', false)).toMatchObject({ title: 'Loading the visits', failed: false });
    expect(daysStep('no visits here', true)).toMatchObject({ detail: 'no visits here', failed: true });
    expect(stageOf(daysStep('', false)).list).toEqual([]);
  });
});

describe('every screen has a boundary', () => {
  it('the app wraps the landing and the screens', () => {
    const app = readFileSync(new URL('../../src/app.tsx', import.meta.url), 'utf8');
    expect(app.match(/<ErrorBoundary /g)?.length).toBe(2);
  });
});
