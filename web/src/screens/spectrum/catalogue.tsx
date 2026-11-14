import { useEffect, useState } from 'preact/hooks';
import type { CatRow, Source } from './types';

export type Ask = { state: 'loading' } | { state: 'absent' } | { state: 'error'; error: string } | { state: 'done'; rows: CatRow[]; notes: string[] };

function query(src: Source): URLSearchParams {
  const q = new URLSearchParams({ ra: src.ra.toFixed(6), dec: src.dec.toFixed(6) });
  if (src.pmra || src.pmdec) {
    q.set('pmra', String(src.pmra ?? 0));
    q.set('pmdec', String(src.pmdec ?? 0));
  }
  return q;
}

/** GET /api/v1/spectrum/catalogue, asked again every 3 s while the spectrum is still being measured ("pending"). */
async function viaCatalogueEndpoint(q: URLSearchParams, stopped: () => boolean): Promise<Ask | null> {
  for (let i = 0; i < 30 && !stopped(); i++) {
    const r = await fetch(`/api/v1/spectrum/catalogue?${q}`, { cache: 'no-store' });
    if (stopped()) return null;
    if (r.status === 404) return { state: 'absent' };
    if (!r.ok) throw new Error(`service answered ${r.status}`);
    const d = (await r.json()) as { state: string; rows?: CatRow[]; notes?: string[] };
    if (d.state === 'done') return { state: 'done', rows: d.rows ?? [], notes: d.notes ?? [] };
    await new Promise((res) => setTimeout(res, 3000));
  }
  return stopped() ? null : { state: 'error', error: 'the comparison did not finish in 90 s' };
}

/** The measured spectrum averaged under the 2MASS and WISE filter curves against the catalogue magnitudes (GET /api/v1/spectrum/catalogue; "pending" while it is measured). */
export function useCatalogue(src: Source | null, ready: boolean): Ask {
  const [a, setA] = useState<Ask>({ state: 'loading' });
  useEffect(() => {
    setA({ state: 'loading' });
    if (!src || !ready) return;
    let stop = false;
    viaCatalogueEndpoint(query(src), () => stop)
      .then((d) => {
        if (d && !stop) setA(d);
      })
      .catch((e) => {
        if (!stop) setA({ state: 'error', error: String(e.message ?? e) });
      });
    return () => {
      stop = true;
    };
  }, [src?.ra, src?.dec, src?.pmra, src?.pmdec, ready]);
  return a;
}

/** Zero points (Jy) of the catalogue bands. */
const ZERO_JY: Record<string, number> = { J: 1594, H: 1024, Ks: 666.7, W1: 309.54, W2: 171.79 };

/** The catalogue photometry of a place alone (for a saturated star, which has no spectrum to compare): 2MASS J, H, Ks and WISE W1, W2 in mJy.
 * Read from the cloud copy of AllWISE (GET /api/v1/xmatch, which carries the 2MASS association) when the service has it, else from the
 * comparison route, which needs the TAP services of IRSA. */
export function useReferencePhotometry(src: Source | null, enabled: boolean): Ask {
  const [a, setA] = useState<Ask>({ state: 'loading' });
  useEffect(() => {
    setA({ state: 'loading' });
    if (!src || !enabled) return;
    let stop = false;
    (async () => {
      try {
        const x = new URLSearchParams(query(src));
        x.set('radius', '4');
        x.set('cats', 'allwise');
        const r = await fetch(`/api/v1/xmatch?${x}`);
        if (r.ok) {
          const d = (await r.json()) as { allwise?: { state: string; photometry?: Record<string, { mag?: number | null; err?: number | null; qual?: string | null }> } };
          const w = d.allwise;
          if (w?.state === 'ok' && w.photometry) {
            const rows: CatRow[] = Object.keys(ZERO_JY).map((band) => {
              const p = w.photometry![band];
              const m = p?.mag;
              if (m == null) return { band, cat_mjy: null, mag: null, note: 'no magnitude' };
              return { band, mag: m, cat_mjy: ZERO_JY[band]! * 10 ** (-0.4 * m) * 1000, note: p?.err == null ? 'upper limit' : undefined };
            });
            if (!stop) setA({ state: 'done', rows, notes: [] });
            return;
          }
        }
      } catch {
        // the route is not there or did not answer: the comparison route below
      }
      const d = await viaCatalogueEndpoint(query(src), () => stop);
      if (d && !stop) setA(d);
    })().catch((e) => {
      if (!stop) setA({ state: 'error', error: String(e.message ?? e) });
    });
    return () => {
      stop = true;
    };
  }, [src?.ra, src?.dec, src?.pmra, src?.pmdec, enabled]);
  return a;
}

/** Centre wavelengths (µm) of the catalogue bands, for drawing catalogue photometry on the spectrum chart. */
export const REF_LAM: Record<string, number> = { J: 1.235, H: 1.662, Ks: 2.159, W1: 3.353, W2: 4.603 };

const mjy = (v: number) => `${v.toFixed(v < 10 ? 2 : 1)} mJy`;

/** The check against catalogues: for each band the spectrum under the filter against the catalogue, and their ratio (near 1 = the numbers are right).
 * For a saturated star nothing can be compared: the catalogue photometry alone is listed, and drawn on the chart as reference points. */
export function CatalogueCheck({ a, ready, saturated }: { a: Ask; ready: boolean; saturated?: boolean }) {
  if (!ready) return null;
  if (saturated) {
    if (a.state === 'loading') return <div class="catchk"><div class="spec-sub">Catalogue photometry</div><div class="mono-s">asking 2MASS and AllWISE…</div></div>;
    if (a.state !== 'done') return null;
    const rows = a.rows.filter((r) => r.cat_mjy != null);
    if (rows.length === 0) return null;
    return (
      <div class="catchk">
        <div class="spec-sub">Catalogue photometry</div>
        <table class="cattab">
          <thead>
            <tr>
              <th>Band</th>
              <th>Catalogue</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.band} title={r.note}>
                <td>{r.band}</td>
                <td>{`${mjy(r.cat_mjy!)}${r.mag != null ? ` (${r.mag.toFixed(2)} mag)` : ''}`}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div class="mono-s">{rows.map((r) => r.band).join(', ')} (2MASS 1999, WISE 2010), shown on the chart as open diamonds: this star is saturated in SPHEREx, so these are the only fluxes to go by.</div>
      </div>
    );
  }
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
