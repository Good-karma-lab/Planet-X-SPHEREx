import { useEffect, useRef, useState } from 'preact/hooks';
import { fetchBodyPath, todayText, type BodyPath, type SmallBody } from './path';

interface Page {
  count: number;
  rows: SmallBody[];
  /** a name that fits several bodies: the choices */
  candidates?: { pdes: string; full_name: string; kind: string }[];
}
type Ask = { state: 'loading' } | { state: 'absent' } | { state: 'error'; error: string } | { state: 'ok'; page: Page };

const PER = 25;

/** The comets or asteroids of the JPL Small-Body Database, a page at a time (or one body by name). `absent`: the service has no such route yet. */
export function useKnown(kind: 'a' | 'c', page: number, name: string): Ask {
  const [a, setA] = useState<Ask>({ state: 'loading' });
  useEffect(() => {
    setA({ state: 'loading' });
    const ctl = new AbortController();
    const q = name.trim()
      ? new URLSearchParams({ q: name.trim() })
      : new URLSearchParams({ kind, page: String(page), per: String(PER), sort: 'number' });
    fetch(`/api/v1/known?${q}`, { signal: ctl.signal })
      .then(async (r) => {
        if (r.status === 404) return setA({ state: 'absent' });
        if (!r.ok) {
          const e = (await r.json().catch(() => ({}))) as { detail?: unknown };
          throw new Error(typeof e.detail === 'string' ? e.detail : `the Small-Body Database answered ${r.status}`);
        }
        const d = (await r.json()) as { count?: number; rows?: SmallBody[]; candidates?: Page['candidates'] };
        setA({ state: 'ok', page: { count: d.count ?? d.rows?.length ?? 0, rows: d.rows ?? [], candidates: d.candidates } });
      })
      .catch((e) => {
        if (!ctl.signal.aborted) setA({ state: 'error', error: String(e.message ?? e) });
      });
    return () => ctl.abort();
  }, [kind, page, name]);
  return a;
}

/** A day on which a body crosses SPHEREx exposures (from /api/v1/known/crossings). */
export interface Crossing {
  ra: number;
  dec: number;
  mjd0: number;
  mjd1: number;
  n: number;
}

/**
 * The list of comets and asteroids for the globe's side panel. "Show on map" fetches the body's path and hands it to the owner of the
 * globe (`onShow`), which draws it with drawBodyPath; "Open in lab" asks the service for the days the body is in exposures and hands
 * the best one to `onOpen`. Renders nothing while the service has no /api/v1/known.
 */
export function KnownList({
  onShow,
  onOpen,
  selected,
  today,
}: {
  onShow: (p: BodyPath) => void;
  onOpen: (b: SmallBody, c: Crossing) => void;
  /** spkid of the body shown on the map */
  selected?: number | null;
  /** the path of the selected body, for its "today" line */
  today?: BodyPath | null;
}) {
  const [kind, setKind] = useState<'a' | 'c'>('a');
  const [page, setPage] = useState(0);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState<number | null>(null);
  const [note, setNote] = useState('');
  const a = useKnown(kind, page, name);
  const timer = useRef<number>(0);
  if (a.state === 'absent') return null;
  const pages = a.state === 'ok' ? Math.max(1, Math.ceil(a.page.count / PER)) : 1;

  const show = async (b: SmallBody) => {
    setBusy(b.spkid);
    setNote('');
    try {
      const p = await fetchBodyPath(b);
      if (!p) setNote('The service has no ephemeris yet.');
      else onShow(p);
    } catch {
      setNote('JPL Horizons did not answer for this body.');
    }
    setBusy(null);
  };
  const open = async (b: SmallBody) => {
    setNote('Looking for the days when this body is in SPHEREx exposures…');
    try {
      const r = await fetch(`/api/v1/known/crossings?target=${encodeURIComponent(b.pdes ?? String(b.spkid))}`);
      if (r.status === 404) return setNote('The service cannot list the crossings yet.');
      if (!r.ok) throw new Error(String(r.status));
      const d = (await r.json()) as { crossings?: Crossing[] };
      const best = (d.crossings ?? []).sort((x, y) => y.n - x.n)[0];
      if (!best || !best.n) return setNote(`${b.full_name} was not found inside a SPHEREx exposure along its path.`);
      setNote('');
      onOpen(b, best);
    } catch {
      setNote('Could not find the days of this body in the archive.');
    }
  };

  return (
    <div class="known">
      <div class="sb-bar">
        <div class="seg sm">
          {(
            [
              ['a', 'Asteroids'],
              ['c', 'Comets'],
            ] as const
          ).map(([k, l]) => (
            <button
              key={k}
              class={kind === k && !name ? 'on' : ''}
              onClick={() => {
                setKind(k);
                setPage(0);
                setName('');
              }}
            >
              {l}
            </button>
          ))}
        </div>
        <input
          class="known-q"
          type="search"
          placeholder="Name or number"
          aria-label="Find a small body by name or number"
          onInput={(e) => {
            const v = (e.currentTarget as HTMLInputElement).value;
            clearTimeout(timer.current);
            timer.current = window.setTimeout(() => {
              setName(v);
              setPage(0);
            }, 450);
          }}
        />
        <div class="mono-s">Real orbits and sizes from the JPL Small-Body Database. A body is not at a fixed place: its path since April 2025 is drawn on the map.</div>
      </div>
      {note && <div class="mono-s known-note">{note}</div>}
      {a.state === 'loading' && <div class="empty">Asking JPL…</div>}
      {a.state === 'error' && <div class="empty">{a.error}</div>}
      {a.state === 'ok' && a.page.candidates && a.page.candidates.length > 0 && (
        <div class="chips known-note">
          <span class="mono-s">Several bodies match:</span>
          {a.page.candidates.map((c) => (
            <button key={c.pdes} class="chip" onClick={() => setName(c.pdes)}>
              {c.full_name}
            </button>
          ))}
        </div>
      )}
      {a.state === 'ok' && !a.page.rows.length && !a.page.candidates && name && <div class="empty">No body of that name.</div>}
      {a.state === 'ok' &&
        a.page.rows.map((r) => {
          const on = selected === r.spkid;
          return (
            <div key={r.spkid} class={`row${on ? ' on' : ''}`} onClick={() => void show(r)}>
              <div class="nm">
                <span class="t" title={r.full_name}>
                  {r.full_name}
                </span>
                <span class="badge orange">{r.class ?? r.kind}</span>
              </div>
              <div class="co">
                {r.H != null ? `H ${r.H} · ` : ''}
                {r.diameter ? `diameter ${r.diameter} km` : 'size unknown'}
                {r.albedo ? ` · albedo ${r.albedo}` : ''}
              </div>
              <div class="mv">
                <span>
                  orbit: a {r.a} au · e {r.e} · i {r.i}°{r.q ? ` · perihelion ${r.q} au` : ''}
                </span>
              </div>
              {on && today && (
                <div class="mv">
                  <span>{todayText(today)}</span>
                </div>
              )}
              <div class="gobtns">
                <button
                  class="btn sm"
                  onClick={(e) => {
                    e.stopPropagation();
                    void show(r);
                  }}
                >
                  {busy === r.spkid ? 'Asking JPL…' : 'Show on map'}
                </button>
                <button
                  class="btn sm"
                  onClick={(e) => {
                    e.stopPropagation();
                    void open(r);
                  }}
                >
                  Open in lab
                </button>
                <a
                  class="btn sm"
                  href={`https://ssd.jpl.nasa.gov/tools/sbdb_lookup.html#/?sstr=${encodeURIComponent(r.pdes ?? String(r.spkid))}`}
                  target="_blank"
                  rel="noopener"
                  onClick={(e) => e.stopPropagation()}
                >
                  JPL SBDB
                </a>
              </div>
            </div>
          );
        })}
      {a.state === 'ok' && !name && (
        <div class="pager">
          <button class="pg" disabled={page === 0} onClick={() => setPage(page - 1)}>
            ‹
          </button>
          <span class="cnt" style="margin:0 auto">
            Page {page + 1} of {pages.toLocaleString('en-US')} · {a.page.count.toLocaleString('en-US')} {kind === 'a' ? 'asteroids' : 'comets'}
          </span>
          <button class="pg" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>
            ›
          </button>
        </div>
      )}
    </div>
  );
}
