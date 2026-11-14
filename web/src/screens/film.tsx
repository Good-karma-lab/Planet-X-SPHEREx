// The Days scale of the lab: the visits of one pass (the film), read from the service, and the known bodies JPL predicts in it.
// The lab draws the visits with its own renderer (the same colour law, Sharpen, Brighten and Stretch as the Months picture).

import { mjdToDate } from '../lib/field';
import { parseSxp } from '../lib/sxp';
import { HI, LO, SOFT } from '../viewer/law';
import { plainPhase } from '../lib/plain';

export interface FrameMeta {
  visit: number;
  det: number;
  mjd: number;
  exposures: number;
}

export interface FilmMeta {
  grid: { ra0: number; dec0: number; n: number; step_arcsec: number };
  pass: { mjd0: number; mjd1: number };
  frames: FrameMeta[];
  exposures: number;
}

export interface Film {
  key: string;
  meta: FilmMeta;
  /** per visit: physical values of the visit's sky (NaN = no data), n×n, row 0 north */
  sky: Float32Array[];
  /** per visit: motion significance encoding (0 = masked or no data, else 1 + 254 × clip((z + 5) / 20)) */
  motion: Uint8Array;
  /** background and noise of the visits (for Sharpen) */
  background: number;
  sigma: number;
}

export type FilmLoad = { state: 'none'; note: string } | { state: 'ok'; film: Film };

export interface KnownBody {
  name: string;
  v: number | null;
  frames: { k: number; x: number; y: number; inside: boolean }[];
  expected: number[];
  /** SPHEREx detector images of the pass (all detectors) that hold the body */
  planes?: number;
  /** the expected frames on which the film's motion map shows something at the predicted place (z ≥ 4 within 2 grid pixels) */
  seen: number[];
  /** the motion significance stacked along JPL's track over the expected visits: Σ z / √N at the predicted pixel, and the same
   *  at four places 20 grid pixels aside (what chance gives) */
  stackZ?: number;
  stackN?: number;
  controlZ?: number;
}

export const FRAME_MS = 700;
export const zOf = (v: number) => ((v - 1) / 254) * 20 - 5;
const SEEN_Z = 4.0;
const GAIN = Math.asinh((HI - LO) / SOFT);

export function utc(mjd: number): string {
  const d = mjdToDate(mjd);
  return d.toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }) + ' UTC';
}

const sleep = (ms: number) => new Promise((res) => setTimeout(res, ms));

/** GET with retries: a restarting service (503), a part not yet written (404) or a dropped connection is asked again. */
async function fetchRetry(url: string, stop: () => boolean, tries = 12): Promise<Response> {
  let wait = 1000;
  for (let i = 0; ; i++) {
    try {
      const r = await fetch(url, { cache: 'no-store' });
      if (r.ok || i >= tries || stop() || (r.status !== 503 && r.status !== 502 && r.status !== 404)) return r;
    } catch (e) {
      if (i >= tries || stop()) throw e;
    }
    await sleep(wait);
    wait = Math.min(8000, wait * 1.5);
  }
}

/** The film of the pass that holds `mjd` (the service builds it on first request); `onPhase` reports the job's progress. */
export async function loadFilm(
  ra: number,
  dec: number,
  fov: number,
  mjd: number | undefined,
  onPhase: (s: string) => void,
  stop: () => boolean,
): Promise<FilmLoad | null> {
  onPhase('asking for the visits of the pass…');
  const r = await fetchRetry(`/api/v1/film?ra=${ra}&dec=${dec}&fov=${fov}${mjd ? `&mjd=${mjd}` : ''}`, stop);
  if (!r.ok) throw new Error(`the sky service answered ${r.status}`);
  const body = (await r.json()) as { key?: string; state?: string; note?: string; job?: { id: string } };
  if (body.state === 'none') return { state: 'none', note: body.note ?? 'this pass has too few visits for a film' };
  let state = body.state;
  while (!stop() && state !== 'done') {
    const jr = await fetchRetry(`/api/v1/jobs/${body.job!.id}`, stop);
    if (jr.status === 404) {
      // the service restarted and forgot the job: ask for the film again (it attaches to the product or starts anew)
      return loadFilm(ra, dec, fov, mjd, onPhase, stop);
    }
    const j = (await jr.json()) as { state: string; phase: string; error: string | null; done: number; total: number };
    if (j.state === 'error' || j.state === 'cancelled') throw new Error(j.error ?? j.state);
    onPhase(`building the visits: ${plainPhase(j.phase)}${j.total ? ` (${j.done} of ${j.total})` : ''}`);
    state = j.state;
    if (state !== 'done') await sleep(1000);
  }
  if (stop()) return null;
  const pr = await fetchRetry(`/api/v1/film/${body.key}/parts/film`, stop);
  if (!pr.ok) throw new Error(`the film part answered ${pr.status}`);
  const p = parseSxp(await pr.arrayBuffer());
  const meta = p.meta as unknown as FilmMeta;
  const n = meta.grid.n;
  const raw = p.arrays['sky']!.data as Uint8Array;
  const sky: Float32Array[] = [];
  // the sky was stored through the lab's display law: back to physical values, so the renderer applies the law (and Brighten,
  // Stretch and Sharpen) exactly as for the Months picture
  const lut = new Float32Array(256);
  lut[0] = NaN;
  for (let v = 1; v < 256; v++) lut[v] = LO + SOFT * Math.sinh(((v - 1) / 254) * GAIN);
  const sample: number[] = [];
  for (let k = 0; k < meta.frames.length; k++) {
    const a = new Float32Array(n * n);
    const off = k * n * n;
    for (let i = 0; i < n * n; i++) a[i] = lut[raw[off + i]!]!;
    fillCores(a, n, HI);
    sky.push(a);
    for (let i = 0; i < n * n; i += 97) if (raw[off + i]) sample.push(a[i]!);
  }
  sample.sort((x, y) => x - y);
  const med = sample.length ? sample[sample.length >> 1]! : 0;
  const dev = sample.map((x) => Math.abs(x - med)).sort((x, y) => x - y);
  const sigma = dev.length ? 1.4826 * dev[dev.length >> 1]! : 1;
  return { state: 'ok', film: { key: body.key!, meta, sky, motion: p.arrays['motion']!.data as Uint8Array, background: med, sigma } };
}

/** The known bodies JPL predicts in the film, and on which of their expected visits the film's motion map shows them. */
export async function loadBodies(film: Film, stop: () => boolean): Promise<{ state: string; bodies: KnownBody[]; vLimit: number } | null> {
  let pause = 2000;
  const n = film.meta.grid.n;
  const mot = film.motion;
  for (let i = 0; i < 60 && !stop(); i++) {
    const r = await fetchRetry(`/api/v1/film/${film.key}/bodies`, stop);
    if (r.ok) {
      const b = (await r.json()) as { state: string; bodies: Omit<KnownBody, 'seen'>[]; v_limit?: number };
      if (b.state === 'done' || b.state === 'unavailable') {
        const zAt = (k: number, x: number, y: number) => {
          const xi = Math.round(x);
          const yi = Math.round(y);
          if (xi < 0 || yi < 0 || xi >= n || yi >= n) return null;
          const v = mot[k * n * n + yi * n + xi]!;
          return v ? zOf(v) : null;
        };
        const list: KnownBody[] = b.bodies.map((kb) => {
          const seen = kb.expected.filter((k) => {
            const f = kb.frames[k]!;
            let best = 0;
            for (let dy = -2; dy <= 2; dy++)
              for (let dx = -2; dx <= 2; dx++) {
                const x = Math.round(f.x) + dx;
                const y = Math.round(f.y) + dy;
                if (x < 0 || y < 0 || x >= n || y >= n) continue;
                best = Math.max(best, mot[k * n * n + y * n + x]!);
              }
            return best > 0 && zOf(best) >= SEEN_Z;
          });
          // the track stacked: z at the predicted pixel of every expected visit over √N; the same 20 pixels aside is chance
          const stack = (dx: number, dy: number) => {
            let sum = 0;
            let cnt = 0;
            for (const k of kb.expected) {
              const z = zAt(k, kb.frames[k]!.x + dx, kb.frames[k]!.y + dy);
              if (z !== null) {
                sum += z;
                cnt++;
              }
            }
            return { z: cnt ? sum / Math.sqrt(cnt) : 0, n: cnt };
          };
          const on = stack(0, 0);
          const off = [stack(20, 0), stack(-20, 0), stack(0, 20), stack(0, -20)];
          const controlZ = Math.sqrt(off.reduce((a, o) => a + o.z * o.z, 0) / off.length);
          return { ...kb, seen, stackZ: on.z, stackN: on.n, controlZ };
        });
        return { state: b.state, bodies: list, vLimit: b.v_limit ?? 20.5 };
      }
    }
    await sleep(pause);
    pause = Math.min(10000, pause * 1.4);
  }
  return null;
}

/** A hole enclosed by data within a few pixels on all four sides: a saturated core (bright all around) is drawn white, never
 *  as a hole; any other masked spot (a cosmic ray, a flagged pixel of this visit) is filled from its neighbours. Edges of the
 *  visit's coverage stay without data. */
function fillCores(a: Float32Array, n: number, white: number, reach = 10): void {
  const N = n * n;
  const dist = [new Int16Array(N), new Int16Array(N), new Int16Array(N), new Int16Array(N)];
  const val = [new Float32Array(N), new Float32Array(N), new Float32Array(N), new Float32Array(N)];
  const far = 32767;
  const scan = (k: number, idx: (o: number, t: number) => number, rev: boolean) => {
    for (let o = 0; o < n; o++) {
      let d = far;
      let v = NaN;
      for (let s = 0; s < n; s++) {
        const i = idx(o, rev ? n - 1 - s : s);
        const x = a[i]!;
        if (Number.isNaN(x)) d = Math.min(far, d + 1);
        else {
          d = 0;
          v = x;
        }
        dist[k]![i] = d;
        val[k]![i] = v;
      }
    }
  };
  scan(0, (y, x) => y * n + x, false);
  scan(1, (y, x) => y * n + x, true);
  scan(2, (x, y) => y * n + x, false);
  scan(3, (x, y) => y * n + x, true);
  const sample: number[] = [];
  for (let i = 0; i < N; i += 13) if (!Number.isNaN(a[i]!)) sample.push(a[i]!);
  sample.sort((x, y) => x - y);
  const bright = sample.length ? sample[Math.floor(sample.length * 0.995)]! : white;
  for (let i = 0; i < N; i++) {
    if (!Number.isNaN(a[i]!)) continue;
    if (dist.some((d) => d[i]! > reach)) continue;
    const m = (val[0]![i]! + val[1]![i]! + val[2]![i]! + val[3]![i]!) / 4;
    const lo = Math.min(val[0]![i]!, val[1]![i]!, val[2]![i]!, val[3]![i]!);
    a[i] = lo >= bright ? white : m;
  }
}
