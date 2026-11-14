import { useEffect, useState } from 'preact/hooks';
import type { Source } from './types';

/** The quick look of a place from the DR1 all-sky spectral cube (GET /api/v1/spectrum/quicklook): surface brightness of one 6″ pixel, not a PSF flux. */
export interface Quick {
  label: string;
  source?: string;
  unit: string;
  lam: number[];
  flux: (number | null)[];
  note?: string;
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
