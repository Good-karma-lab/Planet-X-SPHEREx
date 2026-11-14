// The sky picture under the globe: HiPS tiles resampled to the globe or Mollweide view.
//
// The picture on screen is always complete: a new view is painted over the previous picture, moved and scaled to the new
// view, so pixels whose tiles are still on their way show the old picture (or the previous survey after a switch) instead
// of black. While the view moves, a coarse picture is painted every frame; once it rests, a full-resolution pass runs in
// slices of a few milliseconds per frame and replaces the picture when it is done. Nothing is painted while nothing changes.

import { D2R } from '../../lib/sky';
import { FROM_NOTHING, HIPS, facePt, orderFor, Sampler, tiles, type FacePt } from '../../lib/hips';

export interface SkyView {
  ra: number;
  dec: number;
  /** globe radius, CSS pixels */
  R: number;
  proj: 'globe' | 'moll';
  W: number;
  H: number;
}

export interface SkyLayer {
  id: string;
  gain: number;
  black: number;
}

/** ICRS (equatorial) to galactic: the rows of the standard rotation matrix. */
const GAL = [
  [-0.0548755604162154, -0.873437090234885, -0.4838350155487132],
  [0.4941094278755837, -0.4448296299600112, 0.7469822444972189],
  [-0.8676661490190047, -0.1980763734312015, 0.4559837761750669],
] as const;

/** Galactic longitude (degrees) and sin(latitude) of an equatorial place given by RA (degrees) and z = sin(dec). */
export function toGalactic(ra: number, z: number): [number, number] {
  const c = Math.sqrt(Math.max(0, 1 - z * z));
  const x = c * Math.cos(ra * D2R);
  const y = c * Math.sin(ra * D2R);
  const gx = GAL[0][0] * x + GAL[0][1] * y + GAL[0][2] * z;
  const gy = GAL[1][0] * x + GAL[1][1] * y + GAL[1][2] * z;
  const gz = GAL[2][0] * x + GAL[2][1] * y + GAL[2][2] * z;
  return [Math.atan2(gy, gx) / D2R, gz];
}

const mw = (v: SkyView) => Math.min(v.W - 20, (v.H - 20) * 2);

function mollweideTheta(phi: number): number {
  let th = phi;
  for (let i = 0; i < 12; i++) {
    const d = 2 + 2 * Math.cos(2 * th);
    if (Math.abs(d) < 1e-9) break;
    th -= (2 * th + Math.sin(2 * th) - Math.PI * Math.sin(phi)) / d;
  }
  return th;
}

const basis = (v: SkyView) => {
  const ra = v.ra * D2R;
  const de = v.dec * D2R;
  return {
    c: [Math.cos(de) * Math.cos(ra), Math.cos(de) * Math.sin(ra), Math.sin(de)] as const,
    e: [-Math.sin(ra), Math.cos(ra), 0] as const,
    n: [-Math.sin(de) * Math.cos(ra), -Math.sin(de) * Math.sin(ra), Math.cos(de)] as const,
  };
};

/** Canvas position of a sky place in a view (and whether it is on the visible side). */
export function projectIn(v: SkyView, ra: number, dec: number): [number, number, boolean] {
  if (v.proj === 'moll') {
    const th = mollweideTheta(dec * D2R);
    const lam = (((ra - 180 + 540) % 360) - 180) * D2R;
    return [v.W / 2 - ((lam * Math.cos(th)) / Math.PI) * (mw(v) / 2), v.H / 2 - Math.sin(th) * (mw(v) / 4), true];
  }
  const b = basis(v);
  const r = ra * D2R;
  const d = dec * D2R;
  const x = Math.cos(d) * Math.cos(r);
  const y = Math.cos(d) * Math.sin(r);
  const z = Math.sin(d);
  if (x * b.c[0] + y * b.c[1] + z * b.c[2] < 0.02) return [0, 0, false];
  return [v.W / 2 - (x * b.e[0] + y * b.e[1]) * v.R, v.H / 2 - (x * b.n[0] + y * b.n[1] + z * b.n[2]) * v.R, true];
}

interface Pass {
  view: SkyView;
  layer: SkyLayer[];
  s: number;
  sw: number;
  sh: number;
  img: ImageData;
  row: number;
  samplers: Sampler[];
  /** pixels this pass painted (a pass of a new survey that has nothing yet leaves the picture on show alone) */
  painted: number;
}

/** The all-sky dust map: one equirectangular picture of the whole sky (RA to the right, Dec down), loaded once. */
const DUST = { w: 0, h: 0, data: null as Uint8ClampedArray | null, loading: false, onLoad: (() => {}) as () => void };
function loadDust(): void {
  if (DUST.data || DUST.loading) return;
  DUST.loading = true;
  const im = new Image();
  im.onload = () => {
    const c = document.createElement('canvas');
    c.width = DUST.w = im.width;
    c.height = DUST.h = im.height;
    const x = c.getContext('2d', { willReadFrequently: true })!;
    x.drawImage(im, 0, 0);
    DUST.data = x.getImageData(0, 0, c.width, c.height).data;
    DUST.onLoad();
  };
  im.onerror = () => (DUST.loading = false);
  im.src = '/globe/dust_allsky.jpg';
}

class SkyPainter {
  /** the picture on screen, W×H CSS pixels */
  canvas = document.createElement('canvas');
  private spare = document.createElement('canvas');
  /** the view the picture shows */
  shown: SkyView | null = null;
  /** a full-resolution pass is needed (tiles arrived, the view came to rest) */
  stale = true;
  private pass: Pass | null = null;
  private scratch = document.createElement('canvas');
  private f: FacePt = { face: 0, x: 0, y: 0 };
  private rgb = new Float32Array(3);

  /** One frame of work. `moving`: the view is changing, paint a coarse picture now. Returns true when the picture changed. */
  frame(view: SkyView, layer: SkyLayer[], moving: boolean): boolean {
    if (moving) {
      this.pass = null;
      this.stale = true;
      const s = Math.max(2, Math.ceil(Math.sqrt((view.W * view.H) / 70000)));
      const p = this.begin(view, layer, s);
      this.rows(p, Infinity);
      this.finish(p);
      return true;
    }
    if (!this.pass) {
      if (!this.stale) return false;
      this.stale = false;
      this.pass = this.begin(view, layer, 1);
    }
    const p = this.pass;
    if (this.rows(p, performance.now() + 9)) {
      this.pass = null;
      this.finish(p);
      return true;
    }
    return false;
  }

  private begin(view: SkyView, layer: SkyLayer[], s: number): Pass {
    tiles.frame();
    if (layer[0]!.id === 'dust') {
      loadDust();
      DUST.onLoad = () => (this.stale = true);
    }
    const sw = Math.ceil(view.W / s);
    const sh = Math.ceil(view.H / s);
    const R = view.proj === 'globe' ? view.R : mw(view) / 5;
    // the SPHEREx backdrop is three frames of the cube: one sampler per colour
    const samplers = layer[0]!.id === 'dust' ? [] : layer.map((l) => new Sampler(l.id, orderFor(l.id, R)));
    return { view: { ...view }, layer, s, sw, sh, img: new ImageData(sw, sh), row: 0, samplers, painted: 0 };
  }

  /** Paints rows until the deadline; true when the pass is done. */
  private rows(p: Pass, deadline: number): boolean {
    const { view: v, s, sw, sh, samplers, layer } = p;
    const o = p.img.data;
    const b = basis(v);
    const Rs = v.R / s;
    const M = mw(v);
    const f = this.f;
    const rgb = this.rgb;
    const one = layer.length === 1;
    const l0 = layer[0]!;
    // a survey whose tiles are in galactic coordinates (the dust map) is looked up at the galactic place of each pixel
    const dust = l0.id === 'dust';
    const gal = !dust && layer.every((l) => HIPS[l.id]?.frame === 'galactic');
    const g0 = (l0.gain * 255) / (255 - l0.black);
    while (p.row < sh) {
      const j = p.row++;
      for (let i = 0; i < sw; i++) {
        let ra: number;
        let z: number;
        if (v.proj === 'moll') {
          const X = (i * s - v.W / 2) / (M / 2);
          const Y = -(j * s - v.H / 2) / (M / 4);
          if (X * X + Y * Y > 1) continue;
          const th = Math.asin(Y);
          z = (2 * th + Math.sin(2 * th)) / Math.PI;
          ra = 180 - ((Math.PI * X) / Math.cos(th)) / D2R;
        } else {
          const sx = (sw / 2 - i) / Rs;
          const sy = (sh / 2 - j) / Rs;
          const rr = sx * sx + sy * sy;
          if (rr > 1) continue;
          const zz = Math.sqrt(1 - rr);
          const vx = zz * b.c[0] + sx * b.e[0] + sy * b.n[0];
          const vy = zz * b.c[1] + sx * b.e[1] + sy * b.n[1];
          z = zz * b.c[2] + sy * b.n[2];
          ra = Math.atan2(vy, vx) / D2R;
        }
        const zc = z > 1 ? 1 : z < -1 ? -1 : z;
        if (gal) {
          const g = toGalactic(ra, zc);
          facePt(g[0], g[1] > 1 ? 1 : g[1] < -1 ? -1 : g[1], f);
        } else facePt(ra, zc, f);
        const q = (j * sw + i) * 4;
        if (dust) {
          const dd = DUST.data;
          if (!dd) continue;
          const tx = Math.min(DUST.w - 1, Math.max(0, ((((ra % 360) + 360) % 360) / 360) * DUST.w) | 0);
          const ty = Math.min(DUST.h - 1, Math.max(0, ((90 - Math.asin(zc) / D2R) / 180) * DUST.h) | 0);
          const t = (ty * DUST.w + tx) * 4;
          o[q] = dd[t]!;
          o[q + 1] = dd[t + 1]!;
          o[q + 2] = dd[t + 2]!;
        } else if (one) {
          if (samplers[0]!.at(f, rgb, 0) === FROM_NOTHING) continue;
          o[q] = Math.max(0, rgb[0]! - l0.black) * g0;
          o[q + 1] = Math.max(0, rgb[1]! - l0.black) * g0;
          o[q + 2] = Math.max(0, rgb[2]! - l0.black) * g0;
        } else {
          let ok = true;
          for (let c = 0; c < 3 && ok; c++) {
            const l = layer[c]!;
            if (samplers[c]!.at(f, rgb, 0) === FROM_NOTHING) ok = false;
            else o[q + c] = Math.max(0, rgb[0]! - l.black) * ((l.gain * 255) / (255 - l.black));
          }
          if (!ok) continue;
        }
        o[q + 3] = 255;
        p.painted++;
      }
      if ((j & 7) === 7 && performance.now() > deadline) return false;
    }
    return true;
  }

  /** The new pixels over the previous picture, moved and scaled to the new view. */
  /** the survey on show: a picture is carried over to a new view only while it is the same survey (no half-old, half-new picture after a switch) */
  private shownKey = '';

  private finish(p: Pass): void {
    const v = p.view;
    const key = p.layer.map((l) => l.id).join('+');
    const same = key === this.shownKey;
    if (!same && p.painted === 0) return; // nothing of the new survey yet: the picture on show stays
    this.shownKey = key;
    const sc = this.scratch;
    sc.width = p.sw;
    sc.height = p.sh;
    sc.getContext('2d')!.putImageData(p.img, 0, 0);
    const nx = this.spare;
    nx.width = Math.max(1, Math.round(v.W));
    nx.height = Math.max(1, Math.round(v.H));
    const x = nx.getContext('2d')!;
    const old = this.shown;
    if (old && same && old.proj === v.proj) {
      x.save();
      x.beginPath();
      if (v.proj === 'globe') x.arc(v.W / 2, v.H / 2, v.R, 0, 7);
      else x.ellipse(v.W / 2, v.H / 2, mw(v) / 2, mw(v) / 4, 0, 0, 7);
      x.clip();
      x.imageSmoothingEnabled = true;
      if (v.proj === 'globe') {
        const c = projectIn(v, old.ra, old.dec);
        const k = v.R / old.R;
        if (c[2]) x.drawImage(this.canvas, c[0] - (k * old.W) / 2, c[1] - (k * old.H) / 2, k * old.W, k * old.H);
      } else x.drawImage(this.canvas, (v.W - old.W) / 2, (v.H - old.H) / 2, old.W, old.H);
      x.restore();
    }
    x.imageSmoothingEnabled = true;
    x.drawImage(sc, 0, 0, p.sw * p.s, p.sh * p.s);
    this.spare = this.canvas;
    this.canvas = nx;
    this.shown = v;
  }
}

/** One painter for the page: the last picture survives a visit to another screen. */
export const sky = new SkyPainter();
