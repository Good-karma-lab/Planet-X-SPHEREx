// WebGL2 renderer of the super-frames (docs/plan/05-architecture.md 5.9).
// Every stack of a field is an R16F texture array with a fixed number of layers per pass (picture: 3 colour channels; quick: 1 white
// layer; dets: 6 detectors; bands: 7 named bands); the saturated share is an R8 array (one layer per pass). Linear filtering of R16F is
// core in WebGL2. A view, band, panel or pass change is a uniform change, never a request.

import { HI, LO, SOFT } from './law';

const VS = `#version 300 es
in vec2 p;
out vec2 uv;
void main() { uv = vec2(0.5 * (p.x + 1.0), 0.5 * (1.0 - p.y)); gl_Position = vec4(p, 0.0, 1.0); }`;

// uv: x to the right (west), y down (south) — the grid's own orientation (row 0 north, column 0 east).
const FS = `#version 300 es
precision highp float;
precision highp sampler2DArray;
in vec2 uv;
out vec4 color;
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

float law(float x) { return clamp(asinh((x - uLo) / uSoft) / asinh((uHi - uLo) / uSoft), 0.0, 1.0); }

float val(int pass, int ch) { return texture(uVal, vec3(uv, float(pass * uPerPass + ch))).r; }

vec3 passColour(int k) {
  float sat = texture(uSat, vec3(uv, float(k))).r;
  if (sat > 0.5) return vec3(1.0);                         // a saturated core is white, never a hole
  if (uMode == 0) {
    float r = val(k, 0), g = val(k, 1), b = val(k, 2);
    if (isnan(r) || isnan(g) || isnan(b)) return uNoData;
    vec3 s = vec3(law(r * uWB[k].x), law(g), law(b * uWB[k].y));
    float l = dot(s, vec3(0.3333));
    float fade = 1.0 - smoothstep(0.55, 0.9, l);          // no colour boost on very bright pixels (wings of saturated cores)
    float faint = smoothstep(0.04, 0.16, l);               // nor at the noise level: noise keeps its natural (weak) colour
    return clamp(mix(vec3(l), s, 1.0 + (uBoost - 1.0) * fade * faint), 0.0, 1.0);
  }
  float w = val(k, uLayer);
  if (isnan(w)) return uNoData;
  float l = law(w);
  return clamp(l * mix(vec3(1.0), uTint, 0.35 * (1.0 - smoothstep(0.6, 0.95, l))), 0.0, 1.0);
}

void main() {
  vec3 a = passColour(uA);
  vec3 b = uT > 0.0 ? passColour(uB) : a;
  color = vec4(mix(a, b, uT), 1.0);
}`;

export type StackName = 'quick' | 'picture' | 'dets' | 'bands';

export const PER_PASS: Record<StackName, number> = { quick: 1, picture: 3, dets: 6, bands: 7 };

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
  mode: 'colour' | 'layer';
  layer?: number;
  tint?: [number, number, number];
  /** colour of places without data (default: the Picture's flat blue-grey) */
  noData?: [number, number, number];
}

export class Renderer {
  private gl: WebGL2RenderingContext;
  private prog: WebGLProgram;
  private stacks = new Map<StackName, Stack>();
  private sat: WebGLTexture | null = null;
  private satLoaded = new Set<number>();
  private n = 0;
  private passCount = 0;
  private wb: number[] = new Array(32).fill(1);
  private loc = new Map<string, WebGLUniformLocation | null>();
  boost = 2.4;

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
    this.n = n;
    this.passCount = passes;
    this.sat = this.newArray(gl.R8, passes);
    this.satLoaded.clear();
    this.wb.fill(1);
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
    const s = this.stacks.get(p.stack);
    if (!s || !this.sat) return;
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
    gl.bindTexture(gl.TEXTURE_2D_ARRAY, this.sat);
    gl.uniform1i(this.u('uSat'), 1);
    gl.uniform1i(this.u('uPerPass'), PER_PASS[p.stack]);
    gl.uniform1i(this.u('uMode'), p.mode === 'colour' ? 0 : 1);
    gl.uniform1i(this.u('uLayer'), p.layer ?? 0);
    gl.uniform3fv(this.u('uTint'), new Float32Array(p.tint ?? [1, 1, 1]));
    gl.uniform3fv(this.u('uNoData'), new Float32Array(p.noData ?? [0.36, 0.4, 0.44]));
    gl.uniform1i(this.u('uA'), a);
    gl.uniform1i(this.u('uB'), b);
    gl.uniform1f(this.u('uT'), t);
    gl.uniform2fv(this.u('uWB'), new Float32Array(this.wb));
    gl.uniform1f(this.u('uLo'), LO);
    gl.uniform1f(this.u('uHi'), HI);
    gl.uniform1f(this.u('uSoft'), SOFT);
    gl.uniform1f(this.u('uBoost'), this.boost);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }
}

/** Square panels laid out to fill a w×h area: the column count that makes the panels largest. */
export function wallLayout(count: number, w: number, h: number, gap = 6): { x: number; y: number; size: number }[] {
  let best = { cols: 1, size: 0 };
  for (let cols = 1; cols <= count; cols++) {
    const rows = Math.ceil(count / cols);
    const size = Math.floor(Math.min((w - gap * (cols - 1)) / cols, (h - gap * (rows - 1)) / rows));
    if (size > best.size) best = { cols, size };
  }
  const rows = Math.ceil(count / best.cols);
  const totalW = best.cols * best.size + gap * (best.cols - 1);
  const totalH = rows * best.size + gap * (rows - 1);
  const x0 = (w - totalW) / 2;
  const y0 = (h - totalH) / 2;
  return Array.from({ length: count }, (_, i) => ({
    x: x0 + (i % best.cols) * (best.size + gap),
    y: y0 + Math.floor(i / best.cols) * (best.size + gap),
    size: best.size,
  }));
}
