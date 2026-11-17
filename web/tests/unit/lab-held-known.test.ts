import { describe, expect, it } from 'vitest';
import { heldAt, parseKnown, type KnownState } from '../../src/screens/lab/known';
import { heldIdentified, identifySkip, nameOf } from '../../src/screens/spectrum';
import { heldRow } from '../../src/screens/search/context';

// A ringed known star clicked in the lab: its identity comes from the field's own catalogue lookup, never from asking again and never
// in the words of an unknown source.
const reply = parseKnown({
  objects: [
    {
      id: 'gaia:4472832508888391168',
      source: 'gaia',
      name: 'Gaia DR3 4472832508888391168',
      type: 'star',
      mag: 12.31,
      pm_total: 12.0,
      pmra: -8.0,
      pmdec: 9.0,
      parallax: 1.42,
      positions: [{ epoch: 2025.9, ra: 269.42037, dec: 4.73183 }],
      label: false,
    },
    {
      id: 'gaia:4472832130942575872',
      source: 'gaia',
      name: "Barnard's Star",
      simbad: "Barnard's Star",
      type: 'BY*',
      mag: 8.19,
      pm_total: 10393,
      pmra: -801.6,
      pmdec: 10362.4,
      parallax: 546.98,
      positions: [{ epoch: 2025.9, ra: 269.44629, dec: 4.76792 }],
      label: true,
    },
  ],
  answered: { gaia: true, simbad: true, jpl: true },
  status: {},
  match: [{ id: 'catwise:J175800.00+044500.0', catalogues: ['CatWISE2020', 'AllWISE'], ids: ['catwise:J175800.00+044500.0', 'allwise:J1758'], mag: 15.1, positions: [{ epoch: 2025.9, ra: 269.5, dec: 4.75 }] }],
});
const k: KnownState = { state: 'ok', ...reply };

describe('a ringed known source in the passport', () => {
  it('opens with its Gaia name, Gaia and SIMBAD held, and nothing asked again for them', () => {
    const h = heldAt(k, 269.42037, 4.73183, 2025.9)!;
    expect(h.name).toBe('Gaia DR3 4472832508888391168');
    expect(h.covered).toEqual(['gaia', 'simbad']);
    expect(h.simbad).toBe('none');
    // no /api/v1/context call for SIMBAD and Gaia: the passport has nothing to ask
    expect(identifySkip(h)).toBeNull();
    const id = heldIdentified(h);
    expect(nameOf({ ra: 269.42037, dec: 4.73183 } as never, id, h)).toBe('Gaia DR3 4472832508888391168');
    expect(heldRow('simbad', h)).toBe('no entry');
    expect(heldRow('gaia', h)).toBe('4472832508888391168 · G 12.31 · parallax 1.42 mas · motion 0.01 ″/yr');
    // the other catalogues are not held: asked
    expect(heldRow('twomass', h)).toBeNull();
  });

  it('a star with a SIMBAD name is called by it', () => {
    const h = heldAt(k, 269.44629, 4.76792, 2025.9)!;
    expect(h.name).toBe("Barnard's Star");
    expect(h.simbad).toEqual({ name: "Barnard's Star", type: 'BY*' });
  });

  it('a deeper match is named by its catalogue id and SIMBAD is held as no entry', () => {
    const h = heldAt(k, 269.5, 4.75, 2025.9)!;
    expect(h.name).toBe('CatWISE2020 J175800.00+044500.0');
    expect(h.covered).toEqual(['simbad']);
    expect(identifySkip(h)).toEqual(['simbad']);
  });

  it('when SIMBAD did not answer for the field, it is asked (and only it)', () => {
    const h = heldAt({ ...k, answered: { gaia: true, simbad: false, jpl: true } }, 269.42037, 4.73183, 2025.9)!;
    expect(h.covered).toEqual(['gaia']);
    expect(identifySkip(h)).toEqual(['gaia']);
  });

  it('only a source no catalogue knows is "no catalogue name here"', () => {
    expect(heldAt(k, 269.3, 4.6, 2025.9)).toBeNull();
    expect(nameOf({ ra: 269.3, dec: 4.6 } as never, { simbad: { state: 'none' }, gaia: { state: 'none' } }, null)).toBe('no catalogue name here');
  });
});
