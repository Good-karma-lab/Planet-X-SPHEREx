// Tiles of all-sky surveys (HiPS), read through the service's tile cache and decoded to pixels once; a sampler that answers the
// colour of a survey at any sky position from the deepest tile at hand, and cutouts of a field drawn from those tiles.

import { ang2pix, faceXY } from './healpix';

/** Tiles of the all-sky surveys, read through the service and decoded to pixels once. */
export const tiles = (() => {
  const cache = new Map<string, { w: number; h: number; d: Uint8ClampedArray } | null>();
  const pending = new Set<string>();
  let onTile: () => void = () => {};
  const load = (key: string, url: string, soft: number) => {
    if (cache.has(key) || pending.has(key)) return;
    pending.add(key);
    const im = new Image();
    im.onload = () => {
      pending.delete(key);
      const c = document.createElement('canvas');
      c.width = im.width;
      c.height = im.height;
      const x = c.getContext('2d', { willReadFrequently: true })!;
      if (soft) x.filter = `blur(${soft}px)`;
      x.drawImage(im, 0, 0);
      cache.set(key, { w: im.width, h: im.height, d: x.getImageData(0, 0, im.width, im.height).data });
      if (cache.size > 180) for (const k of cache.keys()) if (!k.endsWith('/allsky')) { cache.delete(k); break; }
      onTile();
    };
    im.onerror = () => {
      pending.delete(key);
      cache.set(key, null);
    };
    im.src = url;
  };
  const px = (t: { w: number; d: Uint8ClampedArray }, row: number, col: number): [number, number, number] => {
    const q = (row * t.w + col) * 4;
    return [t.d[q]!, t.d[q + 1]!, t.d[q + 2]!];
  };
  /** Colour of a survey at a position, from the deepest tile at hand up to `order`; null until something has arrived. */
  function sample(id: string, ra: number, dec: number, order: number): [number, number, number] | null {
    for (let n = order; n >= 3 && order >= 3; n--) {
      const i = ang2pix(n, ra, dec);
      const key = `${id}/${n}/${i}`;
      const t = cache.get(key);
      if (t) {
        const bits = Math.round(Math.log2(t.w));
        const f = faceXY(n + bits, ra, dec);
        return px(t, f.ix & (t.w - 1), f.iy & (t.w - 1));
      }
      if (t === undefined && n === order) load(key, `/api/v1/hips/${id}/${n}/${i}`, 0);
    }
    const key = `${id}/3/allsky`;
    const a = cache.get(key);
    if (a === undefined) {
      load(key, `/api/v1/hips/${id}/3/allsky`, 0.9);
      return null;
    }
    if (!a) return null;
    const ts = Math.round(a.w / 27);
    const bits = Math.round(Math.log2(ts));
    const f = faceXY(3 + bits, ra, dec);
    const tile = f.face * 64 + ang2pixLocal(f.ix >> bits, f.iy >> bits);
    return px(a, Math.floor(tile / 27) * ts + (f.ix & (ts - 1)), (tile % 27) * ts + (f.iy & (ts - 1)));
  }
  const listeners = new Set<() => void>();
  onTile = () => listeners.forEach((f) => f());
  return {
    sample,
    /** called whenever a tile arrives; returns the function that stops it */
    onTile: (f: () => void) => {
      listeners.add(f);
      return () => listeners.delete(f);
    },
  };
})();

/** Nested index of (x, y) inside a face at order 3. */
function ang2pixLocal(x: number, y: number): number {
  let r = 0;
  for (let b = 0; b < 3; b++) r += (((x >> b) & 1) << (2 * b)) + (((y >> b) & 1) << (2 * b + 1));
  return r;
}


/** A cutout of a survey around a place: north up, east left, `size` pixels for `fovDeg` degrees (gnomonic). Returns the RGBA pixels
 *  and whether every pixel had a tile of the order asked for (false while tiles are still arriving). */
export function cutout(id: string, maxOrder: number, ra0: number, dec0: number, fovDeg: number, size: number): { data: ImageData; complete: boolean } {
  const D = Math.PI / 180;
  const scale = (fovDeg * 3600) / size; // ″ per output pixel
  const order = Math.max(3, Math.min(maxOrder, Math.ceil(Math.log2(412 / scale))));
  const img = new ImageData(size, size);
  const vals = new Float32Array(size * size * 3);
  let complete = true;
  const sd = Math.sin(dec0 * D);
  const cd = Math.cos(dec0 * D);
  for (let j = 0; j < size; j++)
    for (let i = 0; i < size; i++) {
      // tangent-plane offsets: x to the east (left on the picture), y to the north (up)
      const xi = -((i + 0.5) / size - 0.5) * fovDeg * D;
      const eta = (0.5 - (j + 0.5) / size) * fovDeg * D;
      const den = cd - eta * sd;
      const ra = ra0 + Math.atan2(xi, den) / D;
      const dec = Math.atan2(sd + eta * cd, Math.hypot(xi, den)) / D;
      const c = tiles.sample(id, ((ra % 360) + 360) % 360, dec, order);
      const q = (j * size + i) * 3;
      if (c) {
        vals[q] = c[0];
        vals[q + 1] = c[1];
        vals[q + 2] = c[2];
      } else {
        complete = false;
        vals[q] = vals[q + 1] = vals[q + 2] = NaN;
      }
    }
  // stretch: the 2nd percentile to black, the 99.7th to white
  const lum: number[] = [];
  for (let k = 0; k < vals.length; k += 9) if (Number.isFinite(vals[k]!)) lum.push(vals[k]!);
  lum.sort((a, b) => a - b);
  const lo = lum.length ? lum[Math.floor(0.02 * (lum.length - 1))]! : 0;
  const hi = lum.length ? Math.max(lo + 1, lum[Math.floor(0.997 * (lum.length - 1))]!) : 255;
  for (let k = 0, p = 0; k < vals.length; k += 3, p += 4) {
    if (!Number.isFinite(vals[k]!)) {
      img.data[p + 3] = 255;
      continue;
    }
    for (let c = 0; c < 3; c++) img.data[p + c] = Math.max(0, Math.min(255, ((vals[k + c]! - lo) / (hi - lo)) * 255));
    img.data[p + 3] = 255;
  }
  return { data: img, complete };
}
