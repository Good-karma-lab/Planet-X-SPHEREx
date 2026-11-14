import { useEffect, useState } from 'preact/hooks';
import type { Source } from './types';

/** The quick look of a place from the DR1 all-sky spectral cube (GET /api/v1/spectrum/quicklook): surface brightness of one 6″ pixel, not a PSF flux. */
export interface Quick {
  label: string;
  source?: string;
  unit: string;
  lam: number[];
  flux: (number | null)[];
  /** exposures behind each plane's value at this pixel (0 or null: nothing measured there) */
  nhits?: (number | null)[];
  note?: string;
}

/** mJy in one cube pixel for 1 MJy/sr: the cube's pixels are HEALPix order 14, all of the solid angle 4π / (12 · 4¹⁴) sr (12.9″ across). */
export const MJY_SR_TO_MJY_PIXEL = (1e9 * 4 * Math.PI) / (12 * 4 ** 14);

export interface QuickPoint {
  lam: number;
  /** flux in one 12.9″ cube pixel, mJy (a surface brightness times the pixel, not a point-source flux) */
  mjy: number;
}

/** The planes worth drawing, in mJy per cube pixel: a finite value with at least one exposure behind it. Null when fewer than half of the
 * planes (and fewer than 20) qualify: a saturated core or a gap of the map, where the line would only be noise. */
export function quickPoints(q: Quick | null): QuickPoint[] | null {
  if (!q || !q.lam?.length || q.unit !== 'MJy/sr') return null;
  const out: QuickPoint[] = [];
  q.lam.forEach((lam, i) => {
    const v = q.flux[i];
    const n = q.nhits?.[i];
    if (v == null || !Number.isFinite(v) || !Number.isFinite(lam)) return;
    if (q.nhits && !(n != null && n > 0)) return;
    out.push({ lam, mjy: v * MJY_SR_TO_MJY_PIXEL });
  });
  return out.length >= Math.max(20, 0.5 * q.lam.length) ? out : null;
}

/** Asked at once on a click; null while loading or when the service has none for this place (404 off the tile, 502 when the bucket is silent). */
export function useQuicklook(src: Source | null): Quick | null {
  const [q, setQ] = useState<Quick | null>(null);
  useEffect(() => {
    setQ(null);
    if (!src) return;
    const ctl = new AbortController();
    const p = new URLSearchParams({ ra: src.ra.toFixed(6), dec: src.dec.toFixed(6) });
    if (src.pmra || src.pmdec) {
      p.set('pmra', String(src.pmra ?? 0));
      p.set('pmdec', String(src.pmdec ?? 0));
    }
    fetch(`/api/v1/spectrum/quicklook?${p}`, { signal: ctl.signal })
      .then((r) => (r.ok ? (r.json() as Promise<Quick>) : null))
      .then((d) => {
        if (d && d.lam?.length) setQ(d);
      })
      .catch(() => {});
    return () => ctl.abort();
  }, [src?.ra, src?.dec, src?.pmra, src?.pmdec]);
  return q;
}
