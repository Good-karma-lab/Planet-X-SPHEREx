import { readFileSync } from 'node:fs';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  TOTAL,
  barText,
  cellAt,
  cellBox,
  cellCentre,
  cellId,
  drawCoverage,
  fromSearched,
  kindOf,
  loadCell,
  loadCoverage,
  modeOf,
  parseId,
  pickCell,
  setScheme,
  type CovView,
} from '../../src/screens/globe/coverage';

// real answers of GET /api/grid/cells (near Barnard's Star, the south pole, RA 0 at Dec +61)
const REAL = JSON.parse(readFileSync(new URL('../fixtures/grid-cells.json', import.meta.url), 'utf8')) as {
  cells: { id: string; ra: number; dec: number; corners: [number, number][] }[];
};

describe('the grid of squares (scheme dec20)', () => {
  it('has as many squares as the service', () => {
    expect(TOTAL).toBe(371272);
  });
  it('places every square where the API says', () => {
    expect(REAL.cells.length).toBeGreaterThan(20);
    for (const c of REAL.cells) {
      const p = parseId(c.id)!;
      expect(p, c.id).not.toBeNull();
      const b = cellBox(p.band, p.col);
      const [sw, se, ne] = [c.corners[0]!, c.corners[1]!, c.corners[2]!];
      expect(b.ra0 % 360).toBeCloseTo(sw[0], 5);
      expect(b.ra1 % 360).toBeCloseTo(se[0] % 360, 5);
      expect(b.dec0).toBeCloseTo(sw[1], 5);
      expect(b.dec1).toBeCloseTo(ne[1], 5);
      const ce = cellCentre(p.band, p.col);
      expect(ce.dec).toBeCloseTo(c.dec, 5);
      expect(Math.abs(((ce.ra - c.ra + 540) % 360) - 180)).toBeLessThan(1e-5);
      const at = cellAt(ce.ra, ce.dec);
      expect(cellId(at.band, at.col)).toBe(c.id);
    }
  });
  it('refuses ids that are not squares', () => {
    expect(parseId('540-0000')).toBeNull();
    expect(parseId('000-0003')).toBeNull();
    expect(parseId('abc')).toBeNull();
  });
});

describe('what a square is', () => {
  it('has finds, then viewed, then searched', () => {
    expect(kindOf({ state: 'searched', finds: 2, viewers: 3 })).toBe('finds');
    expect(kindOf({ state: 'searched', finds: 0, viewers: 3 })).toBe('viewed');
    expect(kindOf({ state: 'searched', finds: 0, viewers: 0 })).toBe('searched');
    expect(kindOf({ state: 'queued', finds: 0, viewers: 0 })).toBe('searching');
    expect(kindOf({ state: 'new', finds: 0, viewers: 0 })).toBeNull();
  });
  it('says the bar in plain words', () => {
    expect(barText({ kind: 'sweep', mover_sigma: 5, mover_shift_arcsec: 3, passes: 4 })).toBe('moving dots 5× brighter than the noise · moved 3″ or more · 4 passes');
    expect(barText({ kind: 'import', searches: ['passes'], fov_arcmin: 5, partial: true })).toBe('a 5′ field at the centre searched (part of the square)');
    expect(barText(null)).toBe('');
  });
});

// a fake of the contract of PRODUCT_SPEC §8 (GET /api/grid/meta, /coverage, /cell/{id}), for a service that has it and for one that has not
function fakeApi(routes: Record<string, unknown>): void {
  vi.stubGlobal('fetch', async (url: string) => {
    const hit = Object.keys(routes).find((k) => url.startsWith(k));
    return hit ? { ok: true, json: async () => routes[hit] } : { ok: false, json: async () => ({}) };
  });
}
afterEach(() => vi.unstubAllGlobals());

describe('the coverage list', () => {
  it('reads the grid API', async () => {
    fakeApi({
      '/api/grid/meta': { scheme: 'dec20', scheme_params: { name: 'dec20', cell_deg: 1 / 3, n_bands: 540, dec0: -90 }, total: 371272, counts: { searched: 2, viewed: 1, with_finds: 1 }, rate_per_hour: 12 },
      '/api/grid/coverage': { cells: [{ id: '284-0805', ra: 269.498141, dec: 4.833333, corners: [[269.330855, 4.666667], [269.665428, 4.666667], [269.665428, 5.0], [269.330855, 5.0]], state: 'searched', finds: 14, viewers: 1 }, { id: '283-0806', state: 'searching', finds: 0, viewers: 0 }, { id: 'junk', state: 'searched', finds: 0, viewers: 0 }] },
      '/api/grid/cell/284-0805': { id: '284-0805', ra: 269.498141, dec: 4.833333, state: 'searched', finds: ['mover:1', 'film:2'], n_finds: 14, viewers: 1, bar: { kind: 'sweep', passes: 3 }, searched_at: '2026-11-16T15:58:50Z', field_key: 'k', open: { ra: 269.49814, dec: 4.83333, fov: 20 } },
    });
    const c = (await loadCoverage(true))!;
    expect(c.source).toBe('grid');
    expect(c.cells.get('284-0805')!.corners).toHaveLength(4);
    expect(c.cells.size).toBe(2);
    expect(c.counts?.searched).toBe(2);
    const card = (await loadCell('284-0805'))!;
    expect(card.n_finds).toBe(14);
    expect(card.finds).toHaveLength(2);
    expect(card.viewers).toBe(1);
    expect(card.open.fov).toBe(20);
    // a square the service knows nothing of is answered from the scheme
    const none = (await loadCell('283-0805'))!;
    expect(none.state).toBe('new');
    expect(none.dec).toBeCloseTo(4.5, 5);
  });
  it('leaves a scheme it does not know to the list of searched fields', async () => {
    fakeApi({
      '/api/grid/meta': { scheme: 'dec20', scheme_params: { name: 'hex7', cell_deg: 0.5, n_bands: 360, dec0: -90 }, total: 1 },
      '/api/grid/coverage': { cells: [{ id: '284-0805', state: 'searched', finds: 1, viewers: 0 }] },
      '/api/v1/searched': { count: 1, fields: [{ ra: 10, dec: 10, state: 'done', found: 0 }] },
    });
    expect((await loadCoverage(true))!.source).toBe('searched');
    expect(setScheme({ name: 'dec20', cell_deg: 1 / 3, n_bands: 540, dec0: -90 })).toBe(true);
    expect(TOTAL).toBe(371272);
  });
  it('falls back to the list of searched fields when the service has no grid', async () => {
    fakeApi({ '/api/v1/searched': { count: 2, fields: [{ ra: 269.45, dec: 4.75, state: 'done', found: 3 }, { ra: 10, dec: 10, state: 'running', found: 0 }] } });
    const c = (await loadCoverage(true))!;
    expect(c.source).toBe('searched');
    expect(c.cells.get('284-0805')).toMatchObject({ state: 'searched', finds: 3 });
    expect([...c.cells.values()].filter((x) => x.state === 'searching')).toHaveLength(1);
  });
  it('gathers fields into squares', () => {
    const m = fromSearched([{ ra: 269.45, dec: 4.75, state: 'done', found: 3 }, { ra: 269.5, dec: 4.8, state: 'done', found: 2 }]);
    expect(m.size).toBe(1);
    expect(m.get('284-0805')!.finds).toBe(5);
  });
});

describe('drawing at every zoom', () => {
  const ctx = new Proxy({}, { get: (_t, k) => (k === 'canvas' ? {} : () => undefined), set: () => true }) as unknown as CanvasRenderingContext2D;
  // a flat view: 1° is `scale` px, centred on (ra0, dec0)
  const view = (scale: number, ra0: number, dec0: number, radius: number): CovView => ({
    project: (ra, dec) => [500 - (((ra - ra0 + 540) % 360) - 180) * scale, 400 - (dec - dec0) * scale, true],
    ra: ra0, dec: dec0, radius, cellPx: scale / 3, W: 1000, H: 800, flat: false, selected: '284-0805', font: 'sans-serif',
  });
  it('draws marks below 6 px, true squares from 6 px, the lattice from 22 px', () => {
    expect(modeOf(2)).toBe('marks');
    expect(modeOf(5.9)).toBe('marks');
    expect(modeOf(6)).toBe('squares');
    expect(modeOf(21)).toBe('squares');
    expect(modeOf(22)).toBe('lattice');
  });
  it('draws every square in sight when zoomed in, one mark or one outline per square that is not new when zoomed out', async () => {
    fakeApi({ '/api/grid/coverage': { cells: [{ id: '284-0805', state: 'searched', finds: 14, viewers: 0 }, { id: '284-0806', state: 'searched', finds: 0, viewers: 0 }, { id: '100-0100', state: 'searched', finds: 0, viewers: 0 }] } });
    const c = (await loadCoverage(true))!;
    // 75 px per degree: a square is 25 px, the picture about 13° × 11°
    const near = drawCoverage(ctx, c, view(75, 269.5, 4.8, 9));
    expect(near).toBeGreaterThan(1000);
    expect(near).toBeLessThan(1700);
    // 6 px per degree: a square is 2 px; the two neighbours are two marks (never one block), the far square is out of sight
    expect(drawCoverage(ctx, c, view(6, 269.5, 4.8, 90))).toBe(2);
    // 30 px per degree: a square is 10 px; the two squares at their true outlines, no lattice
    expect(drawCoverage(ctx, c, view(30, 269.5, 4.8, 25))).toBe(2);
    // the whole sky: three marks
    expect(drawCoverage(ctx, c, { ...view(3, 180, 0, 180), flat: true })).toBe(3);
    // a click near a mark opens its square (the nearer of the two); far from any mark, and zoomed in, the square under the click
    expect(pickCell(c, 269.52, 4.8, 2)).toBe('284-0805');
    expect(pickCell(c, 269.9, 4.7, 2)).toBe('284-0806');
    expect(pickCell(c, 200, 40, 2)).toBe(pickCell(null, 200, 40, 2));
    expect(pickCell(c, 269.9, 4.7, 25)).toBe('284-0806');
    expect(pickCell(null, 269.9, 4.7, 2)).toBe('284-0806');
  });
});
