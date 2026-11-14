import { useEffect, useState } from 'preact/hooks';
import type { CatRow, Source } from './types';

type Ask = { state: 'loading' } | { state: 'absent' } | { state: 'error'; error: string } | { state: 'done'; rows: CatRow[]; notes: string[] };

/** The measured spectrum averaged under the 2MASS and WISE filter curves against the catalogue magnitudes (GET /api/v1/spectrum/catalogue; "pending" while it is measured). */
export function useCatalogue(src: Source | null, ready: boolean): Ask {
  const [a, setA] = useState<Ask>({ state: 'loading' });
  useEffect(() => {
    setA({ state: 'loading' });
    if (!src || !ready) return;
    let stop = false;
    const q = new URLSearchParams({ ra: src.ra.toFixed(6), dec: src.dec.toFixed(6) });
    if (src.pmra || src.pmdec) {
      q.set('pmra', String(src.pmra ?? 0));
      q.set('pmdec', String(src.pmdec ?? 0));
    }
    (async () => {
      for (let i = 0; i < 30 && !stop; i++) {
        const r = await fetch(`/api/v1/spectrum/catalogue?${q}`, { cache: 'no-store' });
        if (stop) return;
        if (r.status === 404) return setA({ state: 'absent' });
        if (!r.ok) throw new Error(`service answered ${r.status}`);
        const d = (await r.json()) as { state: string; rows?: CatRow[]; notes?: string[] };
        if (d.state === 'done') return setA({ state: 'done', rows: d.rows ?? [], notes: d.notes ?? [] });
        await new Promise((res) => setTimeout(res, 3000));
      }
      if (!stop) setA({ state: 'error', error: 'the comparison did not finish in 90 s' });
    })().catch((e) => {
      if (!stop) setA({ state: 'error', error: String(e.message ?? e) });
    });
    return () => {
      stop = true;
    };
  }, [src?.ra, src?.dec, src?.pmra, src?.pmdec, ready]);
  return a;
}

const mjy = (v: number) => `${v.toFixed(v < 10 ? 2 : 1)} mJy`;

/** The check against catalogues: for each band the spectrum under the filter against the catalogue, and their ratio (near 1 = the numbers are right). */
export function CatalogueCheck({ src, ready, saturated }: { src: Source | null; ready: boolean; saturated?: boolean }) {
  const a = useCatalogue(src, ready && !saturated);
  if (!ready) return null;
  if (saturated)
    return (
      <div class="catchk">
        <div class="spec-sub">Check against catalogues</div>
        <div class="mono-s">no unsaturated points to compare with the catalogues</div>
      </div>
    );
  if (a.state === 'absent') return null;
  return (
    <div class="catchk">
      <div class="spec-sub">Check against catalogues</div>
      {a.state === 'loading' && <div class="mono-s">comparing with 2MASS and AllWISE…</div>}
      {a.state === 'error' && <div class="mono-s">The catalogues could not be asked: {a.error}</div>}
      {a.state === 'done' && (
        <>
          {a.rows.length === 0 ? (
            <div class="mono-s">no band could be compared</div>
          ) : (
            <table class="cattab">
              <thead>
                <tr>
                  <th>Band</th>
                  <th>This spectrum</th>
                  <th>Catalogue</th>
                  <th>Ratio</th>
                </tr>
              </thead>
              <tbody>
                {a.rows.map((r) => (
                  <tr key={r.band} title={r.note}>
                    <td>{r.band}</td>
                    <td>{r.synth_mjy != null ? mjy(r.synth_mjy) : '–'}</td>
                    <td>{r.cat_mjy != null ? `${mjy(r.cat_mjy)}${r.mag != null ? ` (${r.mag.toFixed(2)} mag)` : ''}` : r.note || '–'}</td>
                    <td class={r.ratio != null && Math.abs(r.ratio - 1) > 0.25 ? 'warn' : ''}>{r.ratio != null ? r.ratio.toFixed(2) : '–'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div class="mono-s">
            The measured spectrum averaged under the 2MASS J, H, Ks and WISE W1, W2 filter curves (photon counting) against the catalogue magnitude (2MASS 1999, AllWISE 2010: a variable source can differ). Only photometric quality A or B is compared. {a.notes.join(' ')}
          </div>
        </>
      )}
    </div>
  );
}
