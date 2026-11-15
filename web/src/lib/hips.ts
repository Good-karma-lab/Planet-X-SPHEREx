// Tiles of all-sky surveys (HiPS), decoded to pixels once and kept in memory; a sampler that answers the colour of a survey at
// any sky position from the deepest tile at hand (bilinear inside a tile, so neither HEALPix diamonds nor tile seams show), and
// cutouts of a field drawn from those tiles.
//
// Where tiles come from: IRSA's HiPS servers directly (they answer any origin), and the service's tile cache
// (/api/v1/hips/...) when IRSA fails or is slower than FALLBACK_MS. The SPHEREx cube frames are read through the service only.
// At most MAX_INFLIGHT tiles are on their way at once; the tiles of the latest view go first, and requests of views the
// globe has left are dropped.


/** IRSA's HiPS root (CORS open). */
const IRSA = 'https://irsa.ipac.caltech.edu/data/hips/';
const FALLBACK_MS = 3000;
const MAX_INFLIGHT = 8;
/** decoded tiles kept (a 512² tile is 1 MB); tiles drawn in the last KEEP_MS are never dropped */
const CAP = 200;
const KEEP_MS = 4000;
/** a failed tile is not asked for again before this */
const RETRY_MS = 15000;

/** A layer baked into static tiles (the service's /tiles manifest): HEALPix nested, tone already applied, deeper orders read the live path. */
export interface Baked {
  label: string;
  credit?: string;
  frame: 'equatorial' | 'galactic';
  size: number;
  min: number;
  max: number;
  /** what the manifest still asks of the baked pixels on screen (the star map is dark by nature: gain 3); 1 and 0 when the tone is all baked in */
  gain: number;
  black: number;
  /** tile address with {o} (order), {d} (the 10 000-tile directory) and {i} (the tile) */
  url: string;
}
const baked = new Map<string, Baked>();
/** the baked layers of the service, once the manifest has been read (null: not read yet or none) */
export const bakedOf = (id: string): Baked | undefined => baked.get(id);
let manifest: Promise<boolean> | null = null;
/** the manifest has been answered (or has failed): whether a layer is there is known */
let manifestDone = false;
export const manifestKnown = (): boolean => manifestDone;
type ManifestJson = { layers?: Record<string, Partial<Baked> & { frame?: string }> } | null;
const MANIFEST_KEY = 'tiles.manifest.v1';

/** Takes the layers a manifest names into use; true when there are baked layers. */
function applyManifest(m: ManifestJson): boolean {

      for (const [id, b0] of Object.entries(m?.layers ?? {})) {
        const b = b0 as typeof b0 & { type?: string; base?: string; gain?: number; black?: number };
        // an equirectangular pyramid (the first star-map bake, svs-eq) is not used: the star map is the HEALPix layer `svs` (tone-mapped, orders 0-6), which the
        // globe's HEALPix sampler reads with no pole fans or streaks
        if (b.type === 'equirect') continue;
        if (!b.url || b.max == null) continue;
        const size = b.size ?? 512;
        baked.set(id, { label: b.label ?? id, credit: b.credit, frame: b.frame === 'galactic' ? 'galactic' : 'equatorial', size, min: b.min ?? 0, max: b.max, url: b.url, gain: Number(b.gain) > 0 ? Number(b.gain) : 1, black: Number(b.black) >= 0 ? Number(b.black) : 0 });
        // a layer only the baked tiles have gets its own entry; the surveys read live keep theirs (the live path serves the depths below)
        if (HIPS[id]) HIPS[id]!.frame = b.frame === 'galactic' ? 'galactic' : undefined;
        HIPS[id] ??= { max: b.max, min: b.min ?? 0, w: size, frame: b.frame === 'galactic' ? 'galactic' : undefined, bakedOnly: true };
      }
      // the shallowest baked tiles (the whole sky in a dozen files at order 0) are asked for at once, first of all: a layer is then never a few patches on empty sky
      for (const [id, b] of baked) {
        const n = 12 * 4 ** b.min;
        if (n <= 768) for (let i = 0; i < n; i++) want(id, b.min, i, 5);
      }
      listeners.forEach((f) => f());
      return baked.size > 0;
}

/** Reads the service's manifest of baked tiles once. True when the service has baked layers. The last manifest seen is kept in the browser and used at once (the
 *  first tiles are on their way before the answer arrives); index.html starts the request before the app's code is in (window.__manifest). */
export function loadManifest(): Promise<boolean> {
  manifest ??= (() => {
    let cached: string | null = null;
    try {
      cached = localStorage.getItem(MANIFEST_KEY);
      if (cached) {
        applyManifest(JSON.parse(cached) as ManifestJson);
        manifestDone = true;
      }
    } catch {
      cached = null;
    }
    const early = (window as unknown as { __manifest?: Promise<ManifestJson> }).__manifest;
    return (early ?? fetch('/tiles/manifest.json', { cache: 'no-cache' }).then((r) => (r.ok ? (r.json() as Promise<ManifestJson>) : null)))
      .then((m) => {
        if (!m) return baked.size > 0;
        const text = JSON.stringify(m);
        try {
          localStorage.setItem(MANIFEST_KEY, text);
        } catch {
          // kept next time
        }
        return text === cached ? baked.size > 0 : applyManifest(m);
      })
      .catch(() => false)
      .finally(() => {
        manifestDone = true;
      });
  })();
  return manifest;
}

interface Survey {
  /** path under IRSA's HiPS root; none: through the service only */
  path?: string;
  /** deepest order */
  max: number;
  /** shallowest tile order IRSA has (the service serves order 3 and deeper) */
  min: number;
  /** tile width, pixels */
  w: number;
  /** the sky frame of the tiles when it is not equatorial (hips_frame in the survey's properties file) */
  frame?: 'galactic';
  /** only the baked tiles have it (no live path, no Allsky image) */
  bakedOnly?: boolean;
}

/** The surveys the globe and the lab read (the same map the service's tile cache uses). The three colour surveys start at order 3: the whole-sky view is the Allsky image (as on the research globe), not IRSA's order 0–2 tiles, which carry a different colour cast. */
export const HIPS: Record<string, Survey> = {
  dss2: { path: 'CDS/DSS2/color', max: 9, min: 3, w: 512 },
  '2mass': { path: 'CDS/2MASS/Color', max: 9, min: 3, w: 512 },
  wise: { path: 'CDS/AllWISE/RGB-W4-W2-W1', max: 8, min: 3, w: 512 },
  dust: { path: 'CDS/IRIS/color', max: 3, min: 3, w: 256, frame: 'galactic' },
  dss2red: { path: 'CDS/DSS2/red', max: 9, min: 0, w: 512 },
  '2massk': { path: 'CDS/2MASS/K', max: 9, min: 0, w: 512 },
  wise1: { path: 'CDS/AllWISE/W1', max: 8, min: 0, w: 512 },
  sdss: { path: 'CDS/SDSS/DR9/color', max: 10, min: 3, w: 512 },
  spx8: { max: 5, min: 3, w: 512 },
  spx40: { max: 5, min: 3, w: 512 },
  spx88: { max: 5, min: 3, w: 512 },
};

// ---------------------------------------------------------------------------------------------------------------------------
// HEALPix (nested): continuous coordinates inside a base face

/** A sky position as a base face (0–11) and continuous coordinates x, y in [0, 1) inside it. */
export interface FacePt {
  face: number;
  x: number;
  y: number;
}

const ONE = 1 - 1e-12;

/** Face coordinates of a position given by its RA (degrees) and z = sin(dec); the integer pixel at order n is floor(x·2ⁿ), floor(y·2ⁿ). */
export function facePt(ra: number, z: number, out: FacePt = { face: 0, x: 0, y: 0 }): FacePt {
  const za = Math.abs(z);
  let tt = ((((ra % 360) + 360) % 360) / 90);
  if (tt >= 4) tt -= 4;
  if (za <= 2 / 3) {
    const a = 0.5 + tt - 0.75 * z;
    const b = 0.5 + tt + 0.75 * z;
    const ifp = Math.floor(a);
    const ifm = Math.floor(b);
    out.face = ifp === ifm ? (ifp % 4) + 4 : ifp < ifm ? ifp % 4 : (ifm % 4) + 8;
    out.x = Math.min(ONE, b - ifm);
    out.y = Math.min(ONE, Math.max(0, 1 - (a - ifp)));
  } else {
    const ntt = Math.min(Math.floor(tt), 3);
    const tp = tt - ntt;
    const tmp = Math.sqrt(3 * (1 - za));
    const p = Math.min(ONE, tp * tmp);
    const m = Math.min(ONE, (1 - tp) * tmp);
    if (z >= 0) {
      out.face = ntt;
      out.x = Math.min(ONE, 1 - m);
      out.y = Math.min(ONE, 1 - p);
    } else {
      out.face = ntt + 8;
      out.x = p;
      out.y = m;
    }
  }
  return out;
}

/** Spread the bits of v: … b2 b1 b0 → … b2 0 b1 0 b0 (orders up to 13). */
function spread(v: number): number {
  v &= 0x3fff;
  v = (v | (v << 8)) & 0x00ff00ff;
  v = (v | (v << 4)) & 0x0f0f0f0f;
  v = (v | (v << 2)) & 0x33333333;
  v = (v | (v << 1)) & 0x55555555;
  return v;
}

/** Nested pixel number at an order from face coordinates. */
export function pixAt(order: number, f: FacePt): number {
  const n = 1 << order;
  return f.face * n * n + spread(Math.floor(f.x * n)) + 2 * spread(Math.floor(f.y * n));
}

// ---------------------------------------------------------------------------------------------------------------------------
// tile store

interface Tile {
  w: number;
  h: number;
  d: Uint8ClampedArray;
  used: number;
  /** from the baked tiles: its tone is already applied */
  baked?: boolean;
}
/** null: the tile does not exist (outside the survey) */
type Slot = Tile | null;

interface Layer {
  levels: Map<number, Slot>[];
  allsky?: Slot;
}
const layers = new Map<string, Layer>();
const layer = (id: string): Layer => {
  let L = layers.get(id);
  if (!L) {
    L = { levels: Array.from({ length: 14 }, () => new Map<number, Slot>()) };
    layers.set(id, L);
  }
  return L;
};
let stored = 0;
let stamp = 0;

const ALLSKY = -1;
const keyOf = (id: string, o: number, i: number) => (o === ALLSKY ? `${id}/allsky` : `${id}/${o}/${i}`);

interface Want {
  id: string;
  o: number;
  i: number;
  pri: number;
  gen: number;
  seq: number;
}
const queue = new Map<string, Want>();
const inflight = new Set<string>();
const failed = new Map<string, number>();
let gen = 0;
let seq = 0;
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((f) => f());

/** IRSA health: after three failures or slow answers in a row the service goes first for a minute. */
let irsaBad = 0;
let irsaDownUntil = 0;
const irsaUp = () => performance.now() >= irsaDownUntil;
const irsaResult = (ok: boolean) => {
  if (ok) irsaBad = 0;
  else if (++irsaBad >= 3) {
    irsaDownUntil = performance.now() + 60000;
    irsaBad = 0;
  }
};

function want(id: string, o: number, i: number, pri: number): void {
  const key = keyOf(id, o, i);
  if (inflight.has(key)) return;
  const f = failed.get(key);
  if (f !== undefined && performance.now() - f < RETRY_MS) return;
  const q = queue.get(key);
  if (q) {
    q.gen = gen;
    q.pri = Math.max(q.pri, pri);
    q.seq = ++seq;
    return;
  }
  queue.set(key, { id, o, i, pri, gen, seq: ++seq });
  schedulePump();
}

let pumpQueued = false;
function schedulePump(): void {
  if (pumpQueued) return;
  pumpQueued = true;
  queueMicrotask(() => {
    pumpQueued = false;
    pump();
  });
}

function pump(): void {
  while (inflight.size < MAX_INFLIGHT && queue.size) {
    let best: Want | null = null;
    let bestKey = '';
    for (const [k, q] of queue) {
      if (q.gen < gen - 2) {
        queue.delete(k); // asked for by a view the globe has left
        continue;
      }
      if (!best || q.gen > best.gen || (q.gen === best.gen && (q.pri > best.pri || (q.pri === best.pri && q.seq > best.seq)))) {
        best = q;
        bestKey = k;
      }
    }
    if (!best) return;
    queue.delete(bestKey);
    start(bestKey, best);
  }
}

function urls(id: string, o: number, i: number): { baked?: string; irsa?: string; service?: string } {
  const s = HIPS[id]!;
  const b = baked.get(id);
  const bk = b && o !== ALLSKY && o >= b.min && o <= b.max ? b.url.replace('{o}', String(o)).replace('{d}', String(Math.floor(i / 10000) * 10000)).replace('{i}', String(i)) : undefined;
  const rel = o === ALLSKY ? 'Norder3/Allsky' : `Norder${o}/Dir${Math.floor(i / 10000) * 10000}/Npix${i}`;
  return {
    baked: bk,
    irsa: s.path ? `${IRSA}${s.path}/${rel}.jpg` : undefined,
    service: o === ALLSKY ? `/api/v1/hips/${id}/3/allsky` : o >= 3 ? `/api/v1/hips/${id}/${o}/${i}` : undefined,
  };
}

type Got = Blob | 'missing';
async function getBlob(url: string, signal: AbortSignal): Promise<Got> {
  const r = await fetch(url, { signal, mode: 'cors', credentials: 'omit' });
  if (r.status === 404) return 'missing';
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.blob();
}

/** The tile from IRSA, or from the service when IRSA fails or has not answered within FALLBACK_MS (whichever answers first
 *  wins). While IRSA is marked slow the service goes first and IRSA is the one asked after FALLBACK_MS. */
function fetchTile(id: string, o: number, i: number): Promise<{ g: Got; baked: boolean }> {
  const u = urls(id, o, i);
  // a baked tile is one static file: it goes first, and only a failure of the network (not a missing tile) sends the request on to the live path
  if (u.baked)
    return getBlob(u.baked, new AbortController().signal).then(
      (g) => ({ g, baked: true }),
      (e) => (u.irsa || u.service ? fetchLive(u, o === ALLSKY).then((g) => ({ g, baked: false })) : Promise.reject(e)),
    );
  return fetchLive(u, o === ALLSKY).then((g) => ({ g, baked: false }));
}

function fetchLive(u: { irsa?: string; service?: string }, allsky: boolean): Promise<Got> {
  const irsa = u.irsa;
  const service = u.service;
  if (!irsa) return getBlob(service!, new AbortController().signal);
  if (!service) return getBlob(irsa, new AbortController().signal);
  // the Allsky image (a megabyte, the first picture of a survey) is kept by the service: it goes first for it
  const first = irsaUp() && !allsky ? irsa : service;
  const second = first === irsa ? service : irsa;
  return new Promise<Got>((resolve, reject) => {
    const a1 = new AbortController();
    const a2 = new AbortController();
    let done = false;
    let running = 1;
    let asked2 = false;
    const ok = (g: Got) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      a1.abort();
      a2.abort();
      resolve(g);
    };
    const bad = (e: unknown) => {
      if (--running === 0 && !done) reject(e);
    };
    const ask2 = () => {
      if (asked2 || done) return;
      asked2 = true;
      running++;
      getBlob(second, a2.signal).then(
        (g) => {
          if (!done && second === irsa) irsaResult(true);
          ok(g);
        },
        bad,
      );
    };
    const timer = window.setTimeout(() => {
      if (first === irsa) irsaResult(false);
      ask2();
    }, FALLBACK_MS);
    getBlob(first, a1.signal).then(
      (g) => {
        if (!done && first === irsa) irsaResult(true);
        ok(g);
      },
      (e) => {
        if (!done && first === irsa && !a1.signal.aborted) irsaResult(false);
        ask2();
        bad(e);
      },
    );
  });
}

// The Allsky image is a mosaic of 64 x 64 tile cells (27 to a row). It is smoothed a little, as on the research globe (its JPEG blocks and chroma speckle would
// show on the whole-sky view), but cell by cell with the edges clamped inside the cell: a blur over the whole mosaic bleeds each tile into its neighbours and
// draws the cell edges as seams on the sphere.
const KERNEL = [0.0374, 0.2402, 0.4448, 0.2402, 0.0374]; // sigma 0.9 px

function softenCells(d: Uint8ClampedArray, w: number, h: number): void {
  const ts = Math.round(w / 27);
  if (ts < 8 || w % ts > 2) return; // not the mosaic of 27 cells per row: leave it alone
  const tmp = new Float32Array(ts * ts * 3);
  for (let r0 = 0; r0 + ts <= h; r0 += ts)
    for (let c0 = 0; c0 + ts <= w; c0 += ts) {
      // horizontal pass into tmp, edges clamped inside the cell
      for (let y = 0; y < ts; y++)
        for (let x = 0; x < ts; x++)
          for (let c = 0; c < 3; c++) {
            let v = 0;
            for (let k = -2; k <= 2; k++) v += KERNEL[k + 2]! * d[((r0 + y) * w + c0 + Math.min(ts - 1, Math.max(0, x + k))) * 4 + c]!;
            tmp[(y * ts + x) * 3 + c] = v;
          }
      // vertical pass back into the image
      for (let y = 0; y < ts; y++)
        for (let x = 0; x < ts; x++)
          for (let c = 0; c < 3; c++) {
            let v = 0;
            for (let k = -2; k <= 2; k++) v += KERNEL[k + 2]! * tmp[(Math.min(ts - 1, Math.max(0, y + k)) * ts + x) * 3 + c]!;
            d[((r0 + y) * w + c0 + x) * 4 + c] = v;
          }
    }
}

/** The SPHEREx all-sky cube (its frames spx*) has no data in the saturated core of a very bright star: the texels are black, ringed by the wings in the three colours. A black
 *  texel with bright texels on all four sides within a few texels is such a core, and it is white (a saturated core is white everywhere on the site). */
function fillCores(d: Uint8ClampedArray, w: number, h: number): void {
  const R = 10;
  const hole = (i: number) => d[i * 4]! + d[i * 4 + 1]! + d[i * 4 + 2]! <= 3;
  const bright = (i: number) => Math.max(d[i * 4]!, d[i * 4 + 1]!, d[i * 4 + 2]!) > 40;
  const fill: number[] = [];
  for (let y = R; y < h - R; y++)
    for (let x = R; x < w - R; x++) {
      const i = y * w + x;
      if (!hole(i)) continue;
      let ok = 0;
      for (let k = 1; k <= R && !(ok & 1); k++) if (bright(i - k)) ok |= 1;
      if (!(ok & 1)) continue;
      for (let k = 1; k <= R && !(ok & 2); k++) if (bright(i + k)) ok |= 2;
      if (!(ok & 2)) continue;
      for (let k = 1; k <= R && !(ok & 4); k++) if (bright(i - k * w)) ok |= 4;
      if (!(ok & 4)) continue;
      for (let k = 1; k <= R && !(ok & 8); k++) if (bright(i + k * w)) ok |= 8;
      if (ok === 15) fill.push(i);
    }
  for (const i of fill) {
    d[i * 4] = 255;
    d[i * 4 + 1] = 255;
    d[i * 4 + 2] = 255;
    d[i * 4 + 3] = 255;
  }
}

let decoder: HTMLCanvasElement | null = null;
async function decode(b: Blob, allsky = false, fromBaked = false, cores = false): Promise<Tile> {
  const bm = await createImageBitmap(b);
  decoder ??= document.createElement('canvas');
  decoder.width = bm.width;
  decoder.height = bm.height;
  const x = decoder.getContext('2d', { willReadFrequently: true })!;
  x.drawImage(bm, 0, 0);
  const w = bm.width;
  const h = bm.height;
  bm.close();
  const img = x.getImageData(0, 0, w, h);
  if (allsky) softenCells(img.data, w, h);
  if (cores) fillCores(img.data, w, h);
  return { w, h, d: img.data, used: performance.now(), baked: fromBaked };
}

function start(key: string, q: Want): void {
  inflight.add(key);
  fetchTile(q.id, q.o, q.i)
    .then(({ g, baked: bk }) => (g === 'missing' ? null : decode(g, q.o === ALLSKY, bk, q.id.startsWith('spx'))))
    .then((t) => put(q.id, q.o, q.i, t))
    .catch(() => failed.set(key, performance.now()))
    .finally(() => {
      inflight.delete(key);
      pump();
      notify();
    });
}

function put(id: string, o: number, i: number, t: Slot): void {
  const L = layer(id);
  if (o === ALLSKY) L.allsky = t;
  else {
    L.levels[o]!.set(i, t);
    if (t) stored++;
  }
  if (stored > CAP) evict();
}

/** Drops the tiles drawn longest ago (never the Allsky images, never a tile drawn in the last KEEP_MS). */
function evict(): void {
  const now = performance.now();
  const old: { m: Map<number, Slot>; i: number; used: number }[] = [];
  for (const L of layers.values())
    for (const m of L.levels) for (const [i, t] of m) if (t && now - t.used > KEEP_MS) old.push({ m, i, used: t.used });
  old.sort((a, b) => a.used - b.used);
  for (const e of old) {
    if (stored <= CAP * 0.8) break;
    e.m.delete(e.i);
    stored--;
  }
}

// ---------------------------------------------------------------------------------------------------------------------------
// sampling

/** Bilinear colour at continuous tile coordinates (X along the image rows, Y along the columns), clamped inside the tile
 *  (or inside a sub-square of an Allsky image), written to out[k..k+2]. */
function bilinear(t: Tile, X: number, Y: number, size: number, r0: number, c0: number, out: Float32Array | number[], k: number): void {
  let u = X - 0.5;
  let v = Y - 0.5;
  if (u < 0) u = 0;
  if (v < 0) v = 0;
  if (u > size - 1) u = size - 1;
  if (v > size - 1) v = size - 1;
  const i0 = Math.floor(u);
  const j0 = Math.floor(v);
  const i1 = i0 + 1 < size ? i0 + 1 : i0;
  const j1 = j0 + 1 < size ? j0 + 1 : j0;
  const fu = u - i0;
  const fv = v - j0;
  const d = t.d;
  const W = t.w;
  const q00 = ((r0 + i0) * W + c0 + j0) * 4;
  const q01 = ((r0 + i0) * W + c0 + j1) * 4;
  const q10 = ((r0 + i1) * W + c0 + j0) * 4;
  const q11 = ((r0 + i1) * W + c0 + j1) * 4;
  const w00 = (1 - fu) * (1 - fv);
  const w01 = (1 - fu) * fv;
  const w10 = fu * (1 - fv);
  const w11 = fu * fv;
  out[k] = d[q00]! * w00 + d[q01]! * w01 + d[q10]! * w10 + d[q11]! * w11;
  out[k + 1] = d[q00 + 1]! * w00 + d[q01 + 1]! * w01 + d[q10 + 1]! * w10 + d[q11 + 1]! * w11;
  out[k + 2] = d[q00 + 2]! * w00 + d[q01 + 2]! * w01 + d[q10 + 2]! * w10 + d[q11 + 2]! * w11;
}

/** Order used by a sample: a tile order, the Allsky image, or nothing at hand yet. */
export const FROM_ALLSKY = -1;
export const FROM_NOTHING = -2;

/**
 * Samples one survey at one tile order, asking for the tiles it lacks. `at` answers the colour from the deepest tile at hand
 * (the order asked for, a shallower one, the base tiles or the Allsky image) and returns the order it came from.
 * order = FROM_ALLSKY reads the Allsky image only (the whole-sky views of a survey without shallow tiles).
 */
export class Sampler {
  private L: Layer;
  private s: Survey;
  /** tiles of the order asked for that are not here yet (0: the picture is as sharp as it can be) */
  unresolved = 0;
  private lastPix = -1;
  private lastSlot: Slot | undefined = undefined;

  constructor(
    readonly id: string,
    readonly order: number,
  ) {
    this.L = layer(id);
    this.s = HIPS[id]!;
    stamp = performance.now();
  }

  /** shallowest tile order that can be asked for now */
  private get floor(): number {
    const b = baked.get(this.id);
    if (b) return b.min;
    return this.s.path && irsaUp() ? this.s.min : Math.max(3, this.s.min);
  }
  /** the last colour came from a baked tile (its tone is applied already) */
  lastBaked = false;

  at(f: FacePt, out: Float32Array | number[], k: number): number {
    const n = this.order;
    const floor = this.floor;
    if (n >= 0) {
      const pix = pixAt(n, f);
      let t: Slot | undefined;
      if (pix === this.lastPix) t = this.lastSlot;
      else {
        t = this.L.levels[n]!.get(pix);
        this.lastPix = pix;
        this.lastSlot = t;
        if (t === undefined) {
          this.unresolved++;
          if (n >= floor) want(this.id, n, pix, 2);
          // one shallower tile covers the view at an eighth of the resolution in one request
          const c = n - 3;
          if (c > floor && this.L.levels[c]!.get(pixAt(c, f)) === undefined) want(this.id, c, pixAt(c, f), 3);
        }
      }
      if (t) return this.read(t, n, f, out, k);
      for (let m = n - 1; m >= (baked.get(this.id)?.min ?? this.s.min); m--) {
        const u = this.L.levels[m]!.get(pixAt(m, f));
        if (u) return this.read(u, m, f, out, k);
      }
      // the base: the shallowest tiles from IRSA (a dozen small tiles cover the sky)
      if (floor < 3 && n > floor) {
        const bp = pixAt(floor, f);
        if (this.L.levels[floor]!.get(bp) === undefined) want(this.id, floor, bp, baked.has(this.id) ? 5 : 1);
      }
    }
    // and under everything the Allsky image, asked for first: one request gives a picture of the whole sky
    return this.allsky(f, out, k, true);
  }

  private read(t: Tile, n: number, f: FacePt, out: Float32Array | number[], k: number): number {
    t.used = stamp;
    this.lastBaked = !!t.baked;
    const N = 1 << n;
    const X = f.x * N * t.w;
    const Y = f.y * N * t.w;
    bilinear(t, X - Math.floor(f.x * N) * t.w, Y - Math.floor(f.y * N) * t.w, t.w, 0, 0, out, k);
    return n;
  }

  private allsky(f: FacePt, out: Float32Array | number[], k: number, ask: boolean): number {
    this.lastBaked = false;
    // a baked layer has no Allsky image to fall back on: the live one has another tone (dust: another palette), so a place whose baked tile is not here yet waits for it
    if (this.s.bakedOnly || baked.has(this.id)) return FROM_NOTHING;
    const a = this.L.allsky;
    if (a === undefined) {
      if (ask) want(this.id, ALLSKY, 0, 4);
      return FROM_NOTHING;
    }
    if (!a) return FROM_NOTHING;
    const ts = Math.round(a.w / 27);
    const tile = pixAt(3, f);
    const X = f.x * 8 * ts - Math.floor(f.x * 8) * ts;
    const Y = f.y * 8 * ts - Math.floor(f.y * 8) * ts;
    bilinear(a, X, Y, ts, Math.floor(tile / 27) * ts, (tile % 27) * ts, out, k);
    return FROM_ALLSKY;
  }
}

/** The tile order that matches a globe of radius R pixels (a tile pixel about one screen pixel); FROM_ALLSKY when the Allsky image is enough. */
export function orderFor(id: string, R: number, irsaShallow = true): number {
  const s = HIPS[id]!;
  const n = Math.log2((1.023 * R) / s.w) + 0.2;
  const b = baked.get(id);
  // baked tiles start at their own shallowest order (the whole sky is a few tiles): the Allsky image is not needed
  // texels no smaller than a screen pixel (a little less is allowed), so that stars do not shimmer as the view moves
  if (b) return Math.min(s.max, Math.max(b.min, Math.floor(n + 0.2)));
  const floor = s.path && irsaShallow ? s.min : Math.max(3, s.min);
  if (floor >= 3) return n < 2.3 ? FROM_ALLSKY : Math.min(s.max, Math.max(3, Math.round(n)));
  return Math.min(s.max, Math.max(floor, Math.round(n)));
}

/** Tile pixels of all surveys, shared by the globe and the lab. */
export const tiles = {
  /** a new view of the globe: requests of views two frames old or more are dropped from the queue */
  frame(): void {
    gen++;
    stamp = performance.now();
  },
  /** Colour of a survey at a position, from the deepest tile at hand up to `order`; null until something has arrived. */
  sample(id: string, ra: number, dec: number, order: number): [number, number, number] | null {
    const out = [0, 0, 0];
    const r = new Sampler(id, order).at(facePt(ra, Math.sin((dec * Math.PI) / 180)), out, 0);
    return r === FROM_NOTHING ? null : [out[0]!, out[1]!, out[2]!];
  },
  /** called whenever a tile arrives (or fails); returns the function that stops it */
  onTile(f: () => void): () => void {
    listeners.add(f);
    return () => listeners.delete(f);
  },
  /** asks for the whole-sky (Allsky) image of surveys now, so that the globe has a picture the moment it opens */
  preload(ids: string[]): void {
    for (const id of ids) if (HIPS[id]) want(id, ALLSKY, 0, 4);
  },
  /** tiles on their way or waiting */
  busy: () => inflight.size + queue.size,
};

/**
 * A cutout of a survey around a place: north up, east left, `size` pixels for `fovDeg` degrees (gnomonic). Returns the RGBA
 * pixels and whether every pixel came from a tile of the order the scale needs (false while those tiles are on their way).
 * Pixels whose tiles are far coarser than the scale (the Allsky image under a 5′ field) stay black instead of showing
 * HEALPix cells.
 */
export function cutout(id: string, maxOrder: number, ra0: number, dec0: number, fovDeg: number, size: number): { data: ImageData; complete: boolean } {
  const D = Math.PI / 180;
  const scale = (fovDeg * 3600) / size; // ″ per output pixel
  const order = Math.max(3, Math.min(maxOrder, HIPS[id]?.max ?? maxOrder, Math.ceil(Math.log2(412 / scale))));
  const smp = new Sampler(id, order);
  const img = new ImageData(size, size);
  const vals = new Float32Array(size * size * 3);
  const f: FacePt = { face: 0, x: 0, y: 0 };
  const sd = Math.sin(dec0 * D);
  const cd = Math.cos(dec0 * D);
  for (let j = 0; j < size; j++)
    for (let i = 0; i < size; i++) {
      // tangent-plane offsets: x to the east (left on the picture), y to the north (up)
      const xi = -((i + 0.5) / size - 0.5) * fovDeg * D;
      const eta = (0.5 - (j + 0.5) / size) * fovDeg * D;
      const den = cd - eta * sd;
      const ra = ra0 + Math.atan2(xi, den) / D;
      const z = (sd + eta * cd) / Math.hypot(xi, sd + eta * cd, den);
      const q = (j * size + i) * 3;
      const from = smp.at(facePt(ra, z, f), vals, q);
      // a tile up to four times coarser than the scale is a fair stand-in while the right one arrives
      if (from < order - 2) vals[q] = vals[q + 1] = vals[q + 2] = NaN;
    }
  // stretch: the 2nd percentile to black, the 99.7th to white
  const lum: number[] = [];
  for (let k = 0; k < vals.length; k += 9) if (Number.isFinite(vals[k]!)) lum.push(vals[k]!);
  lum.sort((a, b) => a - b);
  const lo = lum.length ? lum[Math.floor(0.02 * (lum.length - 1))]! : 0;
  const hi = lum.length ? Math.max(lo + 1, lum[Math.floor(0.997 * (lum.length - 1))]!) : 255;
  for (let k = 0, p = 0; k < vals.length; k += 3, p += 4) {
    img.data[p + 3] = 255;
    if (!Number.isFinite(vals[k]!)) continue;
    for (let c = 0; c < 3; c++) img.data[p + c] = Math.max(0, Math.min(255, ((vals[k + c]! - lo) / (hi - lo)) * 255));
  }
  return { data: img, complete: smp.unresolved === 0 };
}
