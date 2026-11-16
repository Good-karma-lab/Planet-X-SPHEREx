// The sky painted on the GPU (WebGL2), behind the address flag gl=1; the CPU painter in sky.ts stays the reference and the fallback.
//
// One full-screen triangle. The fragment shader does for each pixel what the CPU loop does: the pixel's direction on the sky from the view, its HEALPix base
// face and place inside it, the tile under it, a bilinear read clamped inside that tile, the tone (value − black) × gain. The tiles in sight are layers of one
// texture array (512 × 512); a small index texture says, for each cell of the tile grid near the view (a window of 32 × 32 cells per base face), which layer
// paints it and how many orders shallower that tile is than the order asked for (a tile that is not here yet is painted from the nearest shallower one, as on
// the CPU). The index is rebuilt every frame from a coarse grid of exact places (every 8th pixel); only the tiles that grid touches are asked for.

import type { FacePt, Sampler, TilePixels } from '../../lib/hips';

const TILE = 512;
const LAYERS = 96;
const WIN = 32;
const STEP = 8;
/** how far apart (in texels, times s − 1) the two reads along RA are that make a HEALPix texel's footprint round; 0.9 by measurement (lag-1 autocorrelation along Dec = along RA at |Dec| 45–85°) */
const ROUND = 0.9;

const VERT = `#version 300 es
void main() { gl_Position = vec4(float((gl_VertexID & 1) * 4 - 1), float((gl_VertexID & 2) * 2 - 1), 0.0, 1.0); }`;

const FRAG = `#version 300 es
precision highp float;
precision highp int;
precision highp sampler2DArray;
uniform vec2 uSize;
uniform vec2 uHalf;
uniform float uR;
uniform int uInside;
uniform int uMoll;
uniform vec3 uMap;
uniform int uChannels;
uniform float uRound;
uniform vec3 uC;
uniform vec3 uE;
uniform vec3 uN;
uniform int uGal;
uniform int uOrder[3];
uniform ivec2 uOrigin[36];
uniform vec4 uTone[3];
uniform sampler2D uIndex;
uniform sampler2DArray uTiles;
out vec4 o;
const float HALF_PI = 1.5707963267948966;
const float BELOW_ONE = 0.99999994;
const mat3 GAL = mat3(
  -0.0548755604162154, 0.4941094278755837, -0.8676661490190047,
  -0.873437090234885, -0.4448296299600112, -0.1980763734312015,
  -0.4838350155487132, 0.7469822444972189, 0.4559837761750669);
const float PI = 3.141592653589793;
// one survey at a face point: its colour with the tone applied; false when no tile is at hand
bool sampleAt(int k, int face, float x, float y, float za, out vec3 c) {
  float N = exp2(float(uOrder[k]));
  ivec2 cell = ivec2(int(min(floor(x * N), N - 1.0)), int(min(floor(y * N), N - 1.0))) - uOrigin[k * 12 + face];
  if (cell.x < 0 || cell.y < 0 || cell.x >= ${WIN} || cell.y >= ${WIN}) return false;
  vec4 e = texelFetch(uIndex, ivec2(cell.x, (k * 12 + face) * ${WIN} + cell.y), 0);
  int slot = int(e.r * 255.0 + 0.5) - 1;
  if (slot < 0) return false;
  float M = exp2(float(uOrder[k]) - floor(e.g * 255.0 + 0.5));
  float fx = x * M - floor(x * M);
  float fy = y * M - floor(y * M);
  const float h = 0.5 / ${TILE}.0;
  // the tile's rows run along x, its columns along y; LINEAR with the coordinates clamped half a texel inside the tile is the CPU's clamped bilinear read
  // A HEALPix texel is a rhombus on the sky, longer towards the pole than along RA by s = 8 / (3 pi (1 - z^2)) in the equatorial belt and 8 / (pi (1 + |z|)) in
  // the polar caps: 0.85 at the equator, 1.53 at |Dec| 42 degrees, 1.27 at the poles. A star field whose faint stars are one texel each is then drawn as dashes
  // that all point at the pole. Two reads half of (s - 1) of a texel apart along RA (the (1, -1) diagonal of the face) make the footprint as wide as it is long.
  float s = za <= 2.0 / 3.0 ? 8.0 / (3.0 * PI * (1.0 - za * za)) : 8.0 / (PI * (1.0 + za));
  float off = clamp(uRound * (s - 1.0), 0.0, 0.5) / ${TILE}.0;
  vec2 uv = vec2(fy, fx);
  vec3 t = (texture(uTiles, vec3(clamp(uv + vec2(-off, off), h, 1.0 - h), float(slot))).rgb + texture(uTiles, vec3(clamp(uv + vec2(off, -off), h, 1.0 - h), float(slot))).rgb) * 127.5;
  float bl = e.b > 0.5 ? uTone[k].x : uTone[k].z;
  float gg = e.b > 0.5 ? uTone[k].y : uTone[k].w;
  c = max(t - bl, 0.0) * gg / 255.0;
  return true;
}
void main() {
  // the CPU painter samples the sky at the pixel's corner (i, j), rows counted from the top
  float i = gl_FragCoord.x - 0.5;
  float j = uSize.y - 0.5 - gl_FragCoord.y;
  vec3 v;
  if (uMoll == 1) {
    // the Mollweide map: uMap is its centre on the picture and its width
    float X = (i - uMap.x) / (uMap.z / 2.0);
    float Y = -(j - uMap.y) / (uMap.z / 4.0);
    if (X * X + Y * Y > 1.0) discard;
    float th = asin(Y);
    float zm = clamp((2.0 * th + sin(2.0 * th)) / PI, -1.0, 1.0);
    float ra = PI - PI * X / cos(th);
    float cd = sqrt(max(0.0, 1.0 - zm * zm));
    v = vec3(cd * cos(ra), cd * sin(ra), zm);
  } else {
    float sx = (uHalf.x - i) / uR;
    float sy = (uHalf.y - j) / uR;
    float zz;
    if (uInside == 1) {
      float n = sqrt(1.0 + sx * sx + sy * sy);
      zz = 1.0 / n;
      sx /= n;
      sy /= n;
    } else {
      float rr = sx * sx + sy * sy;
      if (rr > 1.0) discard;
      zz = sqrt(1.0 - rr);
    }
    v = zz * uC + sx * uE + sy * uN;
  }
  if (uGal == 1) v = GAL * v;
  v = normalize(v);
  float z = v.z;
  float za = abs(z);
  float tt = mod(atan(v.y, v.x) / HALF_PI, 4.0);
  if (tt >= 4.0) tt -= 4.0;
  int face;
  float x;
  float y;
  if (za <= 2.0 / 3.0) {
    float a = 0.5 + tt - 0.75 * z;
    float b = 0.5 + tt + 0.75 * z;
    float fa = floor(a);
    float fb = floor(b);
    int ip = int(fa);
    int im = int(fb);
    face = ip == im ? (ip % 4) + 4 : (ip < im ? ip % 4 : (im % 4) + 8);
    x = b - fb;
    y = 1.0 - (a - fa);
  } else {
    float ntt = min(floor(tt), 3.0);
    float tp = tt - ntt;
    // 1 − |z| from the two small components: 32-bit arithmetic keeps the place exact right up to the pole
    float tmp = sqrt(3.0 * dot(v.xy, v.xy) / (1.0 + za));
    float p = tp * tmp;
    float m = (1.0 - tp) * tmp;
    if (z >= 0.0) { face = int(ntt); x = 1.0 - m; y = 1.0 - p; }
    else { face = int(ntt) + 8; x = p; y = m; }
  }
  x = clamp(x, 0.0, BELOW_ONE);
  y = clamp(y, 0.0, BELOW_ONE);
  vec3 c;
  if (uChannels == 1) {
    if (!sampleAt(0, face, x, y, za, c)) discard;
    o = vec4(c, 1.0);
  } else {
    // three surveys, one per colour (the first channel of each)
    vec3 r;
    vec3 g;
    vec3 b;
    if (!sampleAt(0, face, x, y, za, r) || !sampleAt(1, face, x, y, za, g) || !sampleAt(2, face, x, y, za, b)) discard;
    o = vec4(r.r, g.r, b.r, 1.0);
  }
}`;

export interface GlJob {
  sw: number;
  sh: number;
  R: number;
  inside: boolean;
  c: readonly number[];
  e: readonly number[];
  n: readonly number[];
  gal: boolean;
  /** the Mollweide map (its centre on the picture and its width, CSS pixels) in place of the globe */
  map?: { x: number; y: number; w: number };
  /** one survey, or three (one per colour) */
  samplers: Sampler[];
  /** for each survey: black and gain of a baked tile, black and gain of a live one */
  tones: [number, number, number, number][];
  /** the exact sky place of a picture pixel as a face point; false when the pixel is off the globe */
  place: (i: number, j: number, f: FacePt) => boolean;
}

export class GlSky {
  readonly canvas = document.createElement('canvas');
  private gl: WebGL2RenderingContext | null;
  private prog: WebGLProgram | null = null;
  private u: Record<string, WebGLUniformLocation | null> = {};
  private index = new Uint8Array(WIN * WIN * 36 * 4);
  private origin = new Int32Array(72);
  private gFace = new Int8Array(0);
  private gX = new Float64Array(0);
  private gY = new Float64Array(0);
  private slotOf = new Map<TilePixels, number>();
  private slotTile: (TilePixels | null)[] = new Array<TilePixels | null>(LAYERS).fill(null);
  private slotUsed = new Int32Array(LAYERS);
  private frameNo = 0;
  private f: FacePt = { face: 0, x: 0, y: 0 };
  /** why the last frame was left to the CPU painter ('' when it was painted here) */
  why = '';

  constructor() {
    this.gl = this.canvas.getContext('webgl2', { alpha: true, premultipliedAlpha: true, antialias: false, depth: false, stencil: false });
    const gl = this.gl;
    if (!gl) return;
    const sh = (type: number, src: string) => {
      const s = gl.createShader(type)!;
      gl.shaderSource(s, src);
      gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) ?? 'shader');
      return s;
    };
    try {
      const p = gl.createProgram()!;
      gl.attachShader(p, sh(gl.VERTEX_SHADER, VERT));
      gl.attachShader(p, sh(gl.FRAGMENT_SHADER, FRAG));
      gl.linkProgram(p);
      if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p) ?? 'link');
      this.prog = p;
    } catch (err) {
      console.warn('GPU sky painter is off:', err);
      this.gl = null;
      return;
    }
    for (const n of ['uSize', 'uHalf', 'uR', 'uInside', 'uMoll', 'uMap', 'uChannels', 'uRound', 'uC', 'uE', 'uN', 'uGal', 'uOrder', 'uOrigin', 'uTone', 'uIndex', 'uTiles']) this.u[n] = gl.getUniformLocation(this.prog, n);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, gl.createTexture());
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, WIN, WIN * 36, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    for (const k of [gl.TEXTURE_MIN_FILTER, gl.TEXTURE_MAG_FILTER]) gl.texParameteri(gl.TEXTURE_2D, k, gl.NEAREST);
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D_ARRAY, gl.createTexture());
    gl.texStorage3D(gl.TEXTURE_2D_ARRAY, 1, gl.RGBA8, TILE, TILE, LAYERS);
    for (const k of [gl.TEXTURE_MIN_FILTER, gl.TEXTURE_MAG_FILTER]) gl.texParameteri(gl.TEXTURE_2D_ARRAY, k, gl.LINEAR);
    for (const k of [gl.TEXTURE_WRAP_S, gl.TEXTURE_WRAP_T]) gl.texParameteri(gl.TEXTURE_2D_ARRAY, k, gl.CLAMP_TO_EDGE);
    this.canvas.addEventListener('webglcontextlost', () => {
      this.gl = null;
    });
  }

  get ok(): boolean {
    return !!this.gl;
  }

  /** The layer of the texture array that holds a tile (uploaded when it is new); −1 when every layer is in use by this frame. */
  private slot(t: TilePixels): number {
    const gl = this.gl!;
    let s = this.slotOf.get(t);
    if (s === undefined) {
      s = -1;
      let oldest = this.frameNo;
      for (let k = 0; k < LAYERS; k++) {
        if (!this.slotTile[k]) {
          s = k;
          break;
        }
        if (this.slotUsed[k]! < oldest) {
          oldest = this.slotUsed[k]!;
          s = k;
        }
      }
      if (s < 0) return -1;
      const old = this.slotTile[s];
      if (old) this.slotOf.delete(old);
      this.slotTile[s] = t;
      this.slotOf.set(t, s);
      gl.activeTexture(gl.TEXTURE1);
      gl.texSubImage3D(gl.TEXTURE_2D_ARRAY, 0, 0, 0, s, TILE, TILE, 1, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array(t.d.buffer, t.d.byteOffset, t.d.byteLength));
    }
    this.slotUsed[s] = this.frameNo;
    return s;
  }

  /** Paints one frame into `canvas`; null when this view is left to the CPU painter (see `why`). `painted` of `total` are counts of the coarse grid. */
  paint(job: GlJob): { painted: number; total: number } | null {
    const gl = this.gl;
    const K = job.samplers.length;
    this.why = !gl ? 'no WebGL2' : job.samplers.some((x) => x.order < 0) ? 'the whole-sky image' : '';
    if (!gl || this.why) return null;
    this.frameNo++;
    const f = this.f;
    // the exact places of a coarse grid of pixels, once for all the surveys
    const gw = Math.floor((job.sw + STEP - 1) / STEP) + 1;
    const gh = Math.floor((job.sh + STEP - 1) / STEP) + 1;
    if (this.gFace.length < gw * gh) {
      this.gFace = new Int8Array(gw * gh);
      this.gX = new Float64Array(gw * gh);
      this.gY = new Float64Array(gw * gh);
    }
    const gF = this.gFace;
    const gX = this.gX;
    const gY = this.gY;
    let total = 0;
    for (let b = 0; b < gh; b++) {
      for (let a = 0; a < gw; a++) {
        const ok = job.place(Math.min(a * STEP, job.sw - 1), Math.min(b * STEP, job.sh - 1), f);
        const q = b * gw + a;
        gF[q] = ok ? f.face : -1;
        gX[q] = f.x;
        gY[q] = f.y;
        if (ok) total++;
      }
    }
    const idx = this.index;
    idx.fill(0);
    let paintedMin = total;
    for (let k = 0; k < K; k++) {
      const sm = job.samplers[k]!;
      const n = sm.order;
      const N = 2 ** n;
      // the cells of this survey's tile grid the view touches, with how many grid points fell in each
      const hits = new Map<number, number>();
      const lo = new Int32Array(24).fill(1 << 30);
      const hi = new Int32Array(24).fill(-1);
      for (let q = 0; q < gw * gh; q++) {
        const face = gF[q]!;
        if (face < 0) continue;
        const tx = Math.min(N - 1, Math.floor(gX[q]! * N));
        const ty = Math.min(N - 1, Math.floor(gY[q]! * N));
        const key = (face * 16384 + tx) * 16384 + ty;
        hits.set(key, (hits.get(key) ?? 0) + 1);
        const a = face * 2;
        if (tx < lo[a]!) lo[a] = tx;
        if (ty < lo[a + 1]!) lo[a + 1] = ty;
        if (tx > hi[a]!) hi[a] = tx;
        if (ty > hi[a + 1]!) hi[a + 1] = ty;
      }
      let painted = 0;
      for (let face = 0; face < 12; face++) {
        const a = face * 2;
        const o = (k * 12 + face) * 2;
        if (hi[a]! < 0) {
          this.origin[o] = this.origin[o + 1] = 0;
          continue;
        }
        // one cell of margin round what the grid touched: a tile seen only between two grid points is painted from what is at hand (not asked for)
        const ox = Math.max(0, lo[a]! - 1);
        const oy = Math.max(0, lo[a + 1]! - 1);
        const ex = Math.min(N - 1, hi[a]! + 1);
        const ey = Math.min(N - 1, hi[a + 1]! + 1);
        if (ex - ox >= WIN || ey - oy >= WIN) {
          this.why = 'more tiles in sight than the index holds';
          return null;
        }
        this.origin[o] = ox;
        this.origin[o + 1] = oy;
        for (let ty = oy; ty <= ey; ty++) {
          for (let tx = ox; tx <= ex; tx++) {
            const count = hits.get((face * 16384 + tx) * 16384 + ty) ?? 0;
            let near = count > 0;
            for (let dy = -1; dy <= 1 && !near; dy++) for (let dx = -1; dx <= 1 && !near; dx++) near = hits.has((face * 16384 + tx + dx) * 16384 + ty + dy);
            if (!near) continue;
            const t = sm.resolve(face, tx, ty, count > 0);
            if (!t) continue;
            if (t.w !== TILE || t.h !== TILE) {
              this.why = `a tile of ${t.w} px`;
              return null;
            }
            const s = this.slot(t);
            if (s < 0) {
              this.why = 'more tiles in sight than the texture holds';
              return null;
            }
            const q = (((k * 12 + face) * WIN + (ty - oy)) * WIN + (tx - ox)) * 4;
            idx[q] = s + 1;
            idx[q + 1] = n - sm.resolvedOrder;
            idx[q + 2] = t.baked ? 255 : 0;
            idx[q + 3] = 255;
            painted += count;
          }
        }
      }
      paintedMin = Math.min(paintedMin, painted);
    }
    const painted = paintedMin;
    if (this.canvas.width !== job.sw || this.canvas.height !== job.sh) {
      this.canvas.width = job.sw;
      this.canvas.height = job.sh;
    }
    gl.viewport(0, 0, job.sw, job.sh);
    gl.activeTexture(gl.TEXTURE0);
    gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, WIN, WIN * 36, gl.RGBA, gl.UNSIGNED_BYTE, idx);
    gl.useProgram(this.prog);
    const u = this.u;
    gl.uniform2f(u['uSize']!, job.sw, job.sh);
    gl.uniform2f(u['uHalf']!, job.sw / 2, job.sh / 2);
    gl.uniform1f(u['uR']!, job.R);
    gl.uniform1i(u['uInside']!, job.inside ? 1 : 0);
    gl.uniform1i(u['uMoll']!, job.map ? 1 : 0);
    gl.uniform3f(u['uMap']!, job.map?.x ?? 0, job.map?.y ?? 0, job.map?.w ?? 1);
    gl.uniform1i(u['uChannels']!, K);
    gl.uniform1f(u['uRound']!, (globalThis as { __skyRound?: number }).__skyRound ?? ROUND);
    gl.uniform3f(u['uC']!, job.c[0]!, job.c[1]!, job.c[2]!);
    gl.uniform3f(u['uE']!, job.e[0]!, job.e[1]!, job.e[2]!);
    gl.uniform3f(u['uN']!, job.n[0]!, job.n[1]!, job.n[2]!);
    gl.uniform1i(u['uGal']!, job.gal ? 1 : 0);
    gl.uniform1iv(u['uOrder']!, [0, 1, 2].map((k) => job.samplers[k]?.order ?? 0));
    gl.uniform2iv(u['uOrigin']!, this.origin);
    gl.uniform4fv(u['uTone']!, [0, 1, 2].flatMap((k) => job.tones[k] ?? [0, 1, 0, 1]));
    gl.uniform1i(u['uIndex']!, 0);
    gl.uniform1i(u['uTiles']!, 1);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    return { painted, total };
  }
}
