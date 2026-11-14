// WebGL2 renderer of the super-frames (docs/plan/05-architecture.md 5.9).
// Stacks are R16F texture arrays (linear filtering of R16F is core in WebGL2), one layer per (pass, channel); the saturated share is an
// R8 array. A view, band or pass change is a uniform change, never a request.

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
uniform sampler2DArray uVal;   // layers: pass*3 + channel (colour) or pass (white)
uniform sampler2DArray uSat;   // layer: pass
uniform int uMode;             // 0 colour (3 channels per pass), 1 white (1 channel per pass)
uniform int uA, uB;            // passes
uniform float uT;              // mix of B over A
uniform vec2 uWB[16];          // per pass (kR, kB)
uniform float uLo, uHi, uSoft, uBoost;
const vec3 NODATA = vec3(0.36, 0.40, 0.44);

float law(float x) { return clamp(asinh((x - uLo) / uSoft) / asinh((uHi - uLo) / uSoft), 0.0, 1.0); }

vec4 passColour(int k) {
  float sat = texture(uSat, vec3(uv, float(k))).r;
  if (sat > 0.5) return vec4(1.0, 1.0, 1.0, 1.0);
  vec3 c;
  if (uMode == 0) {
    float r = texture(uVal, vec3(uv, float(3 * k))).r;
    float g = texture(uVal, vec3(uv, float(3 * k + 1))).r;
    float b = texture(uVal, vec3(uv, float(3 * k + 2))).r;
    if (isnan(r) || isnan(g) || isnan(b)) return vec4(NODATA, 0.0);
    vec3 lin = vec3(r * uWB[k].x, g, b * uWB[k].y);
    vec3 s = vec3(law(lin.r), law(lin.g), law(lin.b));
    float l = dot(s, vec3(0.3333));
    float fade = 1.0 - smoothstep(0.55, 0.9, l);          // no colour boost on very bright pixels (wings of saturated cores)
    float faint = smoothstep(0.04, 0.16, l);               // nor at the noise level: noise keeps its natural (weak) colour
    c = clamp(mix(vec3(l), s, 1.0 + (uBoost - 1.0) * fade * faint), 0.0, 1.0);
  } else {
    float w = texture(uVal, vec3(uv, float(k))).r;
    if (isnan(w)) return vec4(NODATA, 0.0);
    c = vec3(law(w));
  }
  return vec4(c, 1.0);
}

void main() {
  vec4 a = passColour(uA);
  vec4 b = uT > 0.0 ? passColour(uB) : a;
  color = vec4(mix(a.rgb, b.rgb, uT), 1.0);
}`;

export interface PassLayers {
  /** float16 bits, size n×n per channel, channels concatenated */
  values: Uint16Array;
  /** uint8 saturated share × 255 */
  sat: Uint8Array;
  wb?: [number, number];
}

export class Renderer {
  private gl: WebGL2RenderingContext;
  private prog: WebGLProgram;
  private val: WebGLTexture | null = null;
  private sat: WebGLTexture | null = null;
  private n = 0;
  private passCount = 0;
  private channels = 3;
  private loaded = new Set<number>();
  private wb: number[] = new Array(32).fill(1);
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

  /** Allocate for a field of `passes` passes, n×n, colour (3 channels) or white (1 channel). Keeps nothing of the previous field. */
  setup(n: number, passes: number, channels: 1 | 3): void {
    const gl = this.gl;
    this.n = n;
    this.passCount = passes;
    this.channels = channels;
    this.loaded.clear();
    for (const t of [this.val, this.sat]) if (t) gl.deleteTexture(t);
    this.val = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D_ARRAY, this.val);
    gl.texStorage3D(gl.TEXTURE_2D_ARRAY, 1, gl.R16F, n, n, passes * channels);
    this.params();
    this.sat = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D_ARRAY, this.sat);
    gl.texStorage3D(gl.TEXTURE_2D_ARRAY, 1, gl.R8, n, n, passes);
    this.params();
  }

  private params(): void {
    const gl = this.gl;
    gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  }

  /** Upload one pass (its channels and saturated share). */
  upload(pass: number, layers: PassLayers): void {
    const gl = this.gl;
    const n = this.n;
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.bindTexture(gl.TEXTURE_2D_ARRAY, this.val);
    gl.texSubImage3D(gl.TEXTURE_2D_ARRAY, 0, 0, 0, pass * this.channels, n, n, this.channels, gl.RED, gl.HALF_FLOAT, layers.values);
    gl.bindTexture(gl.TEXTURE_2D_ARRAY, this.sat);
    gl.texSubImage3D(gl.TEXTURE_2D_ARRAY, 0, 0, 0, pass, n, n, 1, gl.RED, gl.UNSIGNED_BYTE, layers.sat);
    if (layers.wb) {
      this.wb[2 * pass] = layers.wb[0];
      this.wb[2 * pass + 1] = layers.wb[1];
    }
    this.loaded.add(pass);
  }

  get passes(): number {
    return this.passCount;
  }

  get passesLoaded(): number[] {
    return [...this.loaded].sort((a, b) => a - b);
  }

  draw(a: number, b: number, t: number): void {
    const gl = this.gl;
    const size = Math.round(this.canvas.clientWidth * devicePixelRatio);
    if (this.canvas.width !== size || this.canvas.height !== size) {
      this.canvas.width = size;
      this.canvas.height = size;
    }
    gl.viewport(0, 0, size, size);
    gl.useProgram(this.prog);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D_ARRAY, this.val);
    gl.uniform1i(gl.getUniformLocation(this.prog, 'uVal'), 0);
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D_ARRAY, this.sat);
    gl.uniform1i(gl.getUniformLocation(this.prog, 'uSat'), 1);
    gl.uniform1i(gl.getUniformLocation(this.prog, 'uMode'), this.channels === 3 ? 0 : 1);
    gl.uniform1i(gl.getUniformLocation(this.prog, 'uA'), a);
    gl.uniform1i(gl.getUniformLocation(this.prog, 'uB'), b);
    gl.uniform1f(gl.getUniformLocation(this.prog, 'uT'), t);
    gl.uniform2fv(gl.getUniformLocation(this.prog, 'uWB'), new Float32Array(this.wb));
    gl.uniform1f(gl.getUniformLocation(this.prog, 'uLo'), LO);
    gl.uniform1f(gl.getUniformLocation(this.prog, 'uHi'), HI);
    gl.uniform1f(gl.getUniformLocation(this.prog, 'uSoft'), SOFT);
    gl.uniform1f(gl.getUniformLocation(this.prog, 'uBoost'), this.boost);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }
}
