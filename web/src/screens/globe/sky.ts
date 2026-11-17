// The sky picture under the globe: HiPS tiles resampled to the globe or Mollweide view.
//
// The picture on screen is always complete: a new view is painted over the previous picture, moved and scaled to the new
// view, so pixels whose tiles are still on their way show the old picture (or the previous survey after a switch) instead
// of black. While the view moves, a coarse picture is painted every frame; once it rests, a full-resolution pass runs in
// slices of a few milliseconds per frame and replaces the picture when it is done. Nothing is painted while nothing changes.

import { D2R } from '../../lib/sky';
import { EQUI_NOTHING, EquiSampler, equiFrame, equiOf } from '../../lib/equi';
import { FROM_NOTHING, HIPS, bakedOf, facePt, orderFor, Sampler, tiles, type FacePt } from '../../lib/hips';
import { GlSky, footprintOffset } from './skygl';

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
    return [v.W / 2 - ((x * b.e[0] + y * b.e[1] + z * b.e[2]) / zc) * v.R, v.H / 2 - ((x * b.n[0] + y * b.n[1] + z * b.n[2]) / zc) * v.R, true];
  }
  if (zc < 0.02) return [0, 0, false];
  return [v.W / 2 - (x * b.e[0] + y * b.e[1] + z * b.e[2]) * v.R, v.H / 2 - (x * b.n[0] + y * b.n[1] + z * b.n[2]) * v.R, true];
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

/** the globe radius (pixels) past which the star map holds no more detail (its source has 20″ pixels): the globe then shows Visible (DSS2), and the star map again below it */
export const STAR_MAP_TO_VISIBLE = 12000;

/** the black point (of 255) some baked layers need on top of what the manifest says: the faint colour of the empty sky of 2MASS */
const EXTRA_BLACK: Record<string, number> = { '2mass': 16 };

/** The GPU painter paints the sky wherever the browser has WebGL2; gl=0 in the address leaves it to the CPU loop (gl=1 takes it back). The choice holds until
 *  the page is loaded again. A view the GPU painter cannot paint is painted by the CPU loop. */
let glAsked = true;
export function glWanted(): boolean {
  // the picture check switches the painter on one page, without a reload
  const forced = (globalThis as { __skyGl?: boolean }).__skyGl;
  if (forced !== undefined) return forced;
  const m = /[?&]gl=([01])/.exec(location.hash);
  if (m) glAsked = m[1] === '1';
  return glAsked;
}

class SkyPainter {
  private glSky: GlSky | null = null;
  /** how the last frame was painted, for the tests and the frame-time reports: 'gpu', or 'cpu' with the reason the GPU painter left it */
  painter = 'cpu';

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
    if (glWanted()) {
      if (!moving && !this.stale && !this.pass) return false;
      const done = this.glFrame(view, layer);
      if (done !== null) return done;
    } else this.painter = 'cpu';
    if (moving) {
      // every frame is painted at full resolution (one source pixel per screen pixel), with the same level selection as at rest: no coarse step while the view moves and
      // no re-painting when it stops. A slow machine gets a lower frame rate, never a change of quality.
      this.pass = null;
      this.stale = false; // what was painted is complete: a stop is not followed by a second picture (only a tile that arrives later asks for one)
      const p = this.begin(view, layer, 1);
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

  /** One frame on the GPU (see skygl.ts); null when this view or layer is left to the CPU loop. */
  private glFrame(view: SkyView, layer: SkyLayer[]): boolean | null {
    const l0 = layer[0]!;
    const cpu = (why: string): null => {
      this.painter = `cpu: ${why}`;
      return null;
    };
    if ((l0.id === 'dust' && !bakedOf('dust')) || (layer.length === 1 && equiOf(l0.id))) return cpu('a picture of the whole sky');
    if (layer.length !== 1 && layer.length !== 3) return cpu(`${layer.length} surveys`);
    this.glSky ??= new GlSky();
    const g = this.glSky;
    if (!g.ok) return cpu('no WebGL2');
    tiles.frame();
    const moll = view.proj === 'moll';
    const R = moll ? mw(view) / 5 : view.R;
    const samplers = layer.map((l) => new Sampler(l.id, orderFor(l.id, R)));
    const sw = Math.ceil(view.W);
    const sh = Math.ceil(view.H);
    const b = basis(view);
    const gal = layer.every((l) => HIPS[l.id]?.frame === 'galactic');
    // the tones as the CPU loop applies them: one survey (a baked tile may want a black point on top), or three surveys, one per colour
    const tones = layer.map((l): [number, number, number, number] => {
      const B = bakedOf(l.id);
      const blackLive = l.black;
      const gainLive = (l.gain * 255) / (255 - l.black);
      if (layer.length === 3) return [B?.black ?? 0, B?.gain ?? 1, blackLive, gainLive];
      const blackBaked = B ? Math.max(B.black, EXTRA_BLACK[l.id] ?? 0) : blackLive;
      return [blackBaked, B ? (B.gain * 255) / (255 - blackBaked) : gainLive, blackLive, gainLive];
    });
    const r = g.paint({
      sw, sh, R: view.R, inside: !!view.inside, c: b.c, e: b.e, n: b.n, gal, samplers, tones,
      map: moll ? { x: mcx(view), y: mcy(view), w: mw(view) } : undefined,
      place: (i, j, f) => this.place(view, b, gal, 1, sw, sh, i, j, f),
    });
    if (!r) return cpu(g.why);
    this.painter = 'gpu';
    this.pass = null;
    this.stale = false;
    const shown0 = this.shown;
    this.finish({ view: { ...view }, layer, s: 1, sw, sh, painted: r.painted, total: r.total }, g.canvas);
    return this.shown !== shown0;
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
    const fade: Pass['fade'] = undefined;
    return { view: { ...view }, layer, s, sw, sh, img: new ImageData(sw, sh), row: 0, samplers, painted: 0, total: 0, fade, equi };
  }

  // the grid of exact sky places a band of rows is painted from (see rows): two grid rows and the centres of the blocks between them
  private gN = 0;
  private gFace = new Int8Array(0);
  private gX = new Float64Array(0);
  private gY = new Float64Array(0);
  private zone = 0;
  /** |sin Dec| of the place `place` answered last (in the frame of the tiles) */
  private za = 0;
  private gZ = new Float64Array(0);

  /** The exact sky place of a picture pixel as a face point (in the frame of the layer's tiles); false when the pixel is off the globe or the map. */
  private place(v: SkyView, b: ReturnType<typeof basis>, gal: boolean, s: number, sw: number, sh: number, i: number, j: number, f: FacePt): boolean {
    let ra: number;
    let z: number;
    if (v.proj === 'moll') {
      const M = mw(v);
      const X = (i * s - mcx(v)) / (M / 2);
      const Y = -(j * s - mcy(v)) / (M / 4);
      if (X * X + Y * Y > 1) return false;
      const th = Math.asin(Y);
      z = (2 * th + Math.sin(2 * th)) / Math.PI;
      ra = 180 - ((Math.PI * X) / Math.cos(th)) / D2R;
    } else {
      const Rs = v.R / s;
      let sx = (sw / 2 - i) / Rs;
      let sy = (sh / 2 - j) / Rs;
      let zz: number;
      if (v.inside) {
        const n = Math.sqrt(1 + sx * sx + sy * sy);
        zz = 1 / n;
        sx /= n;
        sy /= n;
      } else {
        const rr = sx * sx + sy * sy;
        if (rr > 1) return false;
        zz = Math.sqrt(1 - rr);
      }
      const vx = zz * b.c[0] + sx * b.e[0] + sy * b.n[0];
      const vy = zz * b.c[1] + sx * b.e[1] + sy * b.n[1];
      z = zz * b.c[2] + sx * b.e[2] + sy * b.n[2]; // the east axis has a z part when the view is rolled
      ra = Math.atan2(vy, vx) / D2R;
    }
    let zc = z > 1 ? 1 : z < -1 ? -1 : z;
    if (gal) {
      const g = toGalactic(ra, zc);
      ra = g[0];
      zc = g[1] > 1 ? 1 : g[1] < -1 ? -1 : g[1];
    }
    facePt(ra, zc, f);
    // the two zones of a HEALPix face (equatorial belt, polar cap) are two different maps that meet with a kink
    this.zone = Math.abs(zc) > 2 / 3 ? 1 : 0;
    this.za = Math.abs(zc);
    return true;
  }

  /** One pixel of a picture made of three surveys (one per colour), at the face point f. */
  private three(p: Pass, f: FacePt, q: number): boolean {
    const o = p.img.data;
    const rgb = this.rgb;
    for (let c = 0; c < 3; c++) {
      const l = p.layer[c]!;
      const sm = p.samplers[c]!;
      if (sm.at(f, rgb, 0) === FROM_NOTHING) return false;
      if (sm.lastBaked) o[q + c] = Math.max(0, rgb[0]! - (bakedOf(l.id)?.black ?? 0)) * (bakedOf(l.id)?.gain ?? 1);
      else o[q + c] = Math.max(0, rgb[0]! - l.black) * ((l.gain * 255) / (255 - l.black));
    }
    o[q + 3] = 255;
    return true;
  }

  /**
   * Paints rows until the deadline; true when the pass is done.
   *
   * The sky place of a pixel (a square root, an arctangent, the HEALPix face) is worked out exactly only at the corners and the centre of blocks of 8×8 pixels.
   * Where a block lies inside one face and one zone, and the centre agrees with the mean of the corners to 1/20 of a screen pixel, the places inside it are
   * interpolated (the map is smooth there) and each row is painted as one run of the tile sampler. Every other block (the limb of the globe, a face edge, a pole)
   * is worked out pixel by pixel. The picture is the same; a frame costs a fraction of the arithmetic, so the globe turns smoothly in every browser.
   */
  private rows(p: Pass, deadline: number): boolean {
    const { view: v, s, sw, sh, samplers, layer } = p;
    const l0 = layer[0]!;
    if (p.equi || (l0.id === 'dust' && !bakedOf('dust'))) return this.rowsPlain(p, deadline);
    const o = p.img.data;
    const b = basis(v);
    const one = layer.length === 1;
    const gal = layer.every((l) => HIPS[l.id]?.frame === 'galactic');
    // the tone of a single survey: a baked tile has its own (and may want a black point on top), a live one the layer's
    const B0 = bakedOf(l0.id);
    const blackLive = l0.black;
    const gainLive = (l0.gain * 255) / (255 - l0.black);
    const blackBaked = B0 ? Math.max(B0.black, EXTRA_BLACK[l0.id] ?? 0) : blackLive;
    const gainBaked = B0 ? (B0.gain * 255) / (255 - blackBaked) : gainLive;
    const sm = samplers[0]!;
    // a face is 2^order tiles of w pixels across: 1/20 of a tile pixel (no more than a screen pixel) in face units
    let across = 512;
    for (const x of samplers) across = Math.max(across, (x.order >= 0 ? 2 ** x.order : 1) * (HIPS[x.id]?.w ?? 512));
    const tol = 0.05 / across;
    const BL = 8;
    const nG = Math.ceil(sw / BL) + 1;
    if (this.gN !== nG) {
      this.gN = nG;
      this.gFace = new Int8Array(nG * 3);
      this.gX = new Float64Array(nG * 3);
      this.gY = new Float64Array(nG * 3);
      this.gZ = new Float64Array(nG);
    }
    const gF = this.gFace;
    const gX = this.gX;
    const gY = this.gY;
    const f = this.f;
    while (p.row < sh) {
      const j0 = p.row;
      p.row += BL;
      const jn = Math.min(BL, sh - j0);
      // the exact places: grid row 0 at j0, row 1 at j0 + 8, row 2 the centres of the blocks; face −1 where there is no sky, the zone in bit 4
      for (let g = 0; g < nG; g++) {
        for (let r = 0; r < 3; r++) {
          const ok = r < 2 ? this.place(v, b, gal, s, sw, sh, g * BL, j0 + r * BL, f) : this.place(v, b, gal, s, sw, sh, g * BL + BL / 2, j0 + BL / 2, f);
          const k = r * nG + g;
          gF[k] = ok ? f.face + this.zone * 16 : -1;
          gX[k] = f.x;
          gY[k] = f.y;
          if (r === 2) this.gZ[g] = this.za;
        }
      }
      for (let g = 0; g + 1 < nG; g++) {
        const i0 = g * BL;
        const cnt = Math.min(BL, sw - i0);
        if (cnt <= 0) break;
        const fa = gF[g]!;
        const x00 = gX[g]!;
        const y00 = gY[g]!;
        const x10 = gX[g + 1]!;
        const y10 = gY[g + 1]!;
        const x01 = gX[nG + g]!;
        const y01 = gY[nG + g]!;
        const x11 = gX[nG + g + 1]!;
        const y11 = gY[nG + g + 1]!;
        const smooth =
          fa >= 0 &&
          gF[g + 1] === fa &&
          gF[nG + g] === fa &&
          gF[nG + g + 1] === fa &&
          gF[2 * nG + g] === fa &&
          Math.abs((x00 + x10 + x01 + x11) / 4 - gX[2 * nG + g]!) < tol &&
          Math.abs((y00 + y10 + y01 + y11) / 4 - gY[2 * nG + g]!) < tol;
        if (smooth) {
          const face = fa & 15;
          // the two reads that make the texel's footprint round (see footprintOffset): one offset for the block, from the place of its centre
          const off = footprintOffset(this.gZ[g]!);
          for (let jj = 0; jj < jn; jj++) {
            const a = jj / BL;
            const xl = x00 + (x01 - x00) * a;
            const yl = y00 + (y01 - y00) * a;
            const dx = (x10 + (x11 - x10) * a - xl) / BL;
            const dy = (y10 + (y11 - y10) * a - yl) / BL;
            const q = ((j0 + jj) * sw + i0) * 4;
            p.total += cnt;
            if (one) p.painted += sm.run(face, xl, yl, dx, dy, cnt, o, q, blackBaked, gainBaked, blackLive, gainLive, off);
            else {
              for (let i = 0; i < cnt; i++) {
                f.face = face;
                f.x = xl + dx * i;
                f.y = yl + dy * i;
                if (this.three(p, f, q + i * 4)) p.painted++;
              }
            }
          }
        } else if (fa >= 0 || gF[g + 1]! >= 0 || gF[nG + g]! >= 0 || gF[nG + g + 1]! >= 0 || gF[2 * nG + g]! >= 0) {
          for (let jj = 0; jj < jn; jj++) {
            for (let i = 0; i < cnt; i++) {
              if (!this.place(v, b, gal, s, sw, sh, i0 + i, j0 + jj, f)) continue;
              p.total++;
              const q = ((j0 + jj) * sw + i0 + i) * 4;
              if (one) p.painted += sm.run(f.face, f.x, f.y, 0, 0, 1, o, q, blackBaked, gainBaked, blackLive, gainLive, footprintOffset(this.za));
              else if (this.three(p, f, q)) p.painted++;
            }
          }
        }
      }
      if (performance.now() > deadline) return false;
    }
    return true;
  }

  /** The same, pixel by pixel: the layers that are one picture of the whole sky (the dust picture, an equirectangular map). */
  private rowsPlain(p: Pass, deadline: number): boolean {
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
          z = zz * b.c[2] + sx * b.e[2] + sy * b.n[2]; // the east axis has a z part when the view is rolled
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
  private switchKey = '';
  private switchSince = 0;

  private finish(p: Pick<Pass, 'view' | 'layer' | 's' | 'sw' | 'sh' | 'painted' | 'total'> & { img?: ImageData }, src?: HTMLCanvasElement): void {
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
    const sc = src ?? this.scratch;
    if (!src) {
      sc.width = p.sw;
      sc.height = p.sh;
      sc.getContext('2d')!.putImageData(p.img!, 0, 0);
    }
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
// for the picture checks and the frame-time reports
(globalThis as { __skyPainter?: () => string; __sky?: SkyPainter }).__skyPainter = () => sky.painter;
(globalThis as { __sky?: SkyPainter }).__sky = sky;
