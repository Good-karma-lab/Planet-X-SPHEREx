// The sky picture under the globe: HiPS tiles resampled to the globe or Mollweide view.
//
// The picture on screen is always complete: a new view is painted over the previous picture, moved and scaled to the new
// view, so pixels whose tiles are still on their way show the old picture (or the previous survey after a switch) instead
// of black. While the view moves, a coarse picture is painted every frame; once it rests, a full-resolution pass runs in
// slices of a few milliseconds per frame and replaces the picture when it is done. Nothing is painted while nothing changes.

import { D2R } from '../../lib/sky';
import { EQUI_NOTHING, EquiSampler, equiFrame, equiOf } from '../../lib/equi';
import { FROM_NOTHING, HIPS, bakedOf, facePt, orderFor, Sampler, tiles, type FacePt } from '../../lib/hips';

export interface SkyView {
  ra: number;
  dec: number;
  /** globe radius, CSS pixels */
  R: number;
  /** the turn of the view about its own axis, radians (the globe is turned freely, so north need not be up) */
  roll?: number;
  proj: 'globe' | 'moll';
  /** the globe seen from its centre: a perspective view of the sky with R as the focal length (pixels per radian at the centre) */
  inside?: boolean;
  W: number;
  H: number;
  /** the Mollweide map's zoom and the shift of its centre from the middle of the picture, CSS pixels */
  mz?: number;
  mx?: number;
  my?: number;
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

const mw = (v: SkyView) => Math.min(v.W - 20, (v.H - 20) * 2) * (v.mz ?? 1);
const mcx = (v: SkyView) => v.W / 2 + (v.mx ?? 0);
const mcy = (v: SkyView) => v.H / 2 + (v.my ?? 0);

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
  const c = [Math.cos(de) * Math.cos(ra), Math.cos(de) * Math.sin(ra), Math.sin(de)] as const;
  const e0 = [-Math.sin(ra), Math.cos(ra), 0] as const;
  const n0 = [-Math.sin(de) * Math.cos(ra), -Math.sin(de) * Math.sin(ra), Math.cos(de)] as const;
  const r = v.roll ?? 0;
  if (!r) return { c, e: e0, n: n0 };
  const cr = Math.cos(r);
  const sr = Math.sin(r);
  return {
    c,
    e: [e0[0] * cr + n0[0] * sr, e0[1] * cr + n0[1] * sr, e0[2] * cr + n0[2] * sr] as const,
    n: [n0[0] * cr - e0[0] * sr, n0[1] * cr - e0[1] * sr, n0[2] * cr - e0[2] * sr] as const,
  };
};

/** Canvas position of a sky place in a view (and whether it is on the visible side). */
export function projectIn(v: SkyView, ra: number, dec: number): [number, number, boolean] {
  if (v.proj === 'moll') {
    const th = mollweideTheta(dec * D2R);
    const lam = (((ra - 180 + 540) % 360) - 180) * D2R;
    return [mcx(v) - ((lam * Math.cos(th)) / Math.PI) * (mw(v) / 2), mcy(v) - Math.sin(th) * (mw(v) / 4), true];
  }
  const b = basis(v);
  const r = ra * D2R;
  const d = dec * D2R;
  const x = Math.cos(d) * Math.cos(r);
  const y = Math.cos(d) * Math.sin(r);
  const z = Math.sin(d);
  const zc = x * b.c[0] + y * b.c[1] + z * b.c[2];
  if (v.inside) {
    if (zc < 0.05) return [0, 0, false];
    return [v.W / 2 - ((x * b.e[0] + y * b.e[1]) / zc) * v.R, v.H / 2 - ((x * b.n[0] + y * b.n[1] + z * b.n[2]) / zc) * v.R, true];
  }
  if (zc < 0.02) return [0, 0, false];
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
  /** past the star map's resolution a real survey is faded in over it (the sampler of DSS2, the weight of it, its live tone) */
  fade?: { s: Sampler; w: number; gain: number; black: number };
  /** the layer is an equirectangular pyramid: its own sampler (no HEALPix) */
  equi?: EquiSampler;
  /** pixels of the view this pass could paint at all (the disc of the globe, the map's ellipse, the whole picture inside) */
  total: number;
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

/** the globe radius (pixels) from which a survey is faded in over the star map, and by which it has replaced it */
export const STAR_MAP_FADE_FROM = 8000;
export const STAR_MAP_FADE_TO = 16000;
/** How much of DSS2 shows over the star map at a globe scale R (0 = none, 1 = all): the pins' epoch follows it. */
export const starMapFade = (R: number): number => Math.min(1, Math.max(0, (R - STAR_MAP_FADE_FROM) / (STAR_MAP_FADE_TO - STAR_MAP_FADE_FROM)));

/** the black point (of 255) some baked layers need on top of what the manifest says: the faint colour of the empty sky of 2MASS */
const EXTRA_BLACK: Record<string, number> = { '2mass': 16 };

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
  private rgb2 = new Float32Array(3);

  /** One frame of work. `moving`: the view is changing, paint a coarse picture now. Returns true when the picture changed. */
  frame(view: SkyView, layer: SkyLayer[], moving: boolean): boolean {
    // the star map before the manifest has said where it is: nothing to paint yet
    if (layer[0]!.id === 'svs' && !equiOf('svs') && !HIPS['svs']) return false;
    if (moving) {
      this.pass = null;
      this.stale = true;
      // while the view moves the picture is painted at the finest step the machine keeps near 30 frames a second (1 = every pixel): it steps coarser only
      // when a frame took too long and finer again when it was quick, so a drag is not a drop to a quarter of the resolution that jumps back when it stops
      const t0 = performance.now();
      const s = this.moveS;
      const p = this.begin(view, layer, s);
      this.rows(p, Infinity);
      this.finish(p);
      const dt = performance.now() - t0;
      if (dt > 34 && this.moveS < 4) this.moveS++;
      else if (dt < 14 && this.moveS > 1) this.moveS--;
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
    equiFrame();
    // the dust map is the all-sky picture (the look of the globe) until its baked tiles are there
    const dustPic = layer[0]!.id === 'dust' && !bakedOf('dust');
    if (dustPic) {
      loadDust();
      DUST.onLoad = () => (this.stale = true);
    }
    const sw = Math.ceil(view.W / s);
    const sh = Math.ceil(view.H / s);
    const R = view.proj === 'globe' ? view.R : mw(view) / 5;
    // the SPHEREx backdrop is three frames of the cube: one sampler per colour
    const equi = !dustPic && layer.length === 1 && equiOf(layer[0]!.id) ? new EquiSampler(layer[0]!.id, R) : undefined;
    const samplers = dustPic || equi ? [] : layer.map((l) => new Sampler(l.id, orderFor(l.id, R)));
    // the star map is made from a map of 20″ pixels (one screen pixel at a globe radius of about 10 000 px): zoomed in past that it would only be a smooth
    // wash, so a real survey (DSS2) is faded in over it, fully by twice that radius
    let fade: Pass['fade'];
    if (layer.length === 1 && layer[0]!.id === 'svs' && R > STAR_MAP_FADE_FROM && HIPS['dss2']) {
      const t = Math.min(1, (R - STAR_MAP_FADE_FROM) / (STAR_MAP_FADE_TO - STAR_MAP_FADE_FROM));
      fade = { s: new Sampler('dss2', orderFor('dss2', R)), w: t * t * (3 - 2 * t), gain: 2.4, black: 16 };
    }
    return { view: { ...view }, layer, s, sw, sh, img: new ImageData(sw, sh), row: 0, samplers, painted: 0, total: 0, fade, equi };
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
    const rgb2 = this.rgb2;
    const one = layer.length === 1;
    const l0 = layer[0]!;
    // a survey whose tiles are in galactic coordinates (the dust map) is looked up at the galactic place of each pixel
    const dust = l0.id === 'dust' && !bakedOf('dust');
    const gal = !dust && layer.every((l) => HIPS[l.id]?.frame === 'galactic');
    const g0 = (l0.gain * 255) / (255 - l0.black);
    while (p.row < sh) {
      const j = p.row++;
      for (let i = 0; i < sw; i++) {
        let ra: number;
        let z: number;
        if (v.proj === 'moll') {
          const X = (i * s - mcx(v)) / (M / 2);
          const Y = -(j * s - mcy(v)) / (M / 4);
          if (X * X + Y * Y > 1) continue;
          const th = Math.asin(Y);
          z = (2 * th + Math.sin(2 * th)) / Math.PI;
          ra = 180 - ((Math.PI * X) / Math.cos(th)) / D2R;
        } else {
          let sx = (sw / 2 - i) / Rs;
          let sy = (sh / 2 - j) / Rs;
          let zz: number;
          if (v.inside) {
            // seen from the centre: the ray through the pixel, the focal length being Rs
            const n = Math.sqrt(1 + sx * sx + sy * sy);
            zz = 1 / n;
            sx /= n;
            sy /= n;
          } else {
            const rr = sx * sx + sy * sy;
            if (rr > 1) continue;
            zz = Math.sqrt(1 - rr);
          }
          const vx = zz * b.c[0] + sx * b.e[0] + sy * b.n[0];
          const vy = zz * b.c[1] + sx * b.e[1] + sy * b.n[1];
          z = zz * b.c[2] + sy * b.n[2];
          ra = Math.atan2(vy, vx) / D2R;
        }
        p.total++;
        const zc = z > 1 ? 1 : z < -1 ? -1 : z;
        if (gal) {
          const g = toGalactic(ra, zc);
          facePt(g[0], g[1] > 1 ? 1 : g[1] < -1 ? -1 : g[1], f);
        } else facePt(ra, zc, f);
        const q = (j * sw + i) * 4;
        if (dust) {
          const dd = DUST.data;
          if (!dd) continue;
          // bilinear between the picture's pixels (RA wraps round): zoomed in, the dust is smooth, not a grid of blocks
          const fx = ((((ra % 360) + 360) % 360) / 360) * DUST.w - 0.5;
          const fy = Math.min(DUST.h - 1, Math.max(0, ((90 - Math.asin(zc) / D2R) / 180) * DUST.h - 0.5));
          const x0 = Math.floor(fx);
          const y0 = Math.floor(fy);
          const ax = fx - x0;
          const ay = fy - y0;
          const xa = (x0 + DUST.w) % DUST.w;
          const xb = (x0 + 1 + DUST.w) % DUST.w;
          const yb = Math.min(DUST.h - 1, y0 + 1);
          const t00 = (y0 * DUST.w + xa) * 4;
          const t01 = (y0 * DUST.w + xb) * 4;
          const t10 = (yb * DUST.w + xa) * 4;
          const t11 = (yb * DUST.w + xb) * 4;
          for (let c = 0; c < 3; c++)
            o[q + c] = (dd[t00 + c]! * (1 - ax) + dd[t01 + c]! * ax) * (1 - ay) + (dd[t10 + c]! * (1 - ax) + dd[t11 + c]! * ax) * ay;
        } else if (one) {
          const fd = p.fade;
          const eq = p.equi;
          const have = eq ? eq.at(ra, Math.asin(zc) / D2R, rgb, 0) !== EQUI_NOTHING : samplers[0]!.at(f, rgb, 0) !== FROM_NOTHING;
          if (!have && !fd) continue;
          // a baked tile has its tone already; a live one (deeper than the baked orders, or while the baked ones are not read) gets the layer's
          let r0 = 0;
          let g1 = 0;
          let b1 = 0;
          if (have) {
            const bk = eq ? true : samplers[0]!.lastBaked;
            const B0 = eq ? { gain: eq.s.gain, black: eq.s.black } : bk ? bakedOf(l0.id) : undefined;
            // a baked layer may still want a black point of its own (2MASS's background is a faint maroon, 18 of 255)
            const B = B0 && { gain: B0.gain, black: Math.max(B0.black, EXTRA_BLACK[l0.id] ?? 0) };
            const bl = B ? B.black : l0.black;
            const gg = B ? (B.gain * 255) / (255 - B.black) : g0;
            r0 = Math.max(0, rgb[0]! - bl) * gg;
            g1 = Math.max(0, rgb[1]! - bl) * gg;
            b1 = Math.max(0, rgb[2]! - bl) * gg;
          }
          if (fd) {
            const got = fd.s.at(f, rgb2, 0) !== FROM_NOTHING;
            if (got) {
              const BD = fd.s.lastBaked ? bakedOf('dss2') : undefined;
              const bl = BD ? BD.black : fd.black;
              const gg = BD ? (BD.gain * 255) / (255 - BD.black) : (fd.gain * 255) / (255 - fd.black);
              const w = have ? fd.w : 1;
              r0 = r0 * (1 - w) + Math.max(0, rgb2[0]! - bl) * gg * w;
              g1 = g1 * (1 - w) + Math.max(0, rgb2[1]! - bl) * gg * w;
              b1 = b1 * (1 - w) + Math.max(0, rgb2[2]! - bl) * gg * w;
            } else if (!have) continue;
          }
          o[q] = r0;
          o[q + 1] = g1;
          o[q + 2] = b1;
        } else {
          let ok = true;
          for (let c = 0; c < 3 && ok; c++) {
            const l = layer[c]!;
            if (samplers[c]!.at(f, rgb, 0) === FROM_NOTHING) ok = false;
            else if (samplers[c]!.lastBaked) o[q + c] = Math.max(0, rgb[0]! - (bakedOf(l.id)?.black ?? 0)) * (bakedOf(l.id)?.gain ?? 1);
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
  /** the layers of the picture on show (ids joined with +): the pins stand on the date of THIS picture, not of the layer just asked for */
  shownKey = '';
  /** the step (source pixels per painted pixel) of a picture painted while the view moves */
  private moveS = 2;
  private switchKey = '';
  private switchSince = 0;

  private finish(p: Pass): void {
    const v = p.view;
    const key = p.layer.map((l) => l.id).join('+');
    const same = key === this.shownKey;
    if (!same && p.painted === 0) return; // nothing of the new survey yet: the picture on show stays
    // a new survey replaces the picture on show only once it covers (nearly) all of the view, or after 5 s whatever it covers (a survey with gaps
    // in the sky): until then the previous layer stays, not a patchwork of the two over an empty backdrop
    if (!same && this.shown) {
      this.switchSince = this.switchKey === key ? this.switchSince : performance.now();
      this.switchKey = key;
      if (p.painted < 0.96 * p.total && performance.now() - this.switchSince < 5000) {
        this.stale = true;
        return;
      }
    }
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
    if (old && same && old.proj === v.proj && !!old.inside === !!v.inside) {
      x.save();
      x.beginPath();
      if (v.proj === 'globe' && v.inside) x.rect(0, 0, v.W, v.H);
      else if (v.proj === 'globe') x.arc(v.W / 2, v.H / 2, v.R, 0, 7);
      else x.ellipse(mcx(v), mcy(v), mw(v) / 2, mw(v) / 4, 0, 0, 7);
      x.clip();
      x.imageSmoothingEnabled = true;
      if (v.proj === 'globe') {
        const c = projectIn(v, old.ra, old.dec);
        const k = v.R / old.R;
        if (c[2]) x.drawImage(this.canvas, c[0] - (k * old.W) / 2, c[1] - (k * old.H) / 2, k * old.W, k * old.H);
      } else {
        // the map picture on show, moved and scaled to the new zoom and shift
        const k = mw(v) / mw(old);
        x.drawImage(this.canvas, mcx(v) - k * mcx(old), mcy(v) - k * mcy(old), k * old.W, k * old.H);
      }
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
