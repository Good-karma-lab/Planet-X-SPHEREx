import { useEffect, useState } from 'preact/hooks';
import './search.css';

/** One catalogue's answer inside /api/v1/context: ok (with the fields of that catalogue), none (it answered: nothing here) or unavailable. */
interface Part {
  state: 'ok' | 'none' | 'unavailable';
  error?: string;
  sep?: number | null;
  [k: string]: unknown;
}

export interface ContextReply {
  ra: number;
  dec: number;
  epoch: number;
  radius?: number;
  complete?: boolean;
  simbad?: Part;
  gaia?: Part;
  twomass?: Part;
  allwise?: Part;
  catwise?: Part;
  vsx?: Part;
  ned?: Part;
}

type Ask = { state: 'loading' } | { state: 'absent' } | { state: 'error'; error: string } | { state: 'ok'; reply: ContextReply };

/** What the catalogues know about a place at an epoch. `absent`: the service has no such route yet (the component then renders nothing). */
export function useContext(ra: number, dec: number, epoch = 2025.9, radius = 6): Ask {
  const [a, setA] = useState<Ask>({ state: 'loading' });
  useEffect(() => {
    setA({ state: 'loading' });
    const ctl = new AbortController();
    const q = new URLSearchParams({ ra: ra.toFixed(6), dec: dec.toFixed(6), epoch: String(epoch), radius: String(radius) });
    fetch(`/api/v1/context?${q}`, { signal: ctl.signal })
      .then(async (r) => {
        if (r.status === 404) return setA({ state: 'absent' });
        if (!r.ok) throw new Error(`service answered ${r.status}`);
        setA({ state: 'ok', reply: (await r.json()) as ContextReply });
      })
      .catch((e) => {
        if (!ctl.signal.aborted) setA({ state: 'error', error: String(e.message ?? e) });
      });
    return () => ctl.abort();
  }, [ra, dec, epoch, radius]);
  return a;
}

const num = (v: unknown, d = 2) => (typeof v === 'number' ? v.toFixed(d) : String(v));
const sepText = (s: unknown) => (typeof s === 'number' ? ` · ${s.toFixed(1)}″ away` : '');
const motion = (p: Part) =>
  typeof p.pmra === 'number' && typeof p.pmdec === 'number' ? ` · motion ${(Math.hypot(p.pmra, p.pmdec) / 1000).toFixed(2)} ″/yr` : '';

interface Row {
  key: keyof ContextReply;
  name: string;
  ok: (p: Part) => string;
  link?: (p: Part) => string | undefined;
}

const ROWS: Row[] = [
  {
    key: 'simbad',
    name: 'SIMBAD',
    ok: (p) =>
      `${p.name}${p.otype ? ` · ${p.otype}` : ''}${p.sptype ? ` · ${p.sptype}` : ''}${p.bibcount != null ? ` · ${p.bibcount} papers` : ''}${sepText(p.sep)}`,
    link: (p) => (typeof p.link === 'string' ? p.link : undefined),
  },
  {
    key: 'gaia',
    name: 'Gaia DR3',
    ok: (p) =>
      `G ${num(p.g)}${p.bp_rp != null ? ` · BP−RP ${num(p.bp_rp)}` : ''}${p.parallax != null ? ` · parallax ${num(p.parallax)} mas` : ''}${p.ruwe != null ? ` · RUWE ${num(p.ruwe, 1)}` : ''}${p.variable === 'VARIABLE' ? ' · flagged variable' : ''}${sepText(p.sep)}`,
  },
  { key: 'twomass', name: '2MASS', ok: (p) => `J ${p.J} · H ${p.H} · Ks ${p.Ks}${sepText(p.sep)}` },
  { key: 'allwise', name: 'AllWISE', ok: (p) => `W1 ${p.W1} · W2 ${p.W2} · W3 ${p.W3} · W4 ${p.W4}${sepText(p.sep)}` },
  { key: 'catwise', name: 'CatWISE', ok: (p) => `W1 ${p.W1} · W2 ${p.W2}${motion(p)}${sepText(p.sep)}` },
  {
    key: 'vsx',
    name: 'AAVSO VSX',
    ok: (p) => `${p.name} · ${p.type || 'variable'}${p.period ? ` · P ${p.period} d` : ''}${sepText(p.sep)}`,
  },
  { key: 'ned', name: 'NED', ok: (p) => `${p.name} · ${p.type || ''}${p.z != null ? ` · z ${p.z}` : ''}${sepText(p.sep)}` },
];

/** AllWISE (with its 2MASS magnitudes) read from the cloud copy on AWS (no TAP service in the path): the rows of 2MASS and AllWISE when
 *  IRSA's TAP does not answer. null while asked, or when the cloud copy does not answer either. */
interface CloudRow {
  designation?: string;
  w1mpro?: number | null;
  w2mpro?: number | null;
  w3mpro?: number | null;
  w4mpro?: number | null;
  j_m_2mass?: number | null;
  h_m_2mass?: number | null;
  k_m_2mass?: number | null;
  sep_arcsec?: number;
}
type Cloud = { state: 'ok'; row: CloudRow } | { state: 'none' } | null;
function useCloudAllwise(ra: number, dec: number, pmra: number, pmdec: number, radius = 6): Cloud {
  const [c, setC] = useState<Cloud>(null);
  useEffect(() => {
    setC(null);
    const ctl = new AbortController();
    // the place at J2000 with its motion: the service moves it to the catalogue's epoch
    const q = new URLSearchParams({
      ra: ra.toFixed(6),
      dec: dec.toFixed(6),
      pmra: String(Math.round(pmra)),
      pmdec: String(Math.round(pmdec)),
      radius: String(radius),
      cats: 'allwise',
    });
    fetch(`/api/v1/xmatch?${q}`, { signal: ctl.signal })
      .then((r) => (r.ok ? r.json() : null))
      .then((j: { allwise?: { state: string; nearest?: CloudRow } } | null) => {
        const a = j?.allwise;
        if (a?.state === 'ok' && a.nearest) setC({ state: 'ok', row: a.nearest });
        else if (a?.state === 'none') setC({ state: 'none' });
      })
      .catch(() => {});
    return () => ctl.abort();
  }, [ra, dec, pmra, pmdec, radius]);
  return c;
}
const mag = (v: number | null | undefined) => (typeof v === 'number' ? v.toFixed(2) : '—');

/** What else is known at a place: one row per catalogue (SIMBAD, Gaia, 2MASS, AllWISE, CatWISE, VSX, NED). A catalogue that did not answer is said, never left out. */
export function ContextRows({
  ra,
  dec,
  epoch,
  pmra = 0,
  pmdec = 0,
}: {
  ra: number;
  dec: number;
  epoch?: number;
  pmra?: number;
  pmdec?: number;
}) {
  const a = useContext(ra, dec, epoch);
  const cloud = useCloudAllwise(ra, dec, pmra, pmdec);
  if (a.state === 'absent') return null;
  return (
    <div class="ctxbox">
      <div class="search-h">What else is known here</div>
      {a.state === 'loading' && <div class="mono-s">Asking SIMBAD, Gaia and the infrared catalogues…</div>}
      {a.state === 'error' && <div class="mono-s">The catalogues could not be asked: {a.error}</div>}
      {a.state === 'ok' && (
        <>
          <div class="okv">
            {ROWS.map((row) => {
              const p = a.reply[row.key] as Part | undefined;
              const radius = a.reply.radius ?? 6;
              let text = 'not asked';
              let tip: string | undefined;
              let href: string | undefined;
              const fromCloud = p?.state === 'unavailable' && (row.key === 'twomass' || row.key === 'allwise') ? cloud : null;
              if (fromCloud?.state === 'ok') {
                const c = fromCloud.row;
                const sep = typeof c.sep_arcsec === 'number' ? ` · ${c.sep_arcsec.toFixed(1)}″ away` : '';
                text =
                  row.key === 'twomass'
                    ? c.j_m_2mass == null && c.h_m_2mass == null && c.k_m_2mass == null
                      ? 'no 2MASS match in the AllWISE copy'
                      : `J ${mag(c.j_m_2mass)} · H ${mag(c.h_m_2mass)} · Ks ${mag(c.k_m_2mass)}${sep}`
                    : `W1 ${mag(c.w1mpro)} · W2 ${mag(c.w2mpro)} · W3 ${mag(c.w3mpro)} · W4 ${mag(c.w4mpro)}${sep}`;
                text += ' · from the AllWISE copy on AWS';
                tip =
                  "IRSA's catalogue service did not answer; these come from the AllWISE catalogue on AWS (cloud Parquet), which carries the 2MASS magnitudes";
              } else if (fromCloud?.state === 'none') {
                text = `nothing within ${radius}″ · from the AllWISE copy on AWS`;
              } else if (p?.state === 'unavailable') {
                text = 'did not answer';
                tip = p.error;
              } else if (p?.state === 'none') {
                text = `nothing within ${radius}″`;
                tip = 'the catalogue answered: no source at this place';
              } else if (p?.state === 'ok') {
                text = row.ok(p);
                href = row.link?.(p);
              }
              return (
                <div class="orow" key={row.name} title={tip}>
                  <span class="faint">{row.name}</span>
                  <span>
                    {href ? (
                      <a href={href} target="_blank" rel="noopener">
                        {text}
                      </a>
                    ) : (
                      text
                    )}
                  </span>
                </div>
              );
            })}
          </div>
          <div class="mono-s">
            searched at the epoch of the passes, radius {a.reply.radius ?? 6}″
            {a.reply.complete === false ? ' · at least one catalogue did not answer: asked again next time' : ''}
          </div>
        </>
      )}
    </div>
  );
}
