// White balance of one pass on its field stars: at the peaks of the brightest unsaturated stars the three colour channels are made
// equal, so that an ordinary star of the field is white. Peaks rather than every bright pixel: the wings of bright stars and the
// noise around faint ones carry the colour of the detectors, not of the stars.

import { halfToFloat } from '../lib/sxp';

const MAX_STARS = 30;
const MIN_STARS = 6;

function median(a: number[]): number {
  const s = a.slice().sort((x, y) => x - y);
  const m = s.length >> 1;
  return s.length % 2 ? s[m]! : (s[m - 1]! + s[m]!) / 2;
}

/** (kR, kB) for the picture of one pass (`rgb`: float16 bits, three n×n planes R, G, B), or null when the field has too few stars. */
export function starBalance(rgb: Uint16Array, n: number, sat?: Uint8Array): { kr: number; kb: number; stars: number } | null {
  const N = n * n;
  const R = new Float32Array(N);
  const G = new Float32Array(N);
  const B = new Float32Array(N);
  const sample: number[] = [];
  for (let i = 0; i < N; i++) {
    R[i] = halfToFloat(rgb[i]!);
    G[i] = halfToFloat(rgb[N + i]!);
    B[i] = halfToFloat(rgb[2 * N + i]!);
    if (i % 7 === 0 && Number.isFinite(G[i]!)) sample.push(G[i]!);
  }
  if (sample.length < 100) return null;
  const bg = { r: 0, g: median(sample), b: 0 };
  const sr: number[] = [];
  const sb: number[] = [];
  for (let i = 0; i < N; i += 7) {
    if (Number.isFinite(R[i]!)) sr.push(R[i]!);
    if (Number.isFinite(B[i]!)) sb.push(B[i]!);
  }
  bg.r = median(sr);
  bg.b = median(sb);
  const noise = 1.4826 * median(sample.map((v) => Math.abs(v - bg.g)));
  // a pixel near a saturated core or a hole is no peak of an ordinary star
  const bad = new Uint8Array(N);
  for (let y = 0; y < n; y++)
    for (let x = 0; x < n; x++) {
      const i = y * n + x;
      if ((sat && sat[i]) || !Number.isFinite(R[i]!) || !Number.isFinite(G[i]!) || !Number.isFinite(B[i]!)) {
        for (let dy = -4; dy <= 4; dy++)
          for (let dx = -4; dx <= 4; dx++) {
            const yy = y + dy;
            const xx = x + dx;
            if (yy >= 0 && yy < n && xx >= 0 && xx < n) bad[yy * n + xx] = 1;
          }
      }
    }
  const peaks: { i: number; v: number }[] = [];
  for (let y = 3; y < n - 3; y++)
    for (let x = 3; x < n - 3; x++) {
      const i = y * n + x;
      const v = G[i]!;
      if (bad[i] || !(v - bg.g > 10 * noise)) continue;
      let top = true;
      for (let dy = -3; dy <= 3 && top; dy++)
        for (let dx = -3; dx <= 3; dx++) {
          if ((dx || dy) && G[i + dy * n + dx]! > v) {
            top = false;
            break;
          }
        }
      if (top) peaks.push({ i, v });
    }
  peaks.sort((a, b) => b.v - a.v);
  const use = peaks.slice(0, MAX_STARS);
  const rg: number[] = [];
  const bgr: number[] = [];
  for (const { i } of use) {
    const r = R[i]! - bg.r;
    const g = G[i]! - bg.g;
    const b = B[i]! - bg.b;
    if (r > 0 && g > 0 && b > 0) {
      rg.push(g / r);
      bgr.push(g / b);
    }
  }
  if (rg.length < MIN_STARS) return null;
  return { kr: median(rg), kb: median(bgr), stars: rg.length };
}

/** Background (median) and noise (1.4826 × MAD) of the white light of one pass: the mean of its `planes` n×n float16 planes.
 *  Also the white-light levels at the 99.5th and 99.9th percentile (the trims of the bright end). */
export function passLevels(data: Uint16Array, n: number, planes: number): { bg: number; sigma: number; p995: number; p999: number } | null {
  const N = n * n;
  const v: number[] = [];
  for (let i = 0; i < N; i += 3) {
    let s = 0;
    let k = 0;
    for (let c = 0; c < planes; c++) {
      const x = halfToFloat(data[c * N + i]!);
      if (Number.isFinite(x)) {
        s += x;
        k++;
      }
    }
    if (k) v.push(s / k);
  }
  if (v.length < 100) return null;
  v.sort((a, b) => a - b);
  const bg = v[v.length >> 1]!;
  const dev = v.map((x) => Math.abs(x - bg)).sort((a, b) => a - b);
  return { bg, sigma: 1.4826 * dev[dev.length >> 1]!, p995: v[Math.floor(0.995 * (v.length - 1))]!, p999: v[Math.floor(0.999 * (v.length - 1))]! };
}
