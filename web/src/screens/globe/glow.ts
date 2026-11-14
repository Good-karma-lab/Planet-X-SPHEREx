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

let cache: { gen: number; canvas: HTMLCanvasElement } | null = null;

/**
 * The toned glow of a sky picture at a quarter of its resolution (the caller scales it up smoothly). `gen` names the picture: the same picture is toned once.
 * Pixels the picture does not cover stay transparent.
 */
export function glowOf(src: HTMLCanvasElement, gen: number, blackPct = 0.3): HTMLCanvasElement {
  if (cache && cache.gen === gen) return cache.canvas;
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
  let L = new Float32Array(n);
  const used: number[] = [];
  for (let i = 0; i < n; i++) {
    if (d[i * 4 + 3]! < 8) continue;
    L[i] = (0.3 * d[i * 4]! + 0.55 * d[i * 4 + 1]! + 0.15 * d[i * 4 + 2]!) / 255;
    used.push(L[i]!);
  }
  const out = document.createElement('canvas');
  out.width = sw;
  out.height = sh;
  if (used.length < 100) return (cache = { gen, canvas: out }).canvas;
  const scale = src.width / 1440 / 4;
  L = blur(L, sw, sh, Math.max(0.6, 4.5 * scale));
  const sorted = Float32Array.from(used.map((_, k) => used[k]!)).sort();
  const at = (p: number) => sorted[Math.min(sorted.length - 1, Math.floor(p * (sorted.length - 1)))]!;
  const lo = at(blackPct);
  const hi = Math.max(lo + 1e-4, at(0.9997));
  const v = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const x = Math.max(0, Math.min(1, (L[i]! - lo) / (hi - lo)));
    v[i] = (Math.asinh(x / 0.08) / ASINH_K) * 0.92;
  }
  const halo = blur(v, sw, sh, Math.max(2, 42 * scale));
  const o = out.getContext('2d')!;
  const img = o.createImageData(sw, sh);
  for (let i = 0; i < n; i++) {
    if (d[i * 4 + 3]! < 8) continue;
    const vv = Math.max(0, Math.min(0.9, 0.74 * v[i]! + 0.5 * halo[i]!));
    const w = Math.min(1, 1.25 * vv);
    img.data[i * 4] = (92 + 86 * w) * vv;
    img.data[i * 4 + 1] = (128 + 58 * w) * vv;
    img.data[i * 4 + 2] = (186 + 14 * w) * vv;
    img.data[i * 4 + 3] = 255;
  }
  o.putImageData(img, 0, 0);
  cache = { gen, canvas: out };
  return out;
}

let toneCache: { gen: number; canvas: HTMLCanvasElement } | null = null;
/** The night ramp as a lookup: luminance after the stretch (0..1, 1024 steps) to RGB. */
const RAMP = (() => {
  const t = new Uint8ClampedArray(1024 * 3);
  for (let i = 0; i < 1024; i++) {
    const x = i / 1023;
    const v = (Math.asinh(x / 0.08) / ASINH_K) * 0.92;
    const w = Math.min(1, 1.25 * v);
    t[i * 3] = (92 + 86 * w) * v;
    t[i * 3 + 1] = (128 + 58 * w) * v;
    t[i * 3 + 2] = (186 + 14 * w) * v;
  }
  return t;
})();

/**
 * The same night palette at full resolution and without the blur or the halo: for the close view, where the detail of the survey is the content. Every survey picture
 * goes through the same stretch (its own 30th and 99.97th percentile) and the same ramp, so no texture looks different from its neighbour.
 */
export function toneOf(src: HTMLCanvasElement, gen: number, blackPct = 0.3): HTMLCanvasElement {
  if (toneCache && toneCache.gen === gen) return toneCache.canvas;
  const w = src.width;
  const h = src.height;
  const out = document.createElement('canvas');
  out.width = w;
  out.height = h;
  const d = src.getContext('2d', { willReadFrequently: true })!.getImageData(0, 0, w, h).data;
  const n = w * h;
  const L = new Float32Array(n);
  const sample: number[] = [];
  for (let i = 0; i < n; i++) {
    if (d[i * 4 + 3]! < 8) continue;
    const l = (0.3 * d[i * 4]! + 0.55 * d[i * 4 + 1]! + 0.15 * d[i * 4 + 2]!) / 255;
    L[i] = l;
    if (i % 5 === 0) sample.push(l);
  }
  if (sample.length < 100) return (toneCache = { gen, canvas: out }).canvas;
  const sorted = Float32Array.from(sample).sort();
  const at = (p: number) => sorted[Math.min(sorted.length - 1, Math.floor(p * (sorted.length - 1)))]!;
  const lo = at(blackPct);
  const hi = Math.max(lo + 1e-4, at(0.9997));
  const k = 1023 / (hi - lo);
  const o = out.getContext('2d')!;
  const img = o.createImageData(w, h);
  const q = img.data;
  for (let i = 0; i < n; i++) {
    if (d[i * 4 + 3]! < 8) continue;
    const idx = Math.max(0, Math.min(1023, Math.round((L[i]! - lo) * k))) * 3;
    q[i * 4] = RAMP[idx]!;
    q[i * 4 + 1] = RAMP[idx + 1]!;
    q[i * 4 + 2] = RAMP[idx + 2]!;
    q[i * 4 + 3] = 255;
  }
  o.putImageData(img, 0, 0);
  toneCache = { gen, canvas: out };
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
