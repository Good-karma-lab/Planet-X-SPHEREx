import { describe, expect, it } from 'vitest';
import { knownStatus, knownUrl, KNOWN_OFF, motionOf, parseKnown, placeAt, speedWord, yearOfMjd } from '../../src/screens/lab/known';

// A fake of the `GET /api/known` contract (product spec §8), as stream B2's service answers it: dates echoed as asked (MJD).
const FAKE = {
  objects: [
    {
      id: 'gaia:4472832130942575872',
      source: 'gaia',
      name: "Barnard's star",
      type: 'BY*',
      mag: 8.19,
      pm_total: 10393.3,
      positions: [
        { epoch: 60926.0, ra: 269.446448, dec: 4.767352 },
        { epoch: 61291.25, ra: 269.446224, dec: 4.77023 },
      ],
      label: true,
    },
    { id: 'gaia:1', source: 'gaia', name: 'Gaia DR3 1', mag: 17.2, pm_total: 4.1, positions: [{ epoch: 60926.0, ra: 269.44, dec: 4.76 }], label: false },
    {
      id: 'jpl:433 Eros',
      source: 'jpl',
      name: '433 Eros',
      type: 'asteroid',
      mag: 12.1,
      pm_total: 262980000,
      rate_arcsec_h: 30,
      positions: [{ epoch: 61291.25, ra: 269.45, dec: 4.77 }],
      label: true,
    },
    { id: 'bad', source: 'elsewhere', positions: [] },
    { id: 'bad2', source: 'simbad', positions: [{ epoch: 'x', ra: 1, dec: 2 }] },
  ],
  answered: { gaia: true, simbad: true, jpl: false },
};

describe('known objects of a lab field', () => {
  it('asks with MJD dates and the field size', () => {
    const u = knownUrl(269.44629, 4.76792, 5, [60926.066, 61137.5871]);
    expect(u).toBe('/api/known?ra=269.44629&dec=4.76792&size_arcmin=5&epochs=60926.0660%2C61137.5871');
  });

  it('keeps the rows of the contract, drops wrong ones, turns dates into years', () => {
    const k = parseKnown(FAKE);
    expect(k.objects.map((o) => o.id)).toEqual(['gaia:4472832130942575872', 'gaia:1', 'jpl:433 Eros']);
    expect(k.objects[0]!.positions[0]!.epoch).toBeCloseTo(yearOfMjd(60926.0), 9);
    expect(k.answered).toEqual({ gaia: true, simbad: true, jpl: false });
    expect(parseKnown(null)).toEqual({ objects: [], answered: { gaia: false, simbad: false, jpl: false } });
  });

  it('puts a star between its two places by the date, and beyond them on the same line', () => {
    const o = parseKnown(FAKE).objects[0]!;
    const mid = placeAt(o, yearOfMjd((60926.0 + 61291.25) / 2))!;
    expect(mid[0]).toBeCloseTo((269.446448 + 269.446224) / 2, 6);
    expect(mid[1]).toBeCloseTo((4.767352 + 4.77023) / 2, 6);
    const later = placeAt(o, yearOfMjd(61291.25 + 365.25))!;
    expect(later[1]).toBeCloseTo(4.77023 + (4.77023 - 4.767352), 5);
  });

  it("measures Barnard's motion from its places: about 10.4″/yr, almost due north", () => {
    const m = motionOf(parseKnown(FAKE).objects[0]!)!;
    expect(m.rate).toBeGreaterThan(10.2);
    expect(m.rate).toBeLessThan(10.6);
    expect(m.pa > 350 || m.pa < 5).toBe(true);
    expect(speedWord(parseKnown(FAKE).objects[0]!)).toBe('10.4″/yr');
  });

  it('rings a Solar System body only on the date it was given a place for', () => {
    const eros = parseKnown(FAKE).objects[2]!;
    expect(placeAt(eros, yearOfMjd(61291.3))).toEqual([269.45, 4.77]);
    expect(placeAt(eros, yearOfMjd(60926.0))).toBeNull();
    expect(speedWord(eros)).toBe('30″/h');
  });

  it('says which catalogue did not answer, and nothing when all did', () => {
    const k = parseKnown(FAKE);
    expect(knownStatus({ state: 'ok', ...k })).toBe('JPL did not answer');
    expect(knownStatus({ state: 'ok', ...k, answered: { gaia: true, simbad: true, jpl: true } })).toBe('');
    expect(knownStatus({ state: 'ok', ...k, answered: { gaia: false, simbad: true, jpl: false } })).toBe('Gaia and JPL did not answer');
    expect(knownStatus({ ...KNOWN_OFF, state: 'failed' })).toBe('Gaia, SIMBAD and JPL did not answer');
    expect(knownStatus(KNOWN_OFF)).toBe('');
  });
});
