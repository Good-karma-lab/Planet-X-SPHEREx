import { useEffect, useRef, useState } from 'preact/hooks';
import { honestLines, progressText, runJob, useProduct } from './jobs';
import './search.css';

interface LinkPoint {
  key: string;
  i: number;
  sn: number | null;
  phot_sn: number | null;
  d: number;
  d_lo: number;
  d_hi: number;
  ra: number;
  dec: number;
  flux_mjy: number | null;
}

interface OrbitFit {
  rms: number;
  dist: number;
  dist_err: number;
  a: number;
  e: number;
}

export interface LinkEntry {
  a: LinkPoint;
  b: LinkPoint;
  c?: LinkPoint | null;
  one_body: boolean;
  two_passes?: OrbitFit;
  three_passes?: OrbitFit;
}

export interface LinkResult {
  status: string;
  notes?: string[];
  passes: { key: string; t0: number; candidates: number }[];
  links: LinkEntry[];
  best?: {
    passes: number;
    distance_au: number;
    distance_err_au: number;
    a_au: number;
    e: number;
    misfit_arcsec: number;
    confirmed: boolean;
  };
  tolerance_arcsec?: number;
  honest?: unknown;
}

type Ask = { state: 'idle' } | { state: 'running'; text: string; frac: number | null } | { state: 'none' | 'error'; text: string } | { state: 'ok'; r: LinkResult };

const ut = (m: number) => new Date((m - 40587) * 864e5).toISOString().slice(0, 10);

/**
 * Link the one-pass results of a field by ONE orbit: between two passes a distant body jumps by twice its parallax, so two candidates belong together if one heliocentric
 * orbit passes through both tracks; the fit gives the distance. `keys`: the finished one-pass results of this field (two or three, from useDistant().keys).
 */
export function LinkPanel({ keys, onPick }: { keys: string[]; onPick?: (e: LinkEntry) => void }) {
  const has = useProduct('link');
  const [a, setA] = useState<Ask>({ state: 'idle' });
  const tok = useRef(0);
  const sig = keys.slice().sort().join(',');
  useEffect(() => {
    tok.current++;
    setA({ state: 'idle' });
  }, [sig]);
  if (has !== true || keys.length < 2) return null;
  const run = async () => {
    const my = ++tok.current;
    const t0 = Date.now();
    setA({ state: 'running', text: 'linking…', frac: null });
    try {
      const j = await runJob(`/api/v1/link?keys=${keys.slice(0, 3).join(',')}`, {
        cancelled: () => my !== tok.current,
        every: 2000,
        onJob: (jj) => {
          const p = progressText(jj, t0);
          if (my === tok.current) setA({ state: 'running', text: p.text, frac: p.frac });
        },
      });
      if (my !== tok.current) return;
      if (!j) return setA({ state: 'none', text: 'The service does not link the passes yet.' });
      const r = j.result as unknown as LinkResult;
      setA(r?.status === 'ok' ? { state: 'ok', r } : { state: 'none', text: (r?.notes ?? []).join(' ') || `no link (${r?.status ?? 'no answer'})` });
    } catch (e) {
      if ((e as Error).message !== 'cancelled') setA({ state: 'error', text: (e as Error).message });
    }
  };
  return (
    <div class="dsbox">
      <div class="srrow" title="Two candidates of different passes belong together if one orbit passes through both; the fit gives the distance.">
        <span class="srt">Find Planet X · link of the passes</span>
        {(a.state === 'idle' || a.state === 'none' || a.state === 'error') && (
          <button class="btn sm" onClick={() => void run()}>
            Link {keys.length} passes
          </button>
        )}
      </div>
      {a.state === 'running' && (
        <>
          <div class="mono-s">{a.text}</div>
          <div class={`ds-prog${a.frac == null ? ' indet' : ''}`}>
            <i style={a.frac == null ? '' : `width:${Math.round(a.frac * 100)}%`} />
          </div>
        </>
      )}
      {(a.state === 'none' || a.state === 'error') && <div class="mono-s">{a.state === 'none' ? a.text : `The link failed: ${a.text}`}</div>}
      {a.state === 'ok' && <LinkCard r={a.r} onPick={onPick} />}
    </div>
  );
}

/** The result of a link: the best orbit in words (distance with its error, passes, misfit, shape) and the linked pairs of candidates. */
export function LinkCard({ r, onPick }: { r: LinkResult; onPick?: (e: LinkEntry) => void }) {
  const b = r.best;
  return (
    <div class="linkcard">
      {b ? (
        <>
          <div class="ft-line">
            one orbit through {b.passes} passes · distance {b.distance_au.toFixed(1)} ± {b.distance_err_au.toFixed(1)} au
          </div>
          <div class="mono-s">
            misfit {b.misfit_arcsec.toFixed(1)}″ · a {b.a_au.toFixed(0)} au · e {b.e.toFixed(2)} · {b.confirmed ? 'confirmed in photometry' : 'not confirmed in photometry: a lead, not a discovery'}
          </div>
        </>
      ) : (
        <div class="mono-s">no orbit links the candidates within {r.tolerance_arcsec ?? '?'}″</div>
      )}
      <div class="mono-s">{r.passes.map((p, i) => `pass ${i + 1} (${ut(p.t0)}): ${p.candidates} candidate${p.candidates === 1 ? '' : 's'}`).join(' · ')}</div>
      {r.links.slice(0, 6).map((l, i) => {
        const fit = l.three_passes ?? l.two_passes;
        return (
          <button key={i} class="btn sm leadbtn" onClick={() => onPick?.(l)}>
            <b>Link {i + 1}</b> · {l.c ? '3' : '2'} passes{fit ? ` · ${fit.dist.toFixed(1)} ± ${fit.dist_err.toFixed(1)} au · misfit ${fit.rms.toFixed(1)}″` : ''}
            {l.one_body ? '' : ' · not one body'}
          </button>
        );
      })}
      {honestLines(r.honest).map((t) => (
        <div class="mono-s" key={t}>
          {t}
        </div>
      ))}
    </div>
  );
}

/** The line of places of a candidate of one pass at the time of another pass, by distance, and the candidates of other results within a corridor of it. */
export interface Jump {
  t1: number;
  t2: number;
  line: { d: number; ra: number; dec: number }[];
  corridor_arcmin: number;
  line_length_arcmin: number;
  in_corridor: { ra?: number; dec?: number; key?: string; i?: number; off_line_arcmin: number; line_distance_au: number; own_distance_in_line_interval: boolean; sn?: number }[];
  honest?: unknown;
}

/**
 * The jump view: where a candidate of pass 1 is in pass 2 as a line of places by distance (the nearer, the farther it jumps), with the candidates of the other
 * one-pass results inside the corridor around the line. `distKey`: the key of the one-pass result; `i`: the candidate; `mjd`: the time of the other pass.
 */
export function JumpView({ distKey, i, mjd, corridor = 5 }: { distKey: string; i: number; mjd: number; corridor?: number }) {
  const has = useProduct('link');
  const [j, setJ] = useState<Jump | null | 'error' | 'asking'>('asking');
  const cv = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    if (has !== true) return;
    setJ('asking');
    const ctl = new AbortController();
    fetch(`/api/v1/jump?key=${distKey}&i=${i}&mjd=${mjd}&corridor=${corridor}`, { signal: ctl.signal })
      .then((r) => (r.ok ? (r.json() as Promise<Jump>) : Promise.reject(new Error(String(r.status)))))
      .then(setJ)
      .catch(() => {
        if (!ctl.signal.aborted) setJ('error');
      });
    return () => ctl.abort();
  }, [has, distKey, i, mjd, corridor]);
  useEffect(() => {
    if (!cv.current || !j || j === 'error' || j === 'asking') return;
    const c = cv.current;
    const dpr = Math.min(2, devicePixelRatio || 1);
    const W = c.clientWidth;
    const H = c.clientHeight;
    c.width = W * dpr;
    c.height = H * dpr;
    const g = c.getContext('2d')!;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);
    const cd = Math.cos((j.line[0]!.dec * Math.PI) / 180);
    const pts = j.line.map((p) => [p.ra * cd, p.dec] as const);
    const extra = j.in_corridor.filter((q) => q.ra != null && q.dec != null).map((q) => [q.ra! * cd, q.dec!] as const);
    const all = pts.concat(extra);
    const xs = all.map((p) => p[0]);
    const ys = all.map((p) => p[1]);
    const pad = 18;
    const span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys), 1e-4);
    const s = (Math.min(W, H) - 2 * pad) / span;
    const X = (x: number) => W / 2 - (x - (Math.min(...xs) + Math.max(...xs)) / 2) * s; // east left
    const Y = (y: number) => H / 2 - (y - (Math.min(...ys) + Math.max(...ys)) / 2) * s;
    // the corridor around the line
    g.strokeStyle = 'rgba(95,227,214,.18)';
    g.lineWidth = Math.max(2, (j.corridor_arcmin / 60) * s * 2);
    g.lineCap = 'round';
    g.beginPath();
    pts.forEach((p, k) => (k ? g.lineTo(X(p[0]), Y(p[1])) : g.moveTo(X(p[0]), Y(p[1]))));
    g.stroke();
    g.strokeStyle = '#ffb454';
    g.lineWidth = 1.6;
    g.beginPath();
    pts.forEach((p, k) => (k ? g.lineTo(X(p[0]), Y(p[1])) : g.moveTo(X(p[0]), Y(p[1]))));
    g.stroke();
    g.font = '600 10.5px ui-monospace, Menlo, monospace';
    g.fillStyle = '#d6e7ed';
    let lx = -1e9;
    let ly = -1e9;
    j.line.forEach((p, k) => {
      const px = X(pts[k]![0]);
      const py = Y(pts[k]![1]);
      if (k % 4 && k !== j.line.length - 1) return;
      g.fillStyle = '#ffb454';
      g.beginPath();
      g.arc(px, py, 3, 0, 7);
      g.fill();
      if (Math.hypot(px - lx, py - ly) < 46) return; // a label only where it does not run into the previous one
      lx = px;
      ly = py;
      g.fillStyle = '#d6e7ed';
      g.fillText(`${Math.round(p.d)} au`, px + 6, py - 4);
    });
    g.fillStyle = '#6aff9e';
    extra.forEach((p) => {
      g.beginPath();
      g.arc(X(p[0]), Y(p[1]), 4, 0, 7);
      g.fill();
    });
  }, [j]);
  if (has !== true) return null;
  return (
    <div class="dsbox">
      <div class="search-h">Find Planet X · where it is in the other pass</div>
      {j === 'asking' && <div class="mono-s">asking the service…</div>}
      {j === 'error' && <div class="mono-s">The service could not draw the line of places.</div>}
      {j && j !== 'asking' && j !== 'error' && (
        <>
          <canvas ref={cv} class="jump-cv" aria-label="The line of places of the candidate in the other pass, by distance" />
          <div class="mono-s">
            {ut(j.t1)} → {ut(j.t2)} · the line is {j.line_length_arcmin}′ long (distance {j.line[0]!.d.toFixed(0)}–{j.line[j.line.length - 1]!.d.toFixed(0)} au) · corridor ±{j.corridor_arcmin}′
          </div>
          {j.in_corridor.length === 0 && <div class="mono-s">no candidate of a result already made lies in the corridor</div>}
          {j.in_corridor.map((q, k) => (
            <div class="mono-s" key={k}>
              <b>{q.off_line_arcmin}′</b> off the line · at {q.line_distance_au.toFixed(0)} au on it{q.own_distance_in_line_interval ? ' · its own distance fits' : ' · its own distance does not fit'}
            </div>
          ))}
          {honestLines(j.honest).map((t) => (
            <div class="mono-s" key={t}>
              {t}
            </div>
          ))}
        </>
      )}
    </div>
  );
}
