// A layer baked as an EQUIRECTANGULAR (plate carrée) pyramid of 512 px tiles: x = RA/360 (RA to the right, 0h at the left edge), y = (90 − Dec)/180,
// level l has 2^l × 2^(l−1) tiles, and a full-sky base image. The sampler reads it bilinearly across tile edges (the four texels may lie in four tiles; RA wraps,
// Dec clamps), from the finest level that has the tile at hand, and asks for the tiles it lacks.

export interface EquiSpec {
  id: string;
  label: string;
  credit?: string;
  size: number;
  min: number;
  max: number;
  /** tile address with {l} (level), {x} (column from the left) and {y} (row from the top) */
  url: string;
  /** a full-sky image of the whole map (one request), when the service has one */
  base?: string;
  gain: number;
  black: number;
}

const specs = new Map<string, EquiSpec>();
export const equiOf = (id: string): EquiSpec | undefined => specs.get(id);
export const registerEqui = (s: EquiSpec): void => {
  specs.set(s.id, s);
};

interface Tile {
  w: number;
  h: number;
  d: Uint8ClampedArray;
  used: number;
}
type Slot = Tile | null;

const tiles = new Map<string, Slot>();
const inflight = new Set<string>();
const failed = new Map<string, number>();
const queue = new Map<string, { id: string; key: string; url: string; pri: number; gen: number; seq: number }>();
const baseImg = new Map<string, Slot>();
const listeners = new Set<() => void>();
const MAX_INFLIGHT = 8;
const CAP = 160;
const RETRY_MS = 15000;
let gen = 0;
let seq = 0;
let stamp = 0;
const notify = () => listeners.forEach((f) => f());

/** called whenever a tile arrives (or fails); returns the function that stops it */
export function onEquiTile(f: () => void): () => void {
  listeners.add(f);
  return () => listeners.delete(f);
}
/** a new view: requests of views two frames old or more are dropped from the queue */
export function equiFrame(): void {
  gen++;
  stamp = performance.now();
}

let decoder: HTMLCanvasElement | null = null;
async function decode(b: Blob): Promise<Tile> {
  const bm = await createImageBitmap(b);
  decoder ??= document.createElement('canvas');
  decoder.width = bm.width;
  decoder.height = bm.height;
  const x = decoder.getContext('2d', { willReadFrequently: true })!;
  x.drawImage(bm, 0, 0);
  const w = bm.width;
  const h = bm.height;
  bm.close();
  return { w, h, d: x.getImageData(0, 0, w, h).data, used: performance.now() };
}

function pump(): void {
  while (inflight.size < MAX_INFLIGHT && queue.size) {
    let best: (typeof queue extends Map<string, infer V> ? V : never) | null = null;
    for (const [k, q] of queue) {
      if (q.gen < gen - 2) {
        queue.delete(k);
        continue;
      }
      if (!best || q.gen > best.gen || (q.gen === best.gen && (q.pri > best.pri || (q.pri === best.pri && q.seq > best.seq)))) best = q;
    }
    if (!best) return;
    queue.delete(best.key);
    const q = best;
    inflight.add(q.key);
    fetch(q.url, { mode: 'cors', credentials: 'omit' })
      .then(async (r) => {
        if (r.status === 404) return null;
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return decode(await r.blob());
      })
      .then((t) => {
        if (q.key.endsWith('/base')) baseImg.set(q.id, t);
        else {
          tiles.set(q.key, t);
          if (tiles.size > CAP) evict();
        }
      })
      .catch(() => failed.set(q.key, performance.now()))
      .finally(() => {
        inflight.delete(q.key);
        pump();
        notify();
      });
  }
}

function want(id: string, key: string, url: string, pri: number): void {
  if (inflight.has(key) || tiles.has(key)) return;
  const f = failed.get(key);
  if (f !== undefined && performance.now() - f < RETRY_MS) return;
  const q = queue.get(key);
  if (q) {
    q.gen = gen;
    q.pri = Math.max(q.pri, pri);
    q.seq = ++seq;
    return;
  }
  queue.set(key, { id, key, url, pri, gen, seq: ++seq });
  queueMicrotask(pump);
}

function evict(): void {
  const now = performance.now();
  const old: { k: string; used: number }[] = [];
  for (const [k, t] of tiles) if (t && now - t.used > 4000) old.push({ k, used: t.used });
  old.sort((a, b) => a.used - b.used);
  for (const e of old) {
    if (tiles.size <= CAP * 0.8) break;
    tiles.delete(e.k);
  }
}

/** Asks for the full-sky base image of a layer (first of all). */
export function equiPreload(id: string): void {
  const s = specs.get(id);
  if (s?.base && !baseImg.has(id)) want(id, `${id}/base`, s.base, 9);
}

const tileKey = (id: string, l: number, x: number, y: number) => `${id}/${l}/${x}_${y}`;
const tileUrl = (s: EquiSpec, l: number, x: number, y: number) => s.url.replace('{l}', String(l)).replace('{x}', String(x)).replace('{y}', String(y));

/** The level whose pixels match a globe of radius R px (one tile pixel about one screen pixel). */
export function equiLevelFor(s: EquiSpec, R: number): number {
  // a level whose texels are no smaller than a screen pixel (a little less is allowed): finer texels than the screen has pixels shimmer as the view moves
  const l = Math.log2((2 * Math.PI * R) / s.size) + 0.25;
  return Math.max(s.min, Math.min(s.max, Math.floor(l)));
}

function texel(t: Tile, i: number, j: number, k: number, out: Float32Array | number[], w: number, add: number): void {
  const q = (j * t.w + i) * 4;
  out[k] = (out[k] ?? 0) + t.d[q]! * w;
  out[k + 1] = (out[k + 1] ?? 0) + t.d[q + 1]! * w;
  out[k + 2] = (out[k + 2] ?? 0) + t.d[q + 2]! * w;
  void add;
}

export const EQUI_NOTHING = -2;

/**
 * Samples one equirect layer at one level (asking for the tiles it lacks). `at(ra°, dec°, out, k)` answers the colour bilinearly and returns the level it came from
 * (the base image: 0), or EQUI_NOTHING while nothing is at hand.
 */
export class EquiSampler {
  readonly s: EquiSpec;
  /** the level asked for */
  readonly level: number;
  private lastKey = '';
  private lastTile: Slot | undefined;
  constructor(id: string, R: number) {
    this.s = specs.get(id)!;
    this.level = equiLevelFor(this.s, R);
    stamp = performance.now();
  }
  private tile(l: number, tx: number, ty: number, ask: boolean): Slot | undefined {
    const key = tileKey(this.s.id, l, tx, ty);
    if (key === this.lastKey) return this.lastTile;
    let t = tiles.get(key);
    if (ask && t === undefined) want(this.s.id, key, tileUrl(this.s, l, tx, ty), 2 + (l <= this.s.min + 1 ? 4 : 0));
    if (t) t.used = stamp;
    this.lastKey = key;
    this.lastTile = t;
    return t;
  }
  at(ra: number, dec: number, out: Float32Array | number[], k: number): number {
    const S = this.s.size;
    let u = (((ra % 360) + 360) % 360) / 360;
    const v = (90 - Math.max(-90, Math.min(90, dec))) / 180;
    if (u >= 1) u = 0;
    // the finest level, down from the one asked for, whose tile under this place is at hand (the others are asked for)
    for (let l = this.level; l >= this.s.min; l--) {
      const W = S * 2 ** l;
      const H = W / 2;
      const X = u * W - 0.5;
      const Y = v * H - 0.5;
      const x0 = Math.floor(X);
      const y0 = Math.floor(Y);
      const cx = ((Math.round(X) % W) + W) % W;
      const cy = Math.max(0, Math.min(H - 1, Math.round(Y)));
      const c = this.tile(l, Math.floor(cx / S), Math.floor(cy / S), l >= this.level - 2);
      if (!c) continue;
      const fx = X - x0;
      const fy = Y - y0;
      out[k] = out[k + 1] = out[k + 2] = 0;
      const corners: [number, number, number][] = [
        [x0, y0, (1 - fx) * (1 - fy)],
        [x0 + 1, y0, fx * (1 - fy)],
        [x0, y0 + 1, (1 - fx) * fy],
        [x0 + 1, y0 + 1, fx * fy],
      ];
      for (const [xi0, yi0, w] of corners) {
        const xi = ((xi0 % W) + W) % W;
        const yi = Math.max(0, Math.min(H - 1, yi0));
        // a texel in a tile that is not here yet is read from the tile under the place itself (clamped inside it): no hole, no seam
        const t = this.tile(l, Math.floor(xi / S), Math.floor(yi / S), false) ?? c;
        const same = t === c && (Math.floor(xi / S) !== Math.floor(cx / S) || Math.floor(yi / S) !== Math.floor(cy / S));
        const i = same ? Math.max(0, Math.min(S - 1, xi - Math.floor(cx / S) * S)) : xi % S;
        const j = same ? Math.max(0, Math.min(S - 1, yi - Math.floor(cy / S) * S)) : yi % S;
        texel(t, i, j, k, out, w, 0);
      }
      return l;
    }
    // no tile at any level: the full-sky base image, bilinear, wrapping in RA
    const b = baseImg.get(this.s.id);
    if (b) {
      const X = u * b.w - 0.5;
      const Y = v * b.h - 0.5;
      const x0 = Math.floor(X);
      const y0 = Math.floor(Y);
      const fx = X - x0;
      const fy = Y - y0;
      out[k] = out[k + 1] = out[k + 2] = 0;
      for (const [xi0, yi0, w] of [
        [x0, y0, (1 - fx) * (1 - fy)],
        [x0 + 1, y0, fx * (1 - fy)],
        [x0, y0 + 1, (1 - fx) * fy],
        [x0 + 1, y0 + 1, fx * fy],
      ] as [number, number, number][]) {
        const xi = ((xi0 % b.w) + b.w) % b.w;
        const yi = Math.max(0, Math.min(b.h - 1, yi0));
        texel(b, xi, yi, k, out, w, 0);
      }
      return 0;
    }
    return EQUI_NOTHING;
  }
}
