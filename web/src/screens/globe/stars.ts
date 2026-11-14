// The stars of the globe: the real Hipparcos catalogue (ESA, Hp < 6.6, 8,948 stars) drawn as points of light. Size and opacity fall steeply with
// magnitude, the brightest get a soft glow and faint diffraction spikes, the colour comes from B−V. Nothing is modelled.

/** [ra, dec, Hp, B−V], brightest first */
type Star = [number, number, number, number];

let stars: Star[] | null = null;
let loading: Promise<Star[]> | null = null;

/** Loads the catalogue once (250 kB). */
export function loadStars(): Promise<Star[]> {
  if (stars) return Promise.resolve(stars);
  return (loading ??= fetch('/globe/hipparcos_v66.json')
    .then((r) => (r.ok ? (r.json() as Promise<Star[]>) : []))
    .catch(() => [])
    .then((s) => (stars = s)));
}

export const starsNow = () => stars;

/** B−V → effective temperature (Ballesteros 2012) → blackbody RGB. */
function starColour(bv: number): [number, number, number] {
  const t = 4600 * (1 / (0.92 * bv + 1.7) + 1 / (0.92 * bv + 0.62));
  const k = Math.max(1000, Math.min(40000, t)) / 100;
  let r: number;
  let g: number;
  let b: number;
  if (k <= 66) {
    r = 255;
    g = 99.47 * Math.log(k) - 161.12;
    b = k <= 19 ? 0 : 138.52 * Math.log(k - 10) - 305.04;
  } else {
    r = 329.7 * (k - 60) ** -0.1332;
    g = 288.12 * (k - 60) ** -0.0755;
    b = 255;
  }
  const c = (v: number) => Math.max(0, Math.min(255, Math.round(v)));
  return [c(r), c(g), c(b)];
}

/** The colour pushed 1.55× away from the grey mean. */
const tint = (bv: number): [number, number, number] => {
  const [r, g, b] = starColour(bv);
  const m = (r + g + b) / 3;
  const s = (v: number) => Math.max(0, Math.min(255, Math.round(m + (v - m) * 1.55)));
  return [s(r), s(g), s(b)];
};

const radius = (m: number) => Math.max(0.45, 3.0 - 0.42 * m);
const alpha = (m: number) => Math.max(0.3, Math.min(1, 1.4 - 0.15 * m));
const glowR = (m: number) => Math.max(3, 20 - 3.1 * m);
const glowA = (m: number) => Math.max(0.05, Math.min(0.9, 0.62 - 0.1 * m));
const bin = (bv: number) => Math.max(0, Math.min(6, Math.round(((bv + 0.3) / 2.1) * 6)));

let sprites: HTMLCanvasElement[] | null = null;
/** Seven colour bins of the glow sprite: a 3-stop radial gradient. */
function glowSprites(): HTMLCanvasElement[] {
  return (sprites ??= [...Array(7)].map((_, k) => {
    const [r, g, b] = starColour(-0.3 + (k / 6) * 2.1);
    const R = 48;
    const c = document.createElement('canvas');
    c.width = c.height = R * 2;
    const x = c.getContext('2d')!;
    const gr = x.createRadialGradient(R, R, 0, R, R, R);
    gr.addColorStop(0, `rgba(${r},${g},${b},0.95)`);
    gr.addColorStop(0.12, `rgba(${r},${g},${b},0.34)`);
    gr.addColorStop(0.36, `rgba(${r},${g},${b},0.08)`);
    gr.addColorStop(1, `rgba(${r},${g},${b},0)`);
    x.fillStyle = gr;
    x.fillRect(0, 0, R * 2, R * 2);
    return c;
  }));
}

/** Slower frames drop to a lower tier: fewer glows and spikes. */
export interface StarTier {
  glowTo: number;
  spikeTo: number;
}
export const TIERS: StarTier[] = [
  { glowTo: 5.0, spikeTo: 2.7 },
  { glowTo: 3.6, spikeTo: 2.0 },
];

/**
 * Draws the stars. `project` gives the canvas place of a sky place and whether it is on the visible side; `fov` is the width of the sky on screen in degrees
 * (stars grow a little when zoomed in: scale min(2.4, √(78/fov))). Glows first (additive), then the points, then the spikes (additive).
 */
export function drawStars(
  ctx: CanvasRenderingContext2D,
  project: (ra: number, dec: number) => [number, number, boolean],
  view: { W: number; H: number; fov: number; tier?: number; alpha?: number },
): void {
  const all = stars;
  if (!all || !all.length) return;
  const { W, H } = view;
  const tier = TIERS[view.tier ?? 0] ?? TIERS[0]!;
  const sc = Math.min(2.4, Math.sqrt(78 / Math.max(1, view.fov)));
  const a0 = view.alpha ?? 1;
  const P = all.map((s) => project(s[0], s[1]));
  ctx.save();
  const spr = glowSprites();
  ctx.globalCompositeOperation = 'lighter';
  for (let i = 0; i < all.length; i++) {
    const [, , m, bv] = all[i]!;
    if (m > tier.glowTo) break;
    const [x, y, ok] = P[i]!;
    if (!ok || x < -90 || y < -90 || x > W + 90 || y > H + 90) continue;
    const r = glowR(m) * sc;
    ctx.globalAlpha = glowA(m) * a0;
    ctx.drawImage(spr[bin(bv)]!, x - r, y - r, r * 2, r * 2);
  }
  ctx.globalCompositeOperation = 'source-over';
  for (let i = 0; i < all.length; i++) {
    const [, , m, bv] = all[i]!;
    const [x, y, ok] = P[i]!;
    if (!ok || x < -6 || y < -6 || x > W + 6 || y > H + 6) continue;
    const [r, g, b] = tint(bv);
    ctx.globalAlpha = alpha(m) * a0;
    ctx.fillStyle = `rgb(${r},${g},${b})`;
    ctx.beginPath();
    ctx.arc(x, y, radius(m) * sc, 0, 7);
    ctx.fill();
  }
  ctx.globalCompositeOperation = 'lighter';
  ctx.lineWidth = 0.9;
  for (let i = 0; i < all.length; i++) {
    const [, , m, bv] = all[i]!;
    if (m > tier.spikeTo) break;
    const [x, y, ok] = P[i]!;
    if (!ok) continue;
    const [r, g, b] = tint(bv);
    const L = (28 - 7 * m) * sc;
    const d = L * 0.5;
    ctx.strokeStyle = `rgb(${r},${g},${b})`;
    ctx.globalAlpha = 0.32 * a0;
    ctx.beginPath();
    ctx.moveTo(x - L, y);
    ctx.lineTo(x + L, y);
    ctx.moveTo(x, y - L);
    ctx.lineTo(x, y + L);
    ctx.stroke();
    ctx.globalAlpha = 0.15 * a0;
    ctx.beginPath();
    ctx.moveTo(x - d, y - d);
    ctx.lineTo(x + d, y + d);
    ctx.moveTo(x + d, y - d);
    ctx.lineTo(x - d, y + d);
    ctx.stroke();
  }
  ctx.restore();
}

/** Screen field of view in degrees across a canvas W wide of the camera inside the sphere (stereographic, R pixels per radian at the centre). */
export const fovOfGlobe = (W: number, R: number) => Math.min(180, (4 * Math.atan(W / (4 * Math.max(1, R))) * 180) / Math.PI);
