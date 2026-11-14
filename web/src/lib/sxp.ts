// SXP1: the binary part format of the service (docs/plan/05-architecture.md 5.7; service/products/sxp.py).
// bytes 0..3 "SXP1"; 4..7 u32 LE header length H; 8..8+H JSON {meta, arrays:[{name,dtype,shape,offset,nbytes}]}; then 8-byte aligned arrays.
// Image arrays: row 0 = north, column 0 = east (east left).

export type DType = 'f2' | 'f4' | 'u1' | 'u2' | 'i2' | 'i4';

export interface SxpArray {
  name: string;
  dtype: DType;
  shape: number[];
  /** Raw little-endian data. float16 stays as Uint16Array (uploaded to WebGL as HALF_FLOAT without conversion). */
  data: Uint16Array | Float32Array | Uint8Array | Int16Array | Int32Array;
}

export interface SxpPart {
  meta: Record<string, unknown>;
  arrays: Record<string, SxpArray>;
}

const MAGIC = 'SXP1';

export function parseSxp(buf: ArrayBuffer): SxpPart {
  const head = new Uint8Array(buf, 0, 8);
  const magic = String.fromCharCode(head[0]!, head[1]!, head[2]!, head[3]!);
  if (magic !== MAGIC) throw new Error('not an SXP1 part');
  const h = new DataView(buf).getUint32(4, true);
  const json = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 8, h))) as {
    meta: Record<string, unknown>;
    arrays: { name: string; dtype: DType; shape: number[]; offset: number; nbytes: number }[];
  };
  const base = 8 + h;
  const arrays: Record<string, SxpArray> = {};
  for (const e of json.arrays) {
    const off = base + e.offset;
    const count = e.shape.reduce((a, b) => a * b, 1);
    let data: SxpArray['data'];
    switch (e.dtype) {
      case 'f2':
      case 'u2':
        data = new Uint16Array(buf, off, count);
        break;
      case 'f4':
        data = new Float32Array(buf, off, count);
        break;
      case 'u1':
        data = new Uint8Array(buf, off, count);
        break;
      case 'i2':
        data = new Int16Array(buf, off, count);
        break;
      case 'i4':
        data = new Int32Array(buf, off, count);
        break;
    }
    arrays[e.name] = { name: e.name, dtype: e.dtype, shape: e.shape, data };
  }
  return { meta: json.meta, arrays };
}

/** float16 bits -> number (for reading values on the page; textures take the bits directly). */
export function halfToFloat(h: number): number {
  const s = h & 0x8000 ? -1 : 1;
  const e = (h >> 10) & 0x1f;
  const f = h & 0x3ff;
  if (e === 0) return s * 2 ** -14 * (f / 1024);
  if (e === 31) return f ? NaN : s * Infinity;
  return s * 2 ** (e - 15) * (1 + f / 1024);
}
