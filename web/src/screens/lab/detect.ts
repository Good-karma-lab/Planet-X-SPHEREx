// The sources a pass picture shows: local peaks above the picture's own noise. Used by Known objects to ring only catalogue objects
// the picture can show, and to point at what the picture shows and no catalogue has.
import { halfToFloat } from '../../lib/sxp';

export interface Peak {
  /** grid pixel of the peak's centre (x east to the left as in the picture, y down) */
  x: number;
  y: number;
  /** its height above the background, in sigmas */
  snr: number;
}

/** a peak this many sigmas above the background is a source the eye sees on the picture */
export const DETECT_SIGMA = 5;
/** a source with no catalogue match is pointed at only above this (a faint bump next to noise is not a find) */
export const NOMATCH_SIGMA = 8;
/** peaks closer than this to the picture's edge are not counted (grid pixels) */
const EDGE = 3;
const MAX_PEAKS = 6000;

/** Local maxima (over a 5×5 neighbourhood) of the mean of the three colour planes, at least `DETECT_SIGMA` above the background. */
export function findPeaks(rgb: Uint16Array, n: number, bg: number, sigma: number): Peak[] {
  const N = n * n;
  if (rgb.length < 3 * N || !(sigma > 0)) return [];
  const v = new Float32Array(N);
  for (let i = 0; i < N; i++) v[i] = (halfToFloat(rgb[i]!) + halfToFloat(rgb[N + i]!) + halfToFloat(rgb[2 * N + i]!)) / 3;
  return peaksOf(v, n, bg, sigma);
}

export function peaksOf(v: Float32Array, n: number, bg: number, sigma: number): Peak[] {
  const out: Peak[] = [];
  const cut = bg + DETECT_SIGMA * sigma;
  for (let y = EDGE; y < n - EDGE; y++)
    for (let x = EDGE; x < n - EDGE; x++) {
      const c = v[y * n + x]!;
      if (!(c >= cut)) continue;
      let top = true;
      for (let dy = -2; dy <= 2 && top; dy++)
        for (let dx = -2; dx <= 2; dx++) {
          if (!dx && !dy) continue;
          const o = v[(y + dy) * n + x + dx]!;
          // a tie goes to the first pixel in reading order, so a flat (saturated) top is one peak
          if (o > c || (o === c && (dy < 0 || (dy === 0 && dx < 0)))) {
            top = false;
            break;
          }
        }
      if (top) out.push({ x: x + 0.5, y: y + 0.5, snr: (c - bg) / sigma });
    }
  if (out.length > MAX_PEAKS) out.sort((a, b) => b.snr - a.snr).splice(MAX_PEAKS);
  return out;
}

/** A grid of buckets over points, to ask "is anything within r of here" quickly. */
export class Buckets<T extends { x: number; y: number }> {
  private map = new Map<number, T[]>();
  constructor(
    items: T[],
    private cell: number,
  ) {
    for (const p of items) {
      const k = this.key(Math.floor(p.x / cell), Math.floor(p.y / cell));
      const b = this.map.get(k);
      if (b) b.push(p);
      else this.map.set(k, [p]);
    }
  }
  private key(i: number, j: number): number {
    return (i + 4096) * 16384 + j + 4096;
  }
  /** the nearest item within r, or null */
  near(x: number, y: number, r: number): T | null {
    const span = Math.ceil(r / this.cell);
    const ci = Math.floor(x / this.cell);
    const cj = Math.floor(y / this.cell);
    let best: T | null = null;
    let bd = r;
    for (let i = ci - span; i <= ci + span; i++)
      for (let j = cj - span; j <= cj + span; j++)
        for (const p of this.map.get(this.key(i, j)) ?? []) {
          const d = Math.hypot(p.x - x, p.y - y);
          if (d <= bd) {
            bd = d;
            best = p;
          }
        }
    return best;
  }
}
