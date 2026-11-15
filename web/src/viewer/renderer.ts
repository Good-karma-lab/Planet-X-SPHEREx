// WebGL2 renderer of the super-frames (docs/plan/05-architecture.md 5.9).
// Every stack of a field is an R16F texture array with a fixed number of layers per pass (picture: 3 colour channels; quick: 1 white
// layer; dets: 6 detectors; bands: 7 named bands); the saturated share is an R8 array (one layer per pass). Linear filtering of R16F is
// core in WebGL2. A view, band, panel or pass change is a uniform change, never a request.

import { HI, LO, SOFT } from './law';

const VS = `#version 300 es
in vec2 p;
out vec2 vuv;
void main() { vuv = vec2(0.5 * (p.x + 1.0), 0.5 * (1.0 - p.y)); gl_Position = vec4(p, 0.0, 1.0); }`;

// uv: x to the right (west), y down (south) — the grid's own orientation (row 0 north, column 0 east).
const FS = `#version 300 es
precision highp float;
precision highp sampler2DArray;
in vec2 vuv;
out vec4 color;
vec2 uv;                       // the place in the field: the panel's own uv, zoomed by uZoom (centre xy, share of the field z)
uniform vec3 uZoom;
uniform int uFilm;             // the Days film: one layer per visit, no saturation layer, its own background and noise
uniform vec2 uSNF;
uniform sampler2DArray uVal;   // layer = pass * uPerPass + channel
uniform sampler2DArray uSat;   // layer = pass
uniform int uPerPass;
uniform int uMode;             // 0 colour (channels 0..2 of the pass), 1 one layer (uLayer), tinted by uTint
uniform int uLayer;
uniform vec3 uTint;
uniform int uA, uB;            // passes
uniform float uT;              // mix of B over A
uniform vec2 uWB[16];          // per pass (kR, kB)
uniform float uLo, uHi, uSoft, uBoost;
uniform vec3 uNoData;          // flat blue-grey inside the Picture; black in wall panels (outside coverage)
uniform int uTime;             // 0 pendulum, 1 Difference (pass against the first), 2 Co-add (all passes)
uniform int uPasses;           // passes loaded (Co-add)
uniform sampler2DArray uDiff;  // R8: 128 + 12.7 z, 0 = no data; layer = pass * uDiffPer + uDiffLayer
uniform int uDiffPer, uDiffLayer;
uniform int uHasDiff;
uniform int uBandMask;         // mode 2: bit k = band k on
uniform vec3 uHues[8];         // mode 2: colour of each band
uniform int uMono;             // mode 2: sum the chosen bands in grey instead of blending their colours
uniform int uWhiteCores;       // a saturated core is drawn white (1) or as no data (0)
uniform float uSharp;          // Sharpen amount (0 = off)
uniform vec2 uSN[16];          // per pass: background and noise of the pass's white light (mean of its layers)
uniform float uDP;             // one detector pixel in uv units
uniform vec4 uBBg[32];         // per pass and band (pass * 8 + band, four to a vec4): background of the band's picture
uniform vec4 uBSg[32];         // ... and its noise (0 = not measured)
uniform vec3 uPBg[16];         // per pass: background of the colour picture's three channels
uniform vec3 uPSg[16];         // ... and their noise (0 = not measured)
uniform int uNorm;             // mode 1 (one layer): measure it against its own background and noise (a tile of the bands or detectors wall)
uniform vec2 uBN;              // the colour blend's stretch: the noise level where the response turns from linear to logarithmic, and the level that is full scale (both in sigma)

// the colour blend of the bands: every band is measured against its OWN background and noise, so a source that stands 5 sigma above the noise in
// any band shows in that band's colour (a Y dwarf in D5-D6 is as visible as a star in D1), and noise in every band is equally dark and so neutral
float bandSN(int k, int j, float v) {
  int i = k * 8 + j;
  float sg = uBSg[i >> 2][i & 3];
  return sg > 0.0 ? (v - uBBg[i >> 2][i & 3]) / sg : -1e9;
}
float snLaw(float x) { x = max(x - 2.0, 0.0);   // the first 2 sigma are noise: dark
  return clamp(asinh(x / uBN.x) / asinh(uBN.y / uBN.x), 0.0, 1.0); }

float law(float x) { return clamp(asinh((x - uLo) / uSoft) / asinh((uHi - uLo) / uSoft), 0.0, 1.0); }

int satK(int k) { return uFilm == 1 ? 0 : k; }
float G = 1.0;                 // Sharpen gain of the place, the same for every layer and channel (so it never changes a colour)
// the Days film is a coarse grid shown many times larger: bilinear turns a star into a diamond, so its layers are read with a cubic B-spline (four bilinear taps)
float rawSmooth(float layer, vec2 q) {
  vec2 sz = vec2(textureSize(uVal, 0).xy);
  vec2 p = q * sz - 0.5;
  vec2 i = floor(p);
  vec2 f = p - i;
  vec2 w0 = (1.0 - f) * (1.0 - f) * (1.0 - f) / 6.0;
  vec2 w1 = (4.0 - 6.0 * f * f + 3.0 * f * f * f) / 6.0;
  vec2 w2 = (1.0 + 3.0 * f + 3.0 * f * f - 3.0 * f * f * f) / 6.0;
  vec2 w3 = f * f * f / 6.0;
  vec2 g0 = w0 + w1;
  vec2 g1 = w2 + w3;
  vec2 h0 = (i - 0.5 + w1 / g0) / sz;
  vec2 h1 = (i + 1.5 + w3 / g1) / sz;
  float a = texture(uVal, vec3(h0.x, h0.y, layer)).r;
  float b = texture(uVal, vec3(h1.x, h0.y, layer)).r;
  float c = texture(uVal, vec3(h0.x, h1.y, layer)).r;
  float d = texture(uVal, vec3(h1.x, h1.y, layer)).r;
  return g0.y * (g0.x * a + g1.x * b) + g1.y * (g0.x * c + g1.x * d);
}
float raw(int pass, int ch, vec2 q) {
  float layer = float(pass * uPerPass + ch);
  return uFilm == 1 ? rawSmooth(layer, q) : texture(uVal, vec3(q, layer)).r;
}
vec2 cq;                       // the place the colour is taken at (the pixel, or a neighbour of a saturated core)
float val(int pass, int ch) { return raw(pass, ch, cq) * G; }

// white light at a place: the mean of the pass's layers (colour channels, bands or detectors)
float white(int k, vec2 q) {
  float s = 0.0; int n = 0;
  for (int j = 0; j < 8; j++) { if (j >= uPerPass) break; float v = raw(k, j, q); if (!isnan(v)) { s += v; n++; } }
  return n > 0 ? s / float(n) : -1e9;
}

// Sharpen: boost only (gain >= 1), where the white light stands 3-8 sigma above the pass's noise, fading to nothing near saturated
// pixels and towards the bright end; the gain grows with the local contrast against a ring at half and one detector pixel.
float sharpGain(int k) {
  if (uSharp <= 0.0) return 1.0;
  float L0 = white(k, uv);
  vec2 sn = uFilm == 1 ? uSNF : uSN[k];
  float ws = smoothstep(3.0, 8.0, (L0 - sn.x) / max(sn.y, 1e-9));
  if (ws <= 0.0 || L0 <= 0.0) return 1.0;
  float sb = 0.2 * L0, wt = 0.2, near = 0.0;
  for (int j = 0; j < 6; j++) {
    float an = float(j) * 1.0471976;
    vec2 d = vec2(cos(an), sin(an)) * uDP;
    vec2 q1 = uv + 0.5 * d, q2 = uv + d;
    near = max(near, max(texture(uSat, vec3(q1, float(satK(k)))).r, texture(uSat, vec3(q2, float(satK(k)))).r));
    float a1 = white(k, q1), a2 = white(k, q2);
    if (a1 > -1e8) { sb += 0.4 / 6.0 * a1; wt += 0.4 / 6.0; }
    if (a2 > -1e8) { sb += 0.4 / 6.0 * a2; wt += 0.4 / 6.0; }
  }
  float fd = 1.0 - smoothstep(0.2, 0.6, near);
  float head = 1.0 - smoothstep(0.12, 0.4, law(L0));
  float t = max(L0 - sb / wt, 0.0) / L0;
  return 1.0 + ws * fd * head * uSharp * t * t / (t + 0.1);
}

// the colour of the pass at the place cq (the rgb and 1.0, or 0.0 where the pass has no data there)
vec4 colourOf(int k) {
  if (uMode == 0) {
    float r = val(k, 0), g = val(k, 1), b = val(k, 2);
    if (isnan(r) || isnan(g) || isnan(b)) return vec4(0.0);
    vec3 s;
    vec3 sg = uPSg[k];
    if (sg.y > 0.0) {
      // against the noise of the picture itself: the channels are balanced on the field's stars (as before) and measured in units of the one noise they share,
      // so a source that stands 5 sigma above it shows in the colour of its channels (a Y dwarf in D5-D6 as well as a star in D1) and noise stays dark and neutral
      float ref = pow(max(sg.x * uWB[k].x, 1e-9) * max(sg.y, 1e-9) * max(sg.z * uWB[k].y, 1e-9), 1.0 / 3.0);
      vec3 bg = uPBg[k];
      s = vec3(snLaw((r - bg.x) * uWB[k].x / ref), snLaw((g - bg.y) / ref), snLaw((b - bg.z) * uWB[k].y / ref));
      // bright stars and the halos round them keep the fixed stretch (their colour is the star's, not the faint sources' to bring out): from 150 sigma over the noise
      // the old law takes over, completely at 1500 sigma
      float hot = smoothstep(150.0, 1500.0, (g - bg.y) / max(sg.y, 1e-9));
      if (hot > 0.0) s = mix(s, vec3(law(r * uWB[k].x), law(g), law(b * uWB[k].y)), hot);
    } else {
      s = vec3(law(r * uWB[k].x), law(g), law(b * uWB[k].y));
    }
    float l = dot(s, vec3(0.3333));
    float fade = 1.0 - smoothstep(0.55, 0.9, l);          // no colour boost on very bright pixels (wings of saturated cores)
    float faint = smoothstep(0.04, 0.16, l);               // nor at the noise level: noise keeps its natural (weak) colour
    return vec4(clamp(mix(vec3(l), s, 1.0 + (uBoost - 1.0) * fade * faint), 0.0, 1.0), 1.0);
  }
  if (uMode == 2) {
    vec3 acc = vec3(0.0);
    float sum = 0.0; int n = 0;
    for (int j = 0; j < 8; j++) {
      if (j >= uPerPass) break;
      if ((uBandMask & (1 << j)) == 0) continue;
      float v = val(k, j);
      if (isnan(v)) continue;
      float sn = bandSN(k, j, v);
      float l = sn > -1e8 ? snLaw(sn) : law(v);
      acc = 1.0 - (1.0 - acc) * (1.0 - l * uHues[j]);   // screen blend of the bands' colours
      sum += v; n++;
    }
    if (n == 0) return vec4(0.0);
    if (uMono == 1) return vec4(vec3(law(sum / float(n))), 1.0);
    return vec4(clamp(acc, 0.0, 1.0), 1.0);
  }
  float w = val(k, uLayer);
  if (isnan(w)) return vec4(0.0);
  float sn = uNorm == 1 ? bandSN(k, uLayer, w) : -1e9;
  float l = sn > -1e8 ? snLaw(sn) : law(w);
  return vec4(clamp(l * mix(vec3(1.0), uTint, 0.35 * (1.0 - smoothstep(0.6, 0.95, l))), 0.0, 1.0), 1.0);
}

// a saturated core with White cores off: never a hole. The colour of the star around it, taken from the unsaturated pixels in rings of 3, 6, 9 and 12
// detector pixels, brought to full scale (its brightest channel at 1), so the core is the brightest value of the star's own colour
vec3 satCore(int k) {
  vec3 acc = vec3(0.0);
  float w = 0.0;
  G = 1.0;
  for (int r = 1; r <= 4; r++) {
    for (int j = 0; j < 12; j++) {
      float an = float(j) * 0.5235988;
      vec2 q = uv + vec2(cos(an), sin(an)) * uDP * float(r) * 3.0;
      if (texture(uSat, vec3(q, float(satK(k)))).r > 0.5) continue;
      cq = q;
      vec4 c = colourOf(k);
      if (c.a > 0.5) { acc += c.rgb; w += 1.0; }
    }
  }
  cq = uv;
  if (w > 0.0) {
    vec3 c = acc / w;
    float m = max(c.r, max(c.g, c.b));
    if (m > 1e-4) return c / m;
  }
  return vec3(0.9);
}

vec3 passColour(int k) {
  cq = uv;
  float sat = max(texture(uSat, vec3(uv, float(satK(k)))).r, isnan(raw(k, 0, uv)) ? 1.0 : 0.0);
  // the mask of saturated pixels is a grid of detector pixels, several picture pixels wide: read hard it makes a square. A saturated core is a smooth disc:
  // the mask is blurred over about a detector pixel and the core fades in over its edge
  float sm = sat;
  if (uDP > 0.0) {
    float a = 0.0;
    float w = 0.0;
    for (int j = -2; j <= 2; j++)
      for (int i = -2; i <= 2; i++) {
        vec2 o = vec2(float(i), float(j)) * 0.5;
        float g = exp(-dot(o, o) * 1.1);
        vec2 q = uv + o * uDP;
        // a saturated pixel, or one with no data (the detector's overflow leaves holes round a core): both are part of the core
        a += g * max(texture(uSat, vec3(q, float(satK(k)))).r, isnan(raw(k, 0, q)) ? 1.0 : 0.0);
        w += g;
      }
    sm = a / w;
  }
  float core = smoothstep(0.22, 0.58, sm);
  if (core >= 1.0) return uWhiteCores == 1 ? vec3(1.0) : satCore(k);  // a saturated core is white or the star's colour at full scale, never a hole
  G = sharpGain(k);
  vec4 c = colourOf(k);
  vec3 base = c.a > 0.5 ? c.rgb : (core > 0.2 ? vec3(1.0) : uNoData);   // no data inside the fade of a core is part of the core, not a black pixel
  if (core <= 0.0) return base;
  return mix(base, uWhiteCores == 1 ? vec3(1.0) : satCore(k), core);
}

vec3 diffColour(int k) {
  vec3 base = vec3(dot(passColour(k), vec3(0.3333)) * 0.5);  // the pass, dimmed and grey, under the significance
  if (k == 0 || uHasDiff == 0) return base;                    // the first pass is the reference and shows nothing
  float e = texture(uDiff, vec3(uv, float(k * uDiffPer + uDiffLayer))).r * 255.0;
  if (e < 0.5) return base;
  float z = (e - 128.0) / 12.7;
  float a = clamp((abs(z) - 4.0) / 6.0, 0.0, 1.0) * step(4.0, abs(z));
  // a real change is large against the light that is there: a star whose wings shift by a fraction of a pixel between passes
  // (registration, a different point-spread function) leaves significant but small residual rings, which are not shown
  float ak = uMode == 1 ? raw(k, uLayer, uv) : white(k, uv);
  float a0 = uMode == 1 ? raw(0, uLayer, uv) : white(0, uv);
  // against the brightest light within a detector pixel and a half (in either pass): a moving source changes by about its whole
  // peak; a static star whose profile differs slightly between passes changes by a small part of its peak
  float peak = max(abs(ak), abs(a0));
  for (int j = 0; j < 8; j++) {
    float an = float(j) * 0.7853982;
    vec2 q = uv + vec2(cos(an), sin(an)) * uDP * 1.5;
    float bk = uMode == 1 ? raw(k, uLayer, q) : white(k, q);
    float b0 = uMode == 1 ? raw(0, uLayer, q) : white(0, q);
    if (!isnan(bk)) peak = max(peak, bk);
    if (!isnan(b0)) peak = max(peak, b0);
  }
  float frac = abs(ak - a0) / max(peak, 1e-6);
  a *= smoothstep(0.3, 0.5, frac);
  vec3 c = z > 0.0 ? vec3(1.0, 0.15, 0.85) : vec3(0.1, 0.9, 1.0);  // magenta brighter, cyan fainter
  return mix(base, c, a > 0.0 ? 0.35 + 0.65 * a : 0.0);
}

void main() {
  uv = uZoom.xy + (vuv - 0.5) * uZoom.z;
  if (uv.x < 0.0 || uv.y < 0.0 || uv.x > 1.0 || uv.y > 1.0) { color = vec4(0.0, 0.0, 0.0, 1.0); return; }  // beyond the field: black
  if (uTime == 2) {
    vec3 acc = vec3(0.0);
    for (int k = 0; k < 16; k++) { if (k >= uPasses) break; acc += passColour(k); }
    color = vec4(acc / float(max(uPasses, 1)), 1.0);
    return;
  }
  if (uTime == 1) {
    vec3 a = diffColour(uA);
    vec3 b = uT > 0.0 ? diffColour(uB) : a;
    color = vec4(mix(a, b, uT), 1.0);
    return;
  }
  vec3 a = passColour(uA);
  vec3 b = uT > 0.0 ? passColour(uB) : a;
  color = vec4(mix(a, b, uT), 1.0);
}`;

export type StackName = 'quick' | 'picture' | 'dets' | 'bands' | 'film';

export const PER_PASS: Record<StackName, number> = { quick: 1, picture: 3, dets: 6, bands: 7, film: 1 };

interface Stack {
  tex: WebGLTexture;
  loaded: Set<number>;
}

export interface Panel {
  /** CSS-pixel rectangle inside the canvas (x, y from the top-left) */
  x: number;
  y: number;
  w: number;
  h: number;
  stack: StackName;
  mode: 'colour' | 'layer' | 'blend';
  /** mode blend: which bands (bit k) and their colours; mono sums them in grey */
  bandMask?: number;
  hues?: [number, number, number][];
  mono?: boolean;
  layer?: number;
  tint?: [number, number, number];
  /** colour of places without data (default: the Picture's flat blue-grey) */
  noData?: [number, number, number];
  time?: 'pendulum' | 'difference' | 'coadd';
  /** layer of the Difference maps within a pass (0 white, 1.. the bands) */
  diffLayer?: number;
  /** a zoom on part of the field: centre (uv, 0..1) and the share of the field shown */
  zoom?: { cx: number; cy: number; s: number };
}

export class Renderer {
  private gl: WebGL2RenderingContext;
  private prog: WebGLProgram;
  private stacks = new Map<StackName, Stack>();
  private sat: WebGLTexture | null = null;
  private satLoaded = new Set<number>();
  private diff: WebGLTexture | null = null;
  private diffPer = 1;
  private n = 0;
  private passCount = 0;
  private wb: number[] = new Array(32).fill(1);
  private loc = new Map<string, WebGLUniformLocation | null>();
  boost = 2.4;
  whiteCores = true;
  hi = HI;
  sharp = 16;
  private sn = new Float32Array(32);
  private noiseOf = new Map<StackName, { bg: Float32Array; sg: Float32Array }>();
  private picBg = new Float32Array(48);
  private picSg = new Float32Array(48);
  private dp = 0.01;
  soft = SOFT;

  constructor(private canvas: HTMLCanvasElement) {
    const gl = canvas.getContext('webgl2', { antialias: false, preserveDrawingBuffer: true });
    if (!gl) throw new Error('WebGL2 is not available in this browser');
    this.gl = gl;
    this.prog = this.program(VS, FS);
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(this.prog, 'p');
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  }

  private program(vs: string, fs: string): WebGLProgram {
    const gl = this.gl;
    const sh = (type: number, src: string) => {
      const s = gl.createShader(type)!;
      gl.shaderSource(s, src);
      gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) ?? 'shader');
      return s;
    };
    const p = gl.createProgram()!;
    gl.attachShader(p, sh(gl.VERTEX_SHADER, vs));
    gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fs));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p) ?? 'link');
    return p;
  }

  private u(name: string): WebGLUniformLocation | null {
    if (!this.loc.has(name)) this.loc.set(name, this.gl.getUniformLocation(this.prog, name));
    return this.loc.get(name)!;
  }

  private newArray(format: number, layers: number): WebGLTexture {
    const gl = this.gl;
    const t = gl.createTexture()!;
    gl.bindTexture(gl.TEXTURE_2D_ARRAY, t);
    gl.texStorage3D(gl.TEXTURE_2D_ARRAY, 1, format, this.n, this.n, layers);
    gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return t;
  }

  /** A new field: n×n, `passes` passes. Everything of the previous field is released. */
  setup(n: number, passes: number): void {
    const gl = this.gl;
    for (const s of this.stacks.values()) gl.deleteTexture(s.tex);
    this.stacks.clear();
    if (this.sat) gl.deleteTexture(this.sat);
    if (this.diff) gl.deleteTexture(this.diff);
    this.diff = null;
    this.n = n;
    this.passCount = passes;
    this.sat = this.newArray(gl.R8, passes);
    this.satLoaded.clear();
    this.wb.fill(1);
    this.dp = this.dpGrid / n;
  }

  get size(): number {
    return this.n;
  }

  get passes(): number {
    return this.passCount;
  }

  has(stack: StackName, pass: number): boolean {
    return this.stacks.get(stack)?.loaded.has(pass) ?? false;
  }

  loaded(stack: StackName): number[] {
    return [...(this.stacks.get(stack)?.loaded ?? [])].sort((a, b) => a - b);
  }

  /** Upload the layers of one pass of a stack (float16 bits, PER_PASS[stack] layers of n×n). */
  upload(stack: StackName, pass: number, values: Uint16Array): void {
    const gl = this.gl;
    let s = this.stacks.get(stack);
    if (!s) {
      s = { tex: this.newArray(gl.R16F, this.passCount * PER_PASS[stack]), loaded: new Set() };
      this.stacks.set(stack, s);
    }
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.bindTexture(gl.TEXTURE_2D_ARRAY, s.tex);
    gl.texSubImage3D(gl.TEXTURE_2D_ARRAY, 0, 0, 0, pass * PER_PASS[stack], this.n, this.n, PER_PASS[stack], gl.RED, gl.HALF_FLOAT, values);
    s.loaded.add(pass);
  }

  uploadSat(pass: number, sat: Uint8Array): void {
    const gl = this.gl;
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.bindTexture(gl.TEXTURE_2D_ARRAY, this.sat);
    gl.texSubImage3D(gl.TEXTURE_2D_ARRAY, 0, 0, 0, pass, this.n, this.n, 1, gl.RED, gl.UNSIGNED_BYTE, sat);
    this.satLoaded.add(pass);
  }

  /** The Difference maps: `layers[pass][k]` uint8 n×n (k 0 white, 1.. bands); pass 0 is the reference (no map). */
  uploadDiff(per: number, maps: Map<string, Uint8Array>, names: string[]): void {
    const gl = this.gl;
    if (this.diff) gl.deleteTexture(this.diff);
    this.diffPer = per;
    this.diff = this.newArray(gl.R8, this.passCount * per);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.bindTexture(gl.TEXTURE_2D_ARRAY, this.diff);
    for (let p = 1; p < this.passCount; p++) {
      names.forEach((name, k) => {
        const m = maps.get(`p${p}_${name}`);
        if (m) gl.texSubImage3D(gl.TEXTURE_2D_ARRAY, 0, 0, 0, p * per + k, this.n, this.n, 1, gl.RED, gl.UNSIGNED_BYTE, m);
      });
    }
  }

  get hasDiff(): boolean {
    return this.diff !== null;
  }

  /** Background and noise of a pass's white light (for Sharpen), and the size of one detector pixel in grid pixels. */
  setNoise(pass: number, background: number, sigma: number): void {
    this.sn[2 * pass] = background;
    this.sn[2 * pass + 1] = sigma;
  }

  /** Background and noise of each band's picture in a pass (0 noise = not measured), for the colour blend. */
  setBandNoise(pass: number, bg: number[], sigma: number[], stack: StackName = 'bands'): void {
    let n = this.noiseOf.get(stack);
    if (!n) {
      n = { bg: new Float32Array(128), sg: new Float32Array(128) };
      this.noiseOf.set(stack, n);
    }
    for (let j = 0; j < 8; j++) {
      n.bg[pass * 8 + j] = bg[j] ?? 0;
      n.sg[pass * 8 + j] = sigma[j] ?? 0;
    }
  }

  /** Background and noise of the three channels of a pass's colour picture. */
  setPictureNoise(pass: number, bg: number[], sigma: number[]): void {
    for (let c = 0; c < 3; c++) {
      this.picBg[pass * 3 + c] = bg[c] ?? 0;
      this.picSg[pass * 3 + c] = sigma[c] ?? 0;
    }
  }

  setDetectorPixel(gridPixels: number): void {
    this.dp = this.n ? gridPixels / this.n : 0.01;
    this.dpGrid = gridPixels;
  }

  private dpGrid = 4;

  // ---- the Days film: its own size and texture, kept apart from the field's stacks so switching scales needs no reload ----
  private film: { tex: WebGLTexture; n: number; count: number; loaded: Set<number> } | null = null;
  private filmSat: WebGLTexture | null = null;
  private filmSN: [number, number] = [0, 1];
  private filmDP = 0.01;

  /** A new film: `count` visits of n×n. */
  setupFilm(n: number, count: number, detectorPixels: number): void {
    const gl = this.gl;
    if (this.film) gl.deleteTexture(this.film.tex);
    const keep = this.n;
    this.n = n;
    const tex = this.newArray(gl.R16F, count);
    if (!this.filmSat) {
      this.filmSat = this.newArray(gl.R8, 1);
      gl.texSubImage3D(gl.TEXTURE_2D_ARRAY, 0, 0, 0, 0, n, n, 1, gl.RED, gl.UNSIGNED_BYTE, new Uint8Array(n * n));
    }
    this.n = keep;
    this.film = { tex, n, count, loaded: new Set() };
    this.filmDP = detectorPixels / n;
  }

  /** One visit of the film: physical values (NaN = no data). */
  uploadFilm(k: number, values: Float32Array): void {
    const gl = this.gl;
    const f = this.film;
    if (!f) return;
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.bindTexture(gl.TEXTURE_2D_ARRAY, f.tex);
    gl.texSubImage3D(gl.TEXTURE_2D_ARRAY, 0, 0, 0, k, f.n, f.n, 1, gl.RED, gl.FLOAT, values);
    f.loaded.add(k);
  }

  setFilmNoise(background: number, sigma: number): void {
    this.filmSN = [background, sigma];
  }

  get filmVisits(): number {
    return this.film?.loaded.size ?? 0;
  }

  clearFilm(): void {
    if (this.film) this.gl.deleteTexture(this.film.tex);
    if (this.filmSat) this.gl.deleteTexture(this.filmSat);
    this.film = null;
    this.filmSat = null;
  }

  setWhiteBalance(pass: number, kr: number, kb: number): void {
    this.wb[2 * pass] = kr;
    this.wb[2 * pass + 1] = kb;
  }

  /** Size the drawing buffer to the canvas's CSS size and clear it. */
  begin(): { w: number; h: number } {
    const gl = this.gl;
    const w = Math.max(1, Math.round(this.canvas.clientWidth * devicePixelRatio));
    const h = Math.max(1, Math.round(this.canvas.clientHeight * devicePixelRatio));
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w;
      this.canvas.height = h;
    }
    gl.disable(gl.SCISSOR_TEST);
    gl.viewport(0, 0, w, h);
    gl.clearColor(0, 0, 0, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    return { w: this.canvas.clientWidth, h: this.canvas.clientHeight };
  }

  /** Draw one panel (a pass or the crossfade of two) into its rectangle. */
  panel(p: Panel, a: number, b: number, t: number): void {
    const gl = this.gl;
    const isFilm = p.stack === 'film';
    const s = isFilm ? this.film : this.stacks.get(p.stack);
    const sat = isFilm ? this.filmSat : this.sat;
    if (!s || !sat) return;
    const r = devicePixelRatio;
    const x = Math.round(p.x * r);
    const w = Math.round(p.w * r);
    const h = Math.round(p.h * r);
    const y = this.canvas.height - Math.round(p.y * r) - h;
    gl.enable(gl.SCISSOR_TEST);
    gl.scissor(x, y, w, h);
    gl.viewport(x, y, w, h);
    gl.useProgram(this.prog);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D_ARRAY, s.tex);
    gl.uniform1i(this.u('uVal'), 0);
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D_ARRAY, sat);
    gl.uniform1i(this.u('uSat'), 1);
    gl.uniform1i(this.u('uFilm'), isFilm ? 1 : 0);
    gl.uniform2f(this.u('uSNF'), this.filmSN[0], this.filmSN[1]);
    const z = p.zoom ?? { cx: 0.5, cy: 0.5, s: 1 };
    gl.uniform3f(this.u('uZoom'), z.cx, z.cy, z.s);
    gl.uniform1i(this.u('uPerPass'), PER_PASS[p.stack]);
    gl.uniform1i(this.u('uMode'), p.mode === 'colour' ? 0 : p.mode === 'blend' ? 2 : 1);
    gl.uniform1i(this.u('uBandMask'), p.bandMask ?? 0);
    const hues = new Float32Array(24).fill(1);
    (p.hues ?? []).forEach((h, i) => hues.set(h, 3 * i));
    gl.uniform3fv(this.u('uHues'), hues);
    gl.uniform1i(this.u('uMono'), p.mono ? 1 : 0);
    gl.uniform1i(this.u('uWhiteCores'), this.whiteCores ? 1 : 0);
    gl.uniform1f(this.u('uSharp'), this.sharp);
    gl.uniform2fv(this.u('uSN'), this.sn);
    const nz = this.noiseOf.get(p.stack) ?? this.noiseOf.get('bands');
    gl.uniform4fv(this.u('uBBg'), nz ? nz.bg : new Float32Array(128));
    gl.uniform4fv(this.u('uBSg'), nz ? nz.sg : new Float32Array(128));
    gl.uniform1i(this.u('uNorm'), p.stack === 'bands' || p.stack === 'dets' ? 1 : 0);
    gl.uniform3fv(this.u('uPBg'), this.picBg);
    gl.uniform3fv(this.u('uPSg'), this.picSg);
    // the stretch slider moves the knee (4 sigma at its default 0.4), the Trim menu the full-scale level (20000 sigma at its default of 400)
    gl.uniform2f(this.u('uBN'), (4 * this.soft) / SOFT, (20000 * this.hi) / HI);
    gl.uniform1f(this.u('uDP'), isFilm ? this.filmDP : this.dp);
    gl.uniform1i(this.u('uLayer'), p.layer ?? 0);
    gl.uniform3fv(this.u('uTint'), new Float32Array(p.tint ?? [1, 1, 1]));
    gl.uniform3fv(this.u('uNoData'), new Float32Array(p.noData ?? [0, 0, 0]));
    gl.uniform1i(this.u('uA'), a);
    gl.uniform1i(this.u('uB'), b);
    gl.uniform1f(this.u('uT'), t);
    gl.uniform2fv(this.u('uWB'), new Float32Array(this.wb));
    gl.uniform1f(this.u('uLo'), LO);
    gl.uniform1f(this.u('uHi'), this.hi);
    gl.uniform1f(this.u('uSoft'), this.soft);
    gl.uniform1f(this.u('uBoost'), this.boost);
    gl.uniform1i(this.u('uTime'), p.time === 'difference' ? 1 : p.time === 'coadd' ? 2 : 0);
    gl.uniform1i(this.u('uPasses'), s.loaded.size);
    gl.activeTexture(gl.TEXTURE2);
    gl.bindTexture(gl.TEXTURE_2D_ARRAY, (isFilm ? null : this.diff) ?? sat);
    gl.uniform1i(this.u('uDiff'), 2);
    gl.uniform1i(this.u('uHasDiff'), this.diff && !isFilm ? 1 : 0);
    gl.uniform1i(this.u('uDiffPer'), this.diffPer);
    gl.uniform1i(this.u('uDiffLayer'), p.diffLayer ?? 0);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }
}

/** Square panels laid out to fill a w×h area: the column count that makes the panels largest (on a tie, more columns: a wide
 *  picture area takes the six detectors as 3 × 2); a short last row is centred. */
export function wallLayout(count: number, w: number, h: number, gap = 8): { x: number; y: number; size: number }[] {
  let best = { cols: 1, size: 0 };
  for (let cols = 1; cols <= count; cols++) {
    const rows = Math.ceil(count / cols);
    const size = Math.floor(Math.min((w - gap * (cols - 1)) / cols, (h - gap * (rows - 1)) / rows));
    if (size >= best.size) best = { cols, size };
  }
  const rows = Math.ceil(count / best.cols);
  const totalW = best.cols * best.size + gap * (best.cols - 1);
  const totalH = rows * best.size + gap * (rows - 1);
  const x0 = (w - totalW) / 2;
  const y0 = (h - totalH) / 2;
  return Array.from({ length: count }, (_, i) => {
    const row = Math.floor(i / best.cols);
    const inRow = row === rows - 1 ? count - row * best.cols : best.cols;
    const shift = ((best.cols - inRow) * (best.size + gap)) / 2;
    return { x: x0 + shift + (i % best.cols) * (best.size + gap), y: y0 + row * (best.size + gap), size: best.size };
  });
}
