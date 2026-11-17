import { useEffect, useState } from 'preact/hooks';
import { useRetry } from '../lab/progress';
import type { Held } from '../lab/known';
import './search.css';

/** One catalogue's answer inside /api/v1/context: ok (with the fields of that catalogue), none (it answered: nothing here) or unavailable. */
interface Part {
  state: 'ok' | 'none' | 'unavailable' | 'pending';
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

/** A catalogue's answer not in yet comes back `pending` (the service keeps asking and keeps the answer): the page asks again every 2 s, up to 20 s, and shows each answer as it lands. */
export const REPOLL_MS = 2000;
export const REPOLL_MAX = 10;
export const hasPending = (r: ContextReply): boolean => Object.values(r).some((p) => typeof p === 'object' && p !== null && (p as Part).state === 'pending');

type Ask = { state: 'loading' } | { state: 'absent' } | { state: 'error'; error: string } | { state: 'ok'; reply: ContextReply };

/** What the catalogues know about a place at an epoch. `absent`: the service has no such route yet (the component then renders nothing).
 *  `skip`: catalogues the page already holds (not asked); `pm`: the held motion, mas/yr, that places the infrared surveys then. `nonce`:
 *  a new value asks again (the retry of a catalogue that did not answer). */
export function useContext(
  ra: number,
  dec: number,
  epoch = 2025.9,
  radius = 6,
  skip: string[] = [],
  pm: [number, number] | null = null,
  nonce = 0,
): Ask {
  const [a, setA] = useState<Ask>({ state: 'loading' });
  const skipKey = skip.join(',');
  const pmKey = pm ? `${pm[0]},${pm[1]}` : '';
  useEffect(() => {
    // a retry keeps what is shown until the new answer lands
    if (!nonce) setA({ state: 'loading' });
    const ctl = new AbortController();
    const q = new URLSearchParams({ ra: ra.toFixed(6), dec: dec.toFixed(6), epoch: String(epoch), radius: String(radius) });
    if (skipKey) q.set('skip', skipKey);
    if (pm && skipKey.includes('gaia') && skipKey.includes('simbad')) {
      q.set('pmra', String(pm[0]));
      q.set('pmdec', String(pm[1]));
    }
    const ask = async (n: number): Promise<void> => {
      const r = await fetch(`/api/v1/context?${q}`, { signal: ctl.signal });
      if (r.status === 404) return setA({ state: 'absent' });
      if (!r.ok) throw new Error(`service answered ${r.status}`);
      const reply = (await r.json()) as ContextReply;
      setA({ state: 'ok', reply });
      if (hasPending(reply) && n < REPOLL_MAX) {
        await new Promise((res) => setTimeout(res, REPOLL_MS));
        if (!ctl.signal.aborted) await ask(n + 1);
      }
    };
    ask(0).catch((e) => {
      if (!ctl.signal.aborted) setA({ state: 'error', error: String(e.message ?? e) });
    });
    return () => ctl.abort();
  }, [ra, dec, epoch, radius, skipKey, pmKey, nonce]);
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

/** A catalogue row as the field's lookup already answered it, or null when the lookup did not cover that catalogue. */
export function heldRow(key: keyof ContextReply, held: Held | null | undefined): string | null {
  if (!held || !held.covered.includes(key as 'gaia' | 'simbad')) return null;
  if (key === 'simbad') return held.simbad === 'none' || !held.simbad ? 'no entry' : `${held.simbad.name}${held.simbad.type ? ` · ${held.simbad.type}` : ''}`;
  const g = held.gaia;
  if (!g) return 'no entry';
  const parts = [g.source_id];
  if (g.g != null) parts.push(`G ${g.g.toFixed(2)}`);
  if (g.bp_rp != null) parts.push(`BP−RP ${g.bp_rp.toFixed(2)}`);
  if (g.parallax != null && g.parallax > 0) parts.push(`parallax ${g.parallax.toFixed(2)} mas`);
  if (g.ruwe != null) parts.push(`RUWE ${g.ruwe.toFixed(1)}`);
  if (g.pmra != null && g.pmdec != null) parts.push(`motion ${(Math.hypot(g.pmra, g.pmdec) / 1000).toFixed(2)} ″/yr`);
  return parts.join(' · ');
}

/** What else is known at a place: one row per catalogue (SIMBAD, Gaia, 2MASS, AllWISE, CatWISE, VSX, NED). A catalogue that did not answer is said, never left out. */
export function ContextRows({
  ra,
  dec,
  epoch,
  pmra = 0,
  pmdec = 0,
  held = null,
  omit = [],
}: {
  ra: number;
  dec: number;
  epoch?: number;
  pmra?: number;
  pmdec?: number;
  /** rows another part of the page already shows (the lab's passport has SIMBAD and Gaia DR3 above): not repeated here */
  omit?: string[];
  /** what the field's lookup already holds about this source (a ringed known star): its rows are shown from it, not asked again */
  held?: Held | null;
}) {
  const skip = held?.covered ?? [];
  const hpm: [number, number] | null = held?.gaia?.pmra != null && held.gaia.pmdec != null ? [held.gaia.pmra, held.gaia.pmdec] : null;
  const [nonce, setNonce] = useState(0);
  const a = useContext(ra, dec, epoch, 6, skip, hpm, nonce);
  const cloud = useCloudAllwise(ra, dec, pmra, pmdec);
  // a catalogue that did not answer here is asked again by itself, on the field lookup's schedule, with its countdown
  const silent = a.state === 'error' || (a.state === 'ok' && !hasPending(a.reply) && ROWS.some((r) => (a.reply[r.key] as Part | undefined)?.state === 'unavailable'));
  const retry = useRetry(silent, () => setNonce((n) => n + 1), `${ra.toFixed(5)},${dec.toFixed(5)}`);
  const known = !!held || (a.state === 'ok' && ['simbad', 'gaia', 'twomass', 'allwise', 'catwise'].some((k) => (a.reply[k as keyof ContextReply] as Part | undefined)?.state === 'ok'));
  const nextTry = retry.nextIn != null ? ` · retry ${retry.nextIn} s` : '';
  if (a.state === 'absent') return null;
  return (
    <div class="ctxbox">
      <div class="search-h">What else is known here</div>
      {a.state === 'loading' && (
        <div class="mono-s">
          {skip.length ? 'Asking the infrared catalogues, VSX and NED…' : 'Asking SIMBAD, Gaia and the infrared catalogues…'}
        </div>
      )}
      {a.state === 'error' && (
        <div class="mono-s">
          The catalogues could not be asked: {a.error}
          {nextTry}
        </div>
      )}
      {a.state === 'ok' && (
        <>
          <div class="okv">
            {ROWS.filter((row) => !omit.includes(row.key)).map((row) => {
              const p = a.reply[row.key] as Part | undefined;
              const radius = a.reply.radius ?? 6;
              let text = 'not asked';
              let tip: string | undefined;
              let href: string | undefined;
              const fromCloud = p?.state === 'unavailable' && (row.key === 'twomass' || row.key === 'allwise') ? cloud : null;
              const h = heldRow(row.key, held);
              if (h) {
                text = h;
                tip = "the field's catalogue lookup already answered this: not asked again";
              } else if (fromCloud?.state === 'ok') {
                const c = fromCloud.row;
                const sep = typeof c.sep_arcsec === 'number' ? ` · ${c.sep_arcsec.toFixed(1)}″ away` : '';
                text =
                  row.key === 'twomass'
                    ? c.j_m_2mass == null && c.h_m_2mass == null && c.k_m_2mass == null
                      ? 'no entry'
                      : `J ${mag(c.j_m_2mass)} · H ${mag(c.h_m_2mass)} · Ks ${mag(c.k_m_2mass)}${sep}`
                    : `W1 ${mag(c.w1mpro)} · W2 ${mag(c.w2mpro)} · W3 ${mag(c.w3mpro)} · W4 ${mag(c.w4mpro)}${sep}`;
                text += ' · AllWISE copy';
                tip =
                  "IRSA's catalogue service did not answer; these come from the AllWISE catalogue on AWS (cloud Parquet), which carries the 2MASS magnitudes";
              } else if (fromCloud?.state === 'none') {
                text = `nothing within ${radius}″ · AllWISE copy`;
              } else if (p?.state === 'pending') {
                text = 'asking…';
                tip = 'the catalogue has not answered yet: this page asks again';
              } else if (p?.state === 'unavailable') {
                text = `no answer${nextTry}`;
                tip = p.error;
              } else if (p?.state === 'none') {
                // a known source that one catalogue does not list: a quiet "no entry", not the headline of an unknown
                text = known ? 'no entry' : `nothing within ${radius}″`;
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
            radius {a.reply.radius ?? 6}″ at the passes' epoch
            {silent ? nextTry : ''}
            {silent && (
              <>
                {' '}
                <button class="lnk" onClick={retry.tryNow}>
                  now
                </button>
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}
