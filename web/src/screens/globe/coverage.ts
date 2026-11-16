// The Coverage layer of the globe: the fixed grid of 20′ squares the machine searches, drawn over any background and coloured by
// what has happened to each square (searched by the machine, viewed by people, has finds, being searched).
//
// The grid is the service's scheme `dec20` (service/grid.py): 540 declination bands 20′ high from the south pole; band b is split
// into max(1, round(360·cos(dec_mid)·3)) equal steps of right ascension from RA 0; a square's id is "{band:03d}-{col:04d}".
// `GET /api/grid/coverage` lists every square that is not new by id only, so the places of the squares are worked out here from the
// same scheme (the unit test checks it against corners the API gave). The card of a square is `GET /api/grid/cell/{id}`.
//
// Readable at every zoom: where a square is 22 px or more on screen its real outline is drawn (every square, new ones as a thin
// frame); below that the squares that are not new are gathered into blocks of 2×2, 4×4, … squares, each block at least 12 px on
// screen and filled by what it holds. Drawing is a few thousand projected points per frame at most (under 2 ms), so turning the globe
// costs the same with the layer on.

import { D2R } from '../../lib/sky';

export const CELL_DEG = 1 / 3;
export const N_BANDS = 540;
export const SCHEME = 'dec20';

export type CellState = 'new' | 'queued' | 'searching' | 'searched' | 'failed';
export interface CovCell {
  id: string;
  state: CellState;
  finds: number;
  viewers: number;
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
  open: { ra: number; dec: number; fov: number };
}

/** Python's round(): halves go to the even number (the service counts the columns with it). */
const roundEven = (x: number): number => {
  const r = Math.round(x);
  return Math.abs(x - Math.trunc(x)) === 0.5 && r % 2 !== 0 ? r - 1 : r;
};
const COLS: number[] = Array.from({ length: N_BANDS }, (_, b) => Math.max(1, roundEven((360 * Math.cos((-90 + (b + 0.5) * CELL_DEG) * D2R)) / CELL_DEG)));
export const nCols = (band: number): number => COLS[band]!;
export const TOTAL = COLS.reduce((s, n) => s + n, 0);

export const cellId = (band: number, col: number): string => `${String(band).padStart(3, '0')}-${String(col).padStart(4, '0')}`;
export function parseId(id: string): { band: number; col: number } | null {
  const m = /^(\d{3})-(\d{4})$/.exec(id);
  if (!m) return null;
  const band = Number(m[1]);
  const col = Number(m[2]);
  return band < N_BANDS && col < COLS[band]! ? { band, col } : null;
}
export const bandOf = (dec: number): number => Math.min(N_BANDS - 1, Math.max(0, Math.floor((dec + 90) / CELL_DEG)));
/** The square a sky place lies in. */
export function cellAt(ra: number, dec: number): { band: number; col: number } {
  const band = bandOf(dec);
  const n = COLS[band]!;
  return { band, col: Math.floor((((ra % 360) + 360) % 360) / (360 / n)) % n };
}
/** The edges of a square, degrees (ra1 may be 360). */
export function cellBox(band: number, col: number): { ra0: number; ra1: number; dec0: number; dec1: number } {
  const w = 360 / COLS[band]!;
  return { ra0: col * w, ra1: (col + 1) * w, dec0: -90 + band * CELL_DEG, dec1: -90 + (band + 1) * CELL_DEG };
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
    const [cov, meta] = await Promise.all([getJson('/api/grid/coverage'), getJson('/api/grid/meta')]);
    if (cov && Array.isArray(cov['cells']) && (!meta || meta['scheme'] === SCHEME)) {
      const cells = new Map<string, CovCell>();
      for (const c of cov['cells'] as CovCell[]) if (parseId(c.id)) cells.set(c.id, c);
      const counts = (meta?.['counts'] as Coverage['counts']) ?? null;
      data = { cells, source: 'grid', total: Number(meta?.['total']) || TOTAL, counts, version: ++version };
      return data;
    }
    const s = await getJson('/api/v1/searched');
    if (s && Array.isArray(s['fields'])) {
      data = { cells: fromSearched(s['fields'] as { ra: number; dec: number; state: string; found: number }[]), source: 'searched', total: TOTAL, counts: null, version: ++version };
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
      open: (j['open'] as CellCard['open']) ?? { ra: c.ra, dec: c.dec, fov: 20 },
    };
  }
  const k = data?.cells.get(id);
  return { id, ra: c.ra, dec: c.dec, state: k?.state ?? 'new', n_finds: k?.finds ?? 0, finds: [], viewers: k?.viewers ?? 0, bar: null, searched_at: null, field_key: null, open: { ra: c.ra, dec: c.dec, fov: 20 } };
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
export const KIND_COLOUR: Record<Kind, string> = { finds: '#ffd166', viewed: '#b69cff', searched: '#5fe3d6', searching: '#ffffff', failed: '#ff7b7b' };
/** the short words of the legend */
export const KIND_SHORT: Record<Kind, string> = { finds: 'Has finds', viewed: 'Viewed', searched: 'Searched', searching: 'Being searched', failed: 'Failed' };
export const KIND_WORD: Record<Kind, string> = { finds: 'Has finds', viewed: 'Viewed by people', searched: 'Searched by the machine', searching: 'Being searched', failed: 'Search failed' };
/** Has finds first, then viewed by people, then searched; a square in the queue or in the search is "being searched". */
export function kindOf(c: { state: CellState; finds: number; viewers: number }): Kind | null {
  if (c.finds > 0) return 'finds';
  if (c.viewers > 0) return 'viewed';
  if (c.state === 'searched') return 'searched';
  if (c.state === 'searching' || c.state === 'queued') return 'searching';
  if (c.state === 'failed') return 'failed';
  return null;
}
const RANK: Record<Kind, number> = { finds: 4, viewed: 3, searching: 2, searched: 1, failed: 0 };

interface Block {
  ra0: number;
  ra1: number;
  dec0: number;
  dec1: number;
  n: number;
  kind: Kind;
  /** the square a click on the block opens: the one with the most finds, else the first */
  best: string;
  bestFinds: number;
}
/** a square this large on screen (CSS pixels) is drawn as itself: at most about 2 500 squares are in sight then */
export const REAL_PX = 22;
/** The side of a block in squares at a zoom: 1 when a square is REAL_PX or more, else the power of two that makes a block 12 px or more. */
export function blockSide(cellPx: number): number {
  if (cellPx >= REAL_PX) return 1;
  let k = 2;
  while (k * cellPx < 12 && k < 256) k *= 2;
  return k;
}
/** The block (of side k squares) a place lies in: its key and its edges. */
function blockAt(k: number, ra: number, dec: number): { key: string; ra0: number; ra1: number; dec0: number; dec1: number } {
  const g = Math.floor(bandOf(dec) / k);
  const b0 = g * k;
  const b1 = Math.min(N_BANDS, b0 + k);
  const mid = Math.min(N_BANDS - 1, b0 + (k >> 1));
  const n = Math.max(1, Math.round(COLS[mid]! / k));
  const w = 360 / n;
  const c = Math.floor((((ra % 360) + 360) % 360) / w) % n;
  return { key: `${g}-${c}`, ra0: c * w, ra1: (c + 1) * w, dec0: -90 + b0 * CELL_DEG, dec1: -90 + b1 * CELL_DEG };
}
let blocksOf: { k: number; version: number; blocks: Map<string, Block> } | null = null;
function blocks(cov: Coverage, k: number): Map<string, Block> {
  if (blocksOf && blocksOf.k === k && blocksOf.version === cov.version) return blocksOf.blocks;
  const out = new Map<string, Block>();
  for (const c of cov.cells.values()) {
    const kind = kindOf(c);
    const p = parseId(c.id);
    if (!kind || !p) continue;
    const ce = cellCentre(p.band, p.col);
    const b = blockAt(k, ce.ra, ce.dec);
    const old = out.get(b.key);
    if (!old) out.set(b.key, { ra0: b.ra0, ra1: b.ra1, dec0: b.dec0, dec1: b.dec1, n: 1, kind, best: c.id, bestFinds: c.finds });
    else {
      old.n++;
      if (RANK[kind] > RANK[old.kind]) old.kind = kind;
      if (c.finds > old.bestFinds) {
        old.best = c.id;
        old.bestFinds = c.finds;
      }
    }
  }
  blocksOf = { k, version: cov.version, blocks: out };
  return out;
}

/** The square a click at a sky place opens: the square under it, or at a wide zoom the best square of the block under it. */
export function pickCell(cov: Coverage | null, ra: number, dec: number, cellPx: number): string {
  const k = blockSide(cellPx);
  if (k > 1 && cov) {
    const b = blocks(cov, k).get(blockAt(k, ra, dec).key);
    if (b) return b.best;
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
  const k = blockSide(v.cellPx);
  let drawn = 0;
  ctx.save();
  ctx.lineJoin = 'round';
  if (k > 1) {
    // gathered blocks: only what is not new
    if (cov) {
      const size = k * v.cellPx;
      const reach = v.radius + (k * CELL_DEG) / 2 + 1;
      for (const b of blocks(cov, k).values()) {
        const cra = (b.ra0 + b.ra1) / 2;
        const cde = (b.dec0 + b.dec1) / 2;
        if (v.radius < 179 && sepDeg(cra, cde, v.ra, v.dec) > reach) continue;
        const col = KIND_COLOUR[b.kind];
        ctx.beginPath();
        let ok = false;
        if (size >= 7) ok = boxPath(ctx, v, b.ra0, b.ra1, b.dec0, b.dec1, 2);
        if (!ok) {
          // too small to show as a box (or cut by the limb): a mark of 5 px at its centre
          const p = v.project(cra, cde);
          if (!p[2]) continue;
          ctx.beginPath();
          ctx.rect(p[0] - 2.5, p[1] - 2.5, 5, 5);
        }
        ctx.globalAlpha = b.kind === 'searching' ? 0.28 : 0.4 + 0.4 * Math.min(1, b.n / (k * k));
        ctx.fillStyle = col;
        ctx.fill();
        ctx.globalAlpha = 0.95;
        ctx.strokeStyle = col;
        ctx.lineWidth = 1;
        ctx.stroke();
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
      const d0 = -90 + band * CELL_DEG;
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
          ctx.beginPath();
          ctx.moveTo(p00[0], p00[1]);
          ctx.lineTo(p10[0], p10[1]);
          ctx.lineTo(p11[0], p11[1]);
          ctx.lineTo(p01[0], p01[1]);
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
        } else frames.push([p00[0], p00[1], p10[0], p10[1]], [p00[0], p00[1], p01[0], p01[1]], [p10[0], p10[1], p11[0], p11[1]], [p01[0], p01[1], p11[0], p11[1]]);
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
