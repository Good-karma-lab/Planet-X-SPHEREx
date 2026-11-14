// The survey as a glow for the wide view: the picture the sky painter made (a real HiPS survey) is reduced to its luminance, blurred, stretched between its own
// 30th and 99.97th percentile, given a halo and coloured by a night ramp driven by that luminance. Real data, toned: nothing is added that the survey does not have.
// Up close (below 12° across) the raw survey is shown instead (the caller crossfades).

/** Box blur, three passes ≈ a Gaussian of the given sigma (pixels). */
function blur(a: Float32Array, w: number, h: number, sigma: number): Float32Array {
  const r = Math.max(1, Math.round(sigma * 1.7));
  let src = a;
  let dst = new Float32Array(a.length);
  const tmp = new Float32Array(a.length);
  for (let pass = 0; pass < 3; pass++) {
    // horizontal
    for (let y = 0; y < h; y++) {
      let s = 0;
      const o = y * w;
      for (let x = -r; x <= r; x++) s += src[o + Math.min(w - 1, Math.max(0, x))]!;
      for (let x = 0; x < w; x++) {
        tmp[o + x] = s / (2 * r + 1);
        s += src[o + Math.min(w - 1, x + r + 1)]! - src[o + Math.max(0, x - r)]!;
      }
    }
    // vertical
    for (let x = 0; x < w; x++) {
      let s = 0;
      for (let y = -r; y <= r; y++) s += tmp[Math.min(h - 1, Math.max(0, y)) * w + x]!;
      for (let y = 0; y < h; y++) {
        dst[y * w + x] = s / (2 * r + 1);
        s += tmp[Math.min(h - 1, y + r + 1) * w + x]! - tmp[Math.max(0, y - r) * w + x]!;
      }
    }
    src = dst;
    dst = pass === 2 ? dst : new Float32Array(a.length);
  }
  return src;
}

const ASINH_K = Math.asinh(1 / 0.08);

/** The night ramp as a lookup: stretched luminance (0..1, 1024 steps) to RGB. */
const RAMP = (() => {
  const t = new Uint8ClampedArray(1024 * 3);
  for (let i = 0; i < 1024; i++) {
    const v = i / 1023;
    const w = Math.min(1, 1.25 * v);
    t[i * 3] = (92 + 86 * w) * v;
    t[i * 3 + 1] = (128 + 58 * w) * v;
    t[i * 3 + 2] = (186 + 14 * w) * v;
  }
  return t;
})();
/** The asinh stretch of a luminance already scaled to 0..1: asinh(x / 0.08) / asinh(1 / 0.08) · 0.92. */
const stretch = (x: number) => (Math.asinh(x / 0.08) / ASINH_K) * 0.92;

export interface Bounds {
  lo: number;
  hi: number;
}

const luminance = (d: Uint8ClampedArray, i: number) => (0.3 * d[i * 4]! + 0.55 * d[i * 4 + 1]! + 0.15 * d[i * 4 + 2]!) / 255;

const bounds = new Map<string, Bounds>();
let boundsFor: { gen: number; b: Bounds } | null = null;

/**
 * One black and one white point for a survey, so that no tile or picture differs in brightness from another: they are taken from the whole-sky view (the 35th and
 * 99.95th percentile of its luminance; the 45th for DSS2 plates) and kept; a close view uses them. Without a whole-sky view yet, the picture's own.
 */
export function toneBounds(src: HTMLCanvasElement, gen: number, survey: string, wide: boolean): Bounds {
  if (boundsFor && boundsFor.gen === gen) return boundsFor.b;
  let b = wide ? undefined : bounds.get(survey);
  if (!b) {
    const w = src.width;
    const h = src.height;
    const d = src.getContext('2d', { willReadFrequently: true })!.getImageData(0, 0, w, h).data;
    const sample: number[] = [];
    for (let i = 0; i < w * h; i += 5) if (d[i * 4 + 3]! >= 8) sample.push(luminance(d, i));
    if (sample.length < 100) return { lo: 0, hi: 1 };
    const sorted = Float32Array.from(sample).sort();
    const at = (p: number) => sorted[Math.min(sorted.length - 1, Math.floor(p * (sorted.length - 1)))]!;
    b = { lo: at(survey === 'dss2' ? 0.45 : 0.35), hi: Math.max(at(0.9995), at(survey === 'dss2' ? 0.45 : 0.35) + 1e-4) };
    if (wide) bounds.set(survey, b);
  }
  boundsFor = { gen, b };
  return b;
}

let toneCache: { gen: number; canvas: HTMLCanvasElement } | null = null;

/**
 * The survey picture toned: luminance, the asinh stretch between the survey's bounds, the night ramp, at full resolution. The stars stay the survey's own pixels (a light
 * blur of 0.7 px at most is the picture's own smoothing); nothing is drawn that the survey does not hold.
 */
export function toneOf(src: HTMLCanvasElement, gen: number, b: Bounds): HTMLCanvasElement {
  if (toneCache && toneCache.gen === gen) return toneCache.canvas;
  const w = src.width;
  const h = src.height;
  const out = document.createElement('canvas');
  out.width = w;
  out.height = h;
  const d = src.getContext('2d', { willReadFrequently: true })!.getImageData(0, 0, w, h).data;
  const o = out.getContext('2d')!;
  const img = o.createImageData(w, h);
  const q = img.data;
  const k = 1 / (b.hi - b.lo);
  for (let i = 0; i < w * h; i++) {
    if (d[i * 4 + 3]! < 8) continue;
    const v = stretch(Math.max(0, Math.min(1, (luminance(d, i) - b.lo) * k)));
    const idx = Math.min(1023, Math.round(v * 1023)) * 3;
    q[i * 4] = RAMP[idx]!;
    q[i * 4 + 1] = RAMP[idx + 1]!;
    q[i * 4 + 2] = RAMP[idx + 2]!;
    q[i * 4 + 3] = 255;
  }
  o.putImageData(img, 0, 0);
  toneCache = { gen, canvas: out };
  return out;
}

let haloCache: { gen: number; canvas: HTMLCanvasElement } | null = null;

/** The light halo of the band: the stretched luminance blurred over 42 px (at 1440 wide), at a quarter of the resolution; the caller adds it at 0.35. */
export function haloOf(src: HTMLCanvasElement, gen: number, b: Bounds): HTMLCanvasElement {
  if (haloCache && haloCache.gen === gen) return haloCache.canvas;
  const sw = Math.max(32, Math.ceil(src.width / 4));
  const sh = Math.max(32, Math.ceil(src.height / 4));
  const small = document.createElement('canvas');
  small.width = sw;
  small.height = sh;
  const sx = small.getContext('2d', { willReadFrequently: true })!;
  sx.imageSmoothingQuality = 'high';
  sx.drawImage(src, 0, 0, sw, sh);
  const d = sx.getImageData(0, 0, sw, sh).data;
  const n = sw * sh;
  const v = new Float32Array(n);
  const k = 1 / (b.hi - b.lo);
  for (let i = 0; i < n; i++) if (d[i * 4 + 3]! >= 8) v[i] = stretch(Math.max(0, Math.min(1, (luminance(d, i) - b.lo) * k)));
  const halo = blur(v, sw, sh, Math.max(2, (42 * src.width) / 1440 / 4));
  const out = document.createElement('canvas');
  out.width = sw;
  out.height = sh;
  const o = out.getContext('2d')!;
  const img = o.createImageData(sw, sh);
  for (let i = 0; i < n; i++) {
    if (d[i * 4 + 3]! < 8) continue;
    const idx = Math.min(1023, Math.round(Math.min(0.9, halo[i]!) * 1023)) * 3;
    img.data[i * 4] = RAMP[idx]!;
    img.data[i * 4 + 1] = RAMP[idx + 1]!;
    img.data[i * 4 + 2] = RAMP[idx + 2]!;
    img.data[i * 4 + 3] = 255;
  }
  o.putImageData(img, 0, 0);
  haloCache = { gen, canvas: out };
  return out;
}

/** The deep navy ground: rgb(9,20,36) at (55 %, 45 %) darkening to rgb(3,8,15) at the edges. */
export function paintGround(ctx: CanvasRenderingContext2D, W: number, H: number): void {
  const g = ctx.createRadialGradient(W * 0.55, H * 0.45, 0, W * 0.55, H * 0.45, Math.hypot(W, H) * 0.62);
  g.addColorStop(0, 'rgb(9,20,36)');
  g.addColorStop(1, 'rgb(3,8,15)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, W, H);
}

/** Screen field of view in degrees across a canvas W wide of the camera inside the sphere (stereographic, R pixels per radian at the centre). */
export const fovOfGlobe = (W: number, R: number) => Math.min(180, (4 * Math.atan(W / (4 * Math.max(1, R))) * 180) / Math.PI);
