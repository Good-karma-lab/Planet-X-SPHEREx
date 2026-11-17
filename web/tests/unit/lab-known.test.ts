import { describe, expect, it } from 'vitest';
import { knownStatus, knownUrl, KNOWN_OFF, matchKnown, motionOf, parseKnown, placeAt, speedWord, yearOfMjd } from '../../src/screens/lab/known';

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
  // the deeper catalogues, for matching only
  match: [{ id: 'catwise:J1', catalogues: ['CatWISE2020'], mag: 15.1, positions: [{ epoch: 60926.0, ra: 269.4265, dec: 4.7785 }] }],
  status: { match: { gaia: { state: 'ok' }, catwise: { state: 'ok' }, allwise: { state: 'ok' }, twomass: { state: 'ok' } } },
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
    expect(parseKnown(null)).toMatchObject({ objects: [], answered: { gaia: false, simbad: false, jpl: false }, pending: [], deep: [] });
    expect(parseKnown(null).deepSilent).toHaveLength(4);
    expect(k.deep.map((o) => o.id)).toEqual(['catwise:J1']);
    expect(k.deepSilent).toEqual([]);
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

  it('counts what the picture shows, and waits for a catalogue the service is still asking', () => {
    const k = parseKnown({ ...FAKE, status: { ...FAKE.status, jpl: { state: 'pending' } } });
    expect(k.pending).toEqual(['jpl']);
    expect(knownStatus({ state: 'ok', ...k }, { known: 212, noMatch: 7 })).toBe('known 212 · unknown 7 · JPL: waiting');
    // a deeper catalogue that did not answer: the sources are without a match so far, not unknown
    const half = parseKnown({ ...FAKE, status: { match: { gaia: { state: 'ok' }, catwise: { state: 'unavailable' }, allwise: { state: 'ok' }, twomass: { state: 'ok' } } } });
    expect(knownStatus({ state: 'ok', ...half }, { known: 3, noMatch: 2 })).toBe('known 3 · no match 2 · CatWISE did not answer · JPL did not answer');
  });

  it('rings only catalogue objects with a detected source, and points at sources with no match', () => {
    const k = parseKnown(FAKE);
    // a 100 px picture of 3″ pixels centred on (269.4465, 4.7685); u grows to the west, v to the south
    const cosd = Math.cos((4.7685 * Math.PI) / 180);
    const uvOf = (ra: number, dec: number): [number, number] => [0.5 - ((ra - 269.4465) * cosd * 3600) / 300, 0.5 - ((dec - 4.7685) * 3600) / 300];
    const year = yearOfMjd(60926.0);
    const [bu, bv] = uvOf(269.446448, 4.767352);
    const peaks = [
      { x: bu * 100 + 0.6, y: bv * 100 - 0.4, snr: 400 }, // Barnard's Star
      { x: 80, y: 20, snr: 12 }, // a source no catalogue has
      { x: 20, y: 80, snr: 6 }, // too faint to point at
    ];
    const m = matchKnown(k.objects, peaks, 100, 3, uvOf, year);
    expect([...m.shown]).toEqual(['gaia:4472832130942575872']);
    expect(m.noMatch).toEqual([{ u: 0.8, v: 0.2, snr: 12 }]);
    // the same source is known when a deeper catalogue has it within 3″ at that date
    const [du, dv] = [0.8, 0.2];
    const deep = [{ id: 'catwise:x', source: 'gaia' as const, name: '', label: false, positions: [{ epoch: year, ra: 269.4465 - ((du - 0.5) * 300) / 3600 / cosd + 0.0002, dec: 4.7685 - ((dv - 0.5) * 300) / 3600 }] }];
    expect(matchKnown(k.objects, peaks, 100, 3, uvOf, year, deep).noMatch).toEqual([]);
  });
});
