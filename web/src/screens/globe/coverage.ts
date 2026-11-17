// The Coverage layer of the globe: the fixed grid of 20′ squares the machine searches, drawn over any background and coloured by
// what has happened to each square (searched by the machine, viewed by people, has finds, being searched).
//
// One source for the grid: the service. The squares that are not new come from `GET /api/grid/coverage?corners=1` with their centres and
// corners, and are drawn from those. The lattice itself (the thin frames of the new squares when zoomed in, the square under a click) is
// built from the numbers `GET /api/grid/meta` gives in `scheme_params` (band height, number of bands, the first declination); the one
// rule kept here is the scheme's column count, max(1, round(360·cos(dec_mid) / cell_deg)) equal steps of right ascension from RA 0,
// with ids "{band:03d}-{col:04d}". A scheme of another name switches the grid list off (the list of searched fields stands in). The
// unit test checks the lattice against corners the API gave. The card of a square is `GET /api/grid/cell/{id}`.
//
// Readable at every zoom, and never larger than the truth: where a square is under 6 px on screen each square that is not new is one
// small mark of a fixed size (5 px) at its place, so 44 searched squares of 371 272 look like 44 points on the whole sky; from 6 px
// each square that is not new is drawn at its true outline; from 22 px the lattice is drawn too (new squares as thin frames).
// Nothing is gathered into blocks: a block filled because one square in it is searched overstates the searched sky a hundredfold.
// Drawing is a few thousand projected points per frame at most (under 2 ms), so turning the globe costs the same with the layer on.

import { D2R } from '../../lib/sky';

export const SCHEME = 'dec20';
/** The numbers of the scheme; the values of `scheme_params` replace them when /api/grid/meta has answered. */
export let CELL_DEG = 1 / 3;
export let N_BANDS = 540;
let DEC0 = -90;

export type CellState = 'new' | 'queued' | 'searching' | 'searched' | 'failed';
export interface CovCell {
  id: string;
  state: CellState;
  finds: number;
  viewers: number;
  /** from the API (`corners=1`): the centre and the four corners, SW, SE, NE, NW */
  ra?: number;
  dec?: number;
  corners?: [number, number][];
}
/** The card of one square (`GET /api/grid/cell/{id}`); `finds` are finding ids. */
export interface CellCard {
  id: string;
  ra: number;
  dec: number;
  state: CellState;
  n_finds: number;
  finds: string[];
  viewers: number;
  bar: Record<string, unknown> | null;
  searched_at: string | null;
  field_key: string | null;
  /** where the lab opens the square: its first quarter not looked through by this person, at 10′ */
  open: { ra: number; dec: number; fov: number; quarter?: string | null };
}

/** Python's round(): halves go to the even number (the service counts the columns with it). */
const roundEven = (x: number): number => {
  const r = Math.round(x);
  return Math.abs(x - Math.trunc(x)) === 0.5 && r % 2 !== 0 ? r - 1 : r;
};
const colsOf = (): number[] =>
  Array.from({ length: N_BANDS }, (_, b) => Math.max(1, roundEven((360 * Math.cos((DEC0 + (b + 0.5) * CELL_DEG) * D2R)) / CELL_DEG)));
let COLS: number[] = colsOf();
export const nCols = (band: number): number => COLS[band]!;
export let TOTAL = COLS.reduce((s, n) => s + n, 0);
/** Takes the scheme's numbers from /api/grid/meta (`scheme_params`); false when it is a scheme this layer cannot draw. */
export function setScheme(p: { name?: unknown; cell_deg?: unknown; n_bands?: unknown; dec0?: unknown } | null | undefined): boolean {
  if (!p) return true; // a service that gives no numbers: the scheme named dec20 as it is known here
  const cell = Number(p.cell_deg);
  const bands = Number(p.n_bands);
  const dec0 = Number(p.dec0);
  if (p.name !== SCHEME || !(cell > 0) || !Number.isInteger(bands) || bands < 1 || bands > 100000 || !Number.isFinite(dec0)) return false;
  if (cell !== CELL_DEG || bands !== N_BANDS || dec0 !== DEC0) {
    CELL_DEG = cell;
    N_BANDS = bands;
    DEC0 = dec0;
    COLS = colsOf();
    TOTAL = COLS.reduce((s, n) => s + n, 0);
  }
  return true;
}

export const cellId = (band: number, col: number): string => `${String(band).padStart(3, '0')}-${String(col).padStart(4, '0')}`;
export function parseId(id: string): { band: number; col: number } | null {
  const m = /^(\d{3})-(\d{4})$/.exec(id);
  if (!m) return null;
  const band = Number(m[1]);
  const col = Number(m[2]);
  return band < N_BANDS && col < COLS[band]! ? { band, col } : null;
}
export const bandOf = (dec: number): number => Math.min(N_BANDS - 1, Math.max(0, Math.floor((dec - DEC0) / CELL_DEG)));
/** The square a sky place lies in. */
export function cellAt(ra: number, dec: number): { band: number; col: number } {
  const band = bandOf(dec);
  const n = COLS[band]!;
  return { band, col: Math.floor((((ra % 360) + 360) % 360) / (360 / n)) % n };
}
/** The edges of a square, degrees (ra1 may be 360). */
export function cellBox(band: number, col: number): { ra0: number; ra1: number; dec0: number; dec1: number } {
  const w = 360 / COLS[band]!;
  return { ra0: col * w, ra1: (col + 1) * w, dec0: DEC0 + band * CELL_DEG, dec1: DEC0 + (band + 1) * CELL_DEG };
}
/** The quarters a person looks through (10′ × 10′), in the lab's order: north up, east left. */
export const QUARTERS = ['NE', 'NW', 'SE', 'SW'] as const;
export type Quarter = (typeof QUARTERS)[number];
/** The edges of a quarter of a square: its north or south half in Dec and east (larger RA) or west half in RA. */
export function quarterBox(band: number, col: number, q: Quarter): { ra0: number; ra1: number; dec0: number; dec1: number } {
  const b = cellBox(band, col);
  const dm = (b.dec0 + b.dec1) / 2;
  const rm = (b.ra0 + b.ra1) / 2;
  return {
    ra0: q[1] === 'E' ? rm : b.ra0,
    ra1: q[1] === 'E' ? b.ra1 : rm,
    dec0: q[0] === 'N' ? dm : b.dec0,
    dec1: q[0] === 'N' ? b.dec1 : dm,
  };
}
/** The quarter of a square that holds a sky place. */
export function quarterAt(band: number, col: number, ra: number, dec: number): Quarter {
  const b = cellBox(band, col);
  const r = ((ra % 360) + 360) % 360;
  return `${dec >= (b.dec0 + b.dec1) / 2 ? 'N' : 'S'}${r >= (b.ra0 + b.ra1) / 2 ? 'E' : 'W'}` as Quarter;
}
export function cellCentre(band: number, col: number): { ra: number; dec: number } {
  const b = cellBox(band, col);
  return { ra: ((b.ra0 + b.ra1) / 2) % 360, dec: (b.dec0 + b.dec1) / 2 };
}

// ---------------------------------------------------------------------------------------------------------------------------
// data

export interface Coverage {
  /** every square that is not new */
  cells: Map<string, CovCell>;
  /** where the list came from: the grid API, or (until it answers) the list of searched fields */
  source: 'grid' | 'searched';
  total: number;
  counts: { searched: number; viewed: number; with_finds: number } | null;
  /** grows with every new list: the gathered blocks are rebuilt when it changes */
  version: number;
}
let data: Coverage | null = null;
let req: Promise<Coverage | null> | null = null;
let version = 0;
export const coverageNow = (): Coverage | null => data;

type J = Record<string, unknown>;
const getJson = async (url: string): Promise<J | null> => {
  const r = await fetch(url).catch(() => null);
  if (!r || !r.ok) return null;
  return (await r.json().catch(() => null)) as J | null;
};

/** The squares of the list of searched fields (`GET /api/v1/searched`), for a service that has no grid yet: a finished field makes its square searched. */
export function fromSearched(fields: { ra: number; dec: number; state: string; found: number }[]): Map<string, CovCell> {
  const out = new Map<string, CovCell>();
  for (const f of fields) {
    const { band, col } = cellAt(f.ra, f.dec);
    const id = cellId(band, col);
    const old = out.get(id);
    const state: CellState = f.state === 'done' ? 'searched' : 'searching';
    out.set(id, { id, state: old?.state === 'searched' ? 'searched' : state, finds: (old?.finds ?? 0) + (f.found || 0), viewers: 0 });
  }
  return out;
}

/** Reads the coverage (again when `fresh`); null when the service gave nothing. */
export function loadCoverage(fresh = false): Promise<Coverage | null> {
  if (req && !fresh) return req;
  const mine: Promise<Coverage | null> = (async () => {
    const [cov, meta] = await Promise.all([getJson('/api/grid/coverage?corners=1'), getJson('/api/grid/meta')]);
    if (
      cov &&
      Array.isArray(cov['cells']) &&
      (!meta || (meta['scheme'] === SCHEME && setScheme(meta['scheme_params'] as Parameters<typeof setScheme>[0])))
    ) {
      const cells = new Map<string, CovCell>();
      for (const c of cov['cells'] as CovCell[]) if (parseId(c.id)) cells.set(c.id, c);
      const counts = (meta?.['counts'] as Coverage['counts']) ?? null;
      data = { cells, source: 'grid', total: Number(meta?.['total']) || TOTAL, counts, version: ++version };
      return data;
    }
    const s = await getJson('/api/v1/searched');
    if (s && Array.isArray(s['fields'])) {
      data = {
        cells: fromSearched(s['fields'] as { ra: number; dec: number; state: string; found: number }[]),
        source: 'searched',
        total: TOTAL,
        counts: null,
        version: ++version,
      };
      return data;
    }
    return data;
  })();
  req = mine;
  void mine.then((d) => {
    if (!d && req === mine) req = null;
  });
  return mine;
}

/** The card of a square; when the service has no grid, what the coverage list itself says about it. */
export async function loadCell(id: string): Promise<CellCard | null> {
  const p = parseId(id);
  if (!p) return null;
  const c = cellCentre(p.band, p.col);
  const j = await getJson(`/api/grid/cell/${encodeURIComponent(id)}`);
  if (j && typeof j['id'] === 'string') {
    const finds = Array.isArray(j['finds']) ? (j['finds'] as string[]) : [];
    return {
      id,
      ra: Number(j['ra'] ?? c.ra),
      dec: Number(j['dec'] ?? c.dec),
      state: (j['state'] as CellState) ?? 'new',
      n_finds: Number(j['n_finds'] ?? (Array.isArray(j['finds']) ? finds.length : j['finds'])) || 0,
      finds,
      viewers: Number(j['viewers']) || 0,
      bar: (j['bar'] as Record<string, unknown> | null) ?? null,
      searched_at: (j['searched_at'] as string | null) ?? null,
      field_key: (j['field_key'] as string | null) ?? null,
      open: (j['open'] as CellCard['open']) ?? firstQuarter(id, c),
    };
  }
  const k = data?.cells.get(id);
  return {
    id,
    ra: c.ra,
    dec: c.dec,
    state: k?.state ?? 'new',
    n_finds: k?.finds ?? 0,
    finds: [],
    viewers: k?.viewers ?? 0,
    bar: null,
    searched_at: null,
    field_key: null,
    open: firstQuarter(id, c),
  };
}

/** The first quarter of a square (NE), at 10′: where the lab opens it before the service has said which quarter is next. */
export function firstQuarter(id: string, c: { ra: number; dec: number }): CellCard['open'] {
  const p = parseId(id);
  if (!p) return { ra: c.ra, dec: c.dec, fov: 10, quarter: null };
  const b = quarterBox(p.band, p.col, 'NE');
  return { ra: ((((b.ra0 + b.ra1) / 2) % 360) + 360) % 360, dec: (b.dec0 + b.dec1) / 2, fov: 10, quarter: 'NE' };
}

/** The bar of a square's search in plain words (the measured depth of the search), or '' when the service kept none. */
export function barText(bar: Record<string, unknown> | null): string {
  if (!bar) return '';
  if (bar['kind'] === 'sweep') {
    const parts = [];
    if (bar['mover_sigma'] != null) parts.push(`movers above ${Number(bar['mover_sigma'])}σ`);
    if (bar['mover_shift_arcsec'] != null) parts.push(`shift of ${Number(bar['mover_shift_arcsec'])}″ or more`);
    if (bar['passes'] != null) parts.push(`${Number(bar['passes'])} passes`);
    return parts.join(' · ');
  }
  if (bar['kind'] === 'import') {
    const fov = bar['fov_arcmin'] != null ? `a ${Number(bar['fov_arcmin'])}′ field` : 'a field';
    return `${fov} at the centre searched${bar['partial'] ? ' (part of the square)' : ''}`;
  }
  return Object.entries(bar)
    .filter(([, v]) => typeof v === 'number' || typeof v === 'string')
    .map(([k, v]) => `${k.replace(/_/g, ' ')} ${String(v)}`)
    .join(' · ');
}

// ---------------------------------------------------------------------------------------------------------------------------
// what a square (or a block of squares) is, for its colour

export type Kind = 'finds' | 'viewed' | 'searched' | 'searching' | 'failed';
export const KIND_COLOUR: Record<Kind, string> = {
  finds: '#ffd166',
  viewed: '#b69cff',
  searched: '#5fe3d6',
  searching: '#ffffff',
  failed: '#ff7b7b',
};
/** the short words of the legend */
export const KIND_SHORT: Record<Kind, string> = {
  finds: 'Has finds',
  viewed: 'Viewed',
  searched: 'Searched',
  searching: 'Being searched',
  failed: 'Failed',
};
export const KIND_WORD: Record<Kind, string> = {
  finds: 'Has finds',
  viewed: 'Viewed by people',
  searched: 'Searched by the machine',
  searching: 'Being searched',
  failed: 'Search failed',
};
/** Has finds first, then viewed by people, then searched; a square in the queue or in the search is "being searched". */
export function kindOf(c: { state: CellState; finds: number; viewers: number }): Kind | null {
  if (c.finds > 0) return 'finds';
  if (c.viewers > 0) return 'viewed';
  if (c.state === 'searched') return 'searched';
  if (c.state === 'searching' || c.state === 'queued') return 'searching';
  if (c.state === 'failed') return 'failed';
  return null;
}

/** from this size of a square on screen (CSS pixels) a square that is not new is drawn at its true outline; below it, as a mark of MARK_PX */
export const TRUE_PX = 6;
export const MARK_PX = 5;
/** from this size the lattice is drawn too (every square, new ones as thin frames): at most about 2 500 squares are in sight then */
export const REAL_PX = 22;
/** How the layer is drawn at a zoom. */
export function modeOf(cellPx: number): 'marks' | 'squares' | 'lattice' {
  return cellPx >= REAL_PX ? 'lattice' : cellPx >= TRUE_PX ? 'squares' : 'marks';
}
const placeOf = (c: CovCell): { ra: number; dec: number } | null => {
  if (c.ra != null && c.dec != null) return { ra: c.ra, dec: c.dec };
  const p = parseId(c.id);
  return p ? cellCentre(p.band, p.col) : null;
};

/** The square a click at a sky place opens: the square under it; where squares are marks, the marked square within 8 px of the click (the nearest), else the square under it. */
export function pickCell(cov: Coverage | null, ra: number, dec: number, cellPx: number): string {
  if (cov && modeOf(cellPx) === 'marks') {
    const tol = (8 / cellPx) * CELL_DEG;
    let best = '';
    let bd = tol;
    for (const c of cov.cells.values()) {
      if (!kindOf(c)) continue;
      const pl = placeOf(c);
      if (!pl || Math.abs(pl.dec - dec) > tol) continue;
      const d = sepDeg(pl.ra, pl.dec, ra, dec);
      if (d < bd) {
        bd = d;
        best = c.id;
      }
    }
    if (best) return best;
  }
  const c = cellAt(ra, dec);
  return cellId(c.band, c.col);
}

// ---------------------------------------------------------------------------------------------------------------------------
// drawing

export interface CovView {
  /** canvas position of a sky place and whether it is on the visible side */
  project: (ra: number, dec: number) => [number, number, boolean];
  /** centre of the view and the angle from it to the farthest corner of the picture, degrees (180: the whole sky, the Mollweide map) */
  ra: number;
  dec: number;
  radius: number;
  /** the size of one square on screen at the centre of the view, CSS pixels */
  cellPx: number;
  W: number;
  H: number;
  /** a flat map of the whole sky: a square that straddles RA 0 is not drawn across the map */
  flat: boolean;
  selected: string | null;
  font: string;
}

const sepDeg = (ra1: number, dec1: number, ra2: number, dec2: number): number => {
  const a = Math.sin(dec1 * D2R) * Math.sin(dec2 * D2R) + Math.cos(dec1 * D2R) * Math.cos(dec2 * D2R) * Math.cos((ra1 - ra2) * D2R);
  return Math.acos(Math.max(-1, Math.min(1, a))) / D2R;
};

/** The outline of a box of the sky as a path (its sides follow the parallels, cut into steps where they are long on screen); false when part of it is out of sight. */
function boxPath(ctx: CanvasRenderingContext2D, v: CovView, ra0: number, ra1: number, dec0: number, dec1: number, steps: number): boolean {
  const pts: [number, number][] = [];
  for (let s = 0; s <= steps; s++) pts.push([ra0 + ((ra1 - ra0) * s) / steps, dec0]);
  for (let s = steps; s >= 0; s--) pts.push([ra0 + ((ra1 - ra0) * s) / steps, dec1]);
  let first = true;
  let lx = 0;
  for (const [ra, de] of pts) {
    const p = v.project(ra, de);
    if (!p[2]) return false;
    if (!first && v.flat && Math.abs(p[0] - lx) > v.W / 3) return false;
    if (first) ctx.moveTo(p[0], p[1]);
    else ctx.lineTo(p[0], p[1]);
    first = false;
    lx = p[0];
  }
  ctx.closePath();
  return true;
}

let vx = new Float64Array(1024);

/** Draws the layer; returns how many squares or blocks it drew (for tests). */
export function drawCoverage(ctx: CanvasRenderingContext2D, cov: Coverage | null, v: CovView): number {
  const mode = modeOf(v.cellPx);
  let drawn = 0;
  ctx.save();
  ctx.lineJoin = 'round';
  if (mode !== 'lattice') {
    // only what is not new: a mark of a fixed size where a square is too small to see, its true outline from TRUE_PX
    if (cov) {
      const reach = v.radius + CELL_DEG + 0.5;
      for (const c of cov.cells.values()) {
        const kind = kindOf(c);
        const pl = kind && placeOf(c);
        if (!kind || !pl) continue;
        if (v.radius < 179 && (Math.abs(pl.dec - v.dec) > reach || sepDeg(pl.ra, pl.dec, v.ra, v.dec) > reach)) continue;
        const col = KIND_COLOUR[kind];
        ctx.beginPath();
        if (mode === 'squares') {
          const p = parseId(c.id);
          const q = c.corners?.length === 4 ? c.corners.map(([ra, de]) => v.project(ra, de)) : null;
          if (q && q.every((t) => t[2]) && !(v.flat && Math.max(...q.map((t) => t[0])) - Math.min(...q.map((t) => t[0])) > v.W / 3)) {
            ctx.moveTo(q[0]![0], q[0]![1]);
            for (let i = 1; i < 4; i++) ctx.lineTo(q[i]![0], q[i]![1]);
            ctx.closePath();
          } else {
            const b = p && cellBox(p.band, p.col);
            if (!b || !boxPath(ctx, v, b.ra0, b.ra1, b.dec0, b.dec1, 1)) continue;
          }
          ctx.globalAlpha = kind === 'searching' ? 0.2 : 0.45;
          ctx.fillStyle = col;
          ctx.fill();
          ctx.globalAlpha = 1;
          ctx.strokeStyle = col;
          ctx.lineWidth = 1;
          ctx.stroke();
        } else {
          const p = v.project(pl.ra, pl.dec);
          if (!p[2] || p[0] < -MARK_PX || p[0] > v.W + MARK_PX || p[1] < -MARK_PX || p[1] > v.H + MARK_PX) continue;
          ctx.rect(Math.round(p[0]) - MARK_PX / 2, Math.round(p[1]) - MARK_PX / 2, MARK_PX, MARK_PX);
          ctx.globalAlpha = 1;
          ctx.lineWidth = 2;
          ctx.strokeStyle = 'rgba(0,0,0,.7)';
          ctx.stroke();
          ctx.fillStyle = kind === 'searching' ? 'rgba(255,255,255,.45)' : col;
          ctx.fill();
        }
        drawn++;
      }
    }
  } else {
    // every square in sight: thin frames, and a fill for the ones that are not new
    const b0 = bandOf(Math.max(-90, v.dec - v.radius));
    const b1 = bandOf(Math.min(89.9999, v.dec + v.radius));
    const frames: [number, number, number, number][] = [];
    for (let band = b0; band <= b1; band++) {
      const n = COLS[band]!;
      const w = 360 / n;
      const d0 = DEC0 + band * CELL_DEG;
      const cosd = Math.max(0.01, Math.cos(Math.min(89.9, Math.abs(d0 + CELL_DEG / 2)) * D2R));
      const span = v.radius / cosd + w;
      const all = span >= 180 || Math.abs(v.dec) + v.radius >= 89.5;
      const c0 = all ? 0 : Math.floor((v.ra - span) / w);
      const c1 = all ? n - 1 : Math.floor((v.ra + span) / w);
      // the corners along the lower and the upper edge of the band, each projected once (two squares share a corner)
      const m = c1 - c0 + 1;
      if (vx.length < (m + 1) * 4) vx = new Float64Array((m + 1) * 8);
      for (let q = 0; q <= m; q++) {
        const ra = (c0 + q) * w;
        const lo = v.project(ra, d0);
        const hi = v.project(ra, d0 + CELL_DEG);
        vx[q * 4] = lo[2] ? lo[0] : NaN;
        vx[q * 4 + 1] = lo[1];
        vx[q * 4 + 2] = hi[2] ? hi[0] : NaN;
        vx[q * 4 + 3] = hi[1];
      }
      for (let q = 0; q < m; q++) {
        const col = (((c0 + q) % n) + n) % n;
        // a square is drawn when its four corners are in sight and it touches the picture
        const ax = vx[q * 4]!;
        const ay = vx[q * 4 + 1]!;
        const bx = vx[q * 4 + 4]!;
        const by = vx[q * 4 + 5]!;
        const cx = vx[q * 4 + 6]!;
        const cy = vx[q * 4 + 7]!;
        const dx = vx[q * 4 + 2]!;
        const dy = vx[q * 4 + 3]!;
        if (ax !== ax || bx !== bx || cx !== cx || dx !== dx) continue;
        const p00: [number, number] = [ax, ay];
        const p10: [number, number] = [bx, by];
        const p11: [number, number] = [cx, cy];
        const p01: [number, number] = [dx, dy];
        const x0 = Math.min(ax, bx, cx, dx);
        const x1 = Math.max(ax, bx, cx, dx);
        const y0 = Math.min(ay, by, cy, dy);
        const y1 = Math.max(ay, by, cy, dy);
        if (x1 < 0 || x0 > v.W || y1 < 0 || y0 > v.H || (v.flat && x1 - x0 > v.W / 3)) continue;
        drawn++;
        const id = cellId(band, col);
        const c = cov?.cells.get(id);
        const kind = c ? kindOf(c) : null;
        if (kind) {
          // a square that is not new is drawn at the corners the API gave
          const q = c?.corners?.length === 4 ? c.corners.map(([ra, de]) => v.project(ra, de)) : null;
          const [a0, a1, a2, a3] = q && q.every((t) => t[2]) ? q : [p00, p10, p11, p01];
          ctx.beginPath();
          ctx.moveTo(a0![0], a0![1]);
          ctx.lineTo(a1![0], a1![1]);
          ctx.lineTo(a2![0], a2![1]);
          ctx.lineTo(a3![0], a3![1]);
          ctx.closePath();
          // the larger a square is on screen, the lighter its fill: zoomed in, the sky under it must stay readable
          ctx.globalAlpha = (kind === 'searching' ? 0.14 : 0.3) * Math.max(0.2, Math.min(1, 48 / v.cellPx));
          ctx.fillStyle = KIND_COLOUR[kind];
          ctx.fill();
          ctx.globalAlpha = 1;
          ctx.strokeStyle = KIND_COLOUR[kind];
          ctx.lineWidth = 1.5;
          if (kind === 'searching') ctx.setLineDash([5, 4]);
          ctx.stroke();
          ctx.setLineDash([]);
          if (v.cellPx >= 70 && c) {
            ctx.font = `600 11px ${v.font}`;
            ctx.lineWidth = 3;
            ctx.strokeStyle = '#000';
            ctx.fillStyle = KIND_COLOUR[kind];
            const t = c.finds > 0 ? `${c.finds} find${c.finds === 1 ? '' : 's'}` : KIND_WORD[kind].split(' ')[0]!;
            ctx.strokeText(t, x0 + 5, y1 - 6);
            ctx.fillText(t, x0 + 5, y1 - 6);
          }
        } else
          frames.push(
            [p00[0], p00[1], p10[0], p10[1]],
            [p00[0], p00[1], p01[0], p01[1]],
            [p10[0], p10[1], p11[0], p11[1]],
            [p01[0], p01[1], p11[0], p11[1]],
          );
      }
    }
    // the frames of the new squares in one stroke
    ctx.beginPath();
    for (const f of frames) {
      ctx.moveTo(f[0], f[1]);
      ctx.lineTo(f[2], f[3]);
    }
    ctx.globalAlpha = 1;
    ctx.strokeStyle = 'rgba(255,255,255,.2)';
    ctx.lineWidth = 1;
    ctx.stroke();
  }
  // the selected square: its real outline, or a ring where it is too small to see
  const sel = v.selected ? parseId(v.selected) : null;
  if (sel) {
    const b = cellBox(sel.band, sel.col);
    ctx.globalAlpha = 1;
    ctx.beginPath();
    const ok = v.cellPx >= 9 && boxPath(ctx, v, b.ra0, b.ra1, b.dec0, b.dec1, 1);
    if (!ok) {
      const c = cellCentre(sel.band, sel.col);
      const p = v.project(c.ra, c.dec);
      ctx.beginPath();
      if (p[2]) ctx.arc(p[0], p[1], 8, 0, 7);
    }
    ctx.lineWidth = 4;
    ctx.strokeStyle = 'rgba(0,0,0,.75)';
    ctx.stroke();
    ctx.lineWidth = 2;
    ctx.strokeStyle = '#fff';
    ctx.stroke();
  }
  ctx.restore();
  return drawn;
}
