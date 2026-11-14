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

/** What else is known at a place: one row per catalogue (SIMBAD, Gaia, 2MASS, AllWISE, CatWISE, VSX, NED). A catalogue that did not answer is said, never left out. */
export function ContextRows({ ra, dec, epoch }: { ra: number; dec: number; epoch?: number }) {
  const a = useContext(ra, dec, epoch);
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
              if (p?.state === 'unavailable') {
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
