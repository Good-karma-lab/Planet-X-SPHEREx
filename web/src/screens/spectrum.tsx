import { useEffect, useRef, useState } from 'preact/hooks';
import { formatDec, formatRA } from '../lib/sky';

export interface Source {
  ra: number;
  dec: number;
  /** a catalogue object: its name and proper motion (mas/yr, J2000 place) */
  name?: string;
  pmra?: number;
  pmdec?: number;
}

export interface SpecPoint {
  i: number;
  mjd: number;
  det: number;
  lam: number;
  ok: boolean;
  why: string;
  flux?: number;
  err?: number;
}

interface SpecView {
  key: string;
  state: 'running' | 'done' | 'failed';
  error: string;
  total: number;
  measured: number;
  usable: number;
  saturated: number;
  no_spectrum: boolean;
  points: SpecPoint[];
}

const DET_HUE = [270, 220, 175, 95, 40, 5];

/** Follows one spectrum as it is measured: the points arrive in pieces (`since` = points already held). */
export function useSpectrum(src: Source | null): {
  view: SpecView | null;
  points: SpecPoint[];
  error: string;
} {
  const [view, setView] = useState<SpecView | null>(null);
  const [points, setPoints] = useState<SpecPoint[]>([]);
  const [error, setError] = useState('');
  useEffect(() => {
    setView(null);
    setPoints([]);
    setError('');
    if (!src) return;
    let stop = false;
    let held: SpecPoint[] = [];
    const q = new URLSearchParams({
      ra: src.ra.toFixed(6),
      dec: src.dec.toFixed(6),
    });
    if (src.pmra || src.pmdec) {
      q.set('pmra', String(src.pmra ?? 0));
      q.set('pmdec', String(src.pmdec ?? 0));
    }
    const poll = async () => {
      while (!stop) {
        try {
          const r = await fetch(`/api/v1/spectrum?${q}&since=${held.length}`, {
            cache: 'no-store',
          });
          if (r.status === 429) {
            setError('Too many new measurements from this address; trying again shortly');
            await new Promise((res) => setTimeout(res, 5000));
            continue;
          }
          if (!r.ok) throw new Error(`service answered ${r.status}`);
          const v = (await r.json()) as SpecView;
          if (stop) return;
          held = [...held, ...v.points];
          setView(v);
          setPoints(held);
          setError(v.state === 'failed' ? v.error : '');
          if (v.state !== 'running') {
            // the finished spectrum once more in full: points that deviate from their neighbours are flagged only at the end
            const all = (await fetch(`/api/v1/spectrum?${q}&since=0`, {
              cache: 'no-store',
            }).then((x) => x.json())) as SpecView;
            if (!stop) setPoints(all.points);
            return;
          }
        } catch (e) {
          setError(String(e));
        }
        await new Promise((res) => setTimeout(res, 700));
      }
    };
    void poll();
    return () => {
      stop = true;
    };
  }, [src?.ra, src?.dec, src?.pmra, src?.pmdec]);
  return { view, points, error };
}

function SpectrumChart({ points }: { points: SpecPoint[] }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    const dpr = Math.min(2, devicePixelRatio || 1);
    const W = cv.clientWidth;
    const H = cv.clientHeight;
    cv.width = W * dpr;
    cv.height = H * dpr;
    const c = cv.getContext('2d')!;
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.clearRect(0, 0, W, H);
    const ok = points.filter((p) => p.ok && p.flux != null);
    const L = 42;
    const R = 8;
    const T = 8;
    const B = 26;
    const x = (lam: number) => L + ((lam - 0.7) / (5.05 - 0.7)) * (W - L - R);
    let lo = 0;
    let hi = 1;
    if (ok.length) {
      const f = ok.map((p) => p.flux!).sort((a, b) => a - b);
      lo = Math.min(0, f[Math.floor(0.01 * (f.length - 1))]!);
      hi = f[Math.floor(0.99 * (f.length - 1))]! * 1.15;
      if (hi <= lo) hi = lo + 1;
    }
    const y = (v: number) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
    c.strokeStyle = 'rgba(140,225,240,0.18)';
    c.fillStyle = 'rgba(160,185,194,0.9)';
    c.font = '500 10.5px ui-monospace, Menlo, monospace';
    c.lineWidth = 1;
    for (let lam = 1; lam <= 5; lam++) {
      c.beginPath();
      c.moveTo(x(lam), T);
      c.lineTo(x(lam), H - B);
      c.stroke();
      c.fillText(`${lam}`, x(lam) - 3, H - B + 13);
    }
    c.fillText('µm', W - R - 14, H - 4);
    const step = niceStep((hi - lo) / 4);
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
      c.beginPath();
      c.moveTo(L, y(v));
      c.lineTo(W - R, y(v));
      c.stroke();
      c.fillText(fmt(v), 2, y(v) + 3);
    }
    c.fillText('mJy', 2, T + 8);
    if (lo < 0) {
      c.strokeStyle = 'rgba(214,231,237,0.35)';
      c.beginPath();
      c.moveTo(L, y(0));
      c.lineTo(W - R, y(0));
      c.stroke();
    }
    for (const p of ok) {
      const col = `hsl(${DET_HUE[p.det - 1]},80%,62%)`;
      c.strokeStyle = col;
      c.globalAlpha = 0.45;
      c.beginPath();
      c.moveTo(x(p.lam), y(p.flux! - (p.err ?? 0)));
      c.lineTo(x(p.lam), y(p.flux! + (p.err ?? 0)));
      c.stroke();
      c.globalAlpha = 1;
      c.fillStyle = col;
      c.beginPath();
      c.arc(x(p.lam), y(p.flux!), 2.2, 0, 7);
      c.fill();
    }
    const last = ok[ok.length - 1];
    if (last) {
      c.strokeStyle = '#ffffff';
      c.lineWidth = 1.4;
      c.beginPath();
      c.arc(x(last.lam), y(last.flux!), 5.5, 0, 7);
      c.stroke();
    }
  }, [points]);
  return <canvas ref={ref} class="spec-chart" aria-label="Spectrum: flux against wavelength" />;
}

function niceStep(x: number): number {
  const e = 10 ** Math.floor(Math.log10(Math.max(x, 1e-9)));
  const m = x / e;
  return (m < 1.5 ? 1 : m < 3.5 ? 2 : m < 7.5 ? 5 : 10) * e;
}
function fmt(v: number): string {
  const a = Math.abs(v);
  return a >= 100 ? v.toFixed(0) : a >= 10 ? v.toFixed(0) : a >= 1 ? v.toFixed(1) : v.toFixed(2);
}

function csv(src: Source, points: SpecPoint[]): string {
  const head = `# SPHEREx Level-2 PSF photometry at RA ${src.ra.toFixed(6)} Dec ${src.dec.toFixed(6)}${src.pmra || src.pmdec ? ` (proper motion ${src.pmra} ${src.pmdec} mas/yr)` : ''}\n# i,mjd,detector,wavelength_um,flux_mJy,error_mJy,used,reason\n`;
  return head + points.map((p) => [p.i, p.mjd, p.det, p.lam, p.flux ?? '', p.err ?? '', p.ok ? 1 : 0, p.why].join(',')).join('\n') + '\n';
}

export function SpectrumTab({ src }: { src: Source | null }) {
  const { view, points, error } = useSpectrum(src);
  if (!src) return <div class="mono-s">Click a source in the picture</div>;
  const why = new Map<string, number>();
  for (const p of points) if (!p.ok) why.set(p.why, (why.get(p.why) ?? 0) + 1);
  const perDet = [1, 2, 3, 4, 5, 6].map((d) => points.filter((p) => p.ok && p.det === d).length);
  const usable = points.filter((p) => p.ok).length;
  return (
    <div class="spec">
      <div class="spec-head">
        <b>{src.name ?? 'Source'}</b>
        <span class="mono-s">
          {formatRA(src.ra)} {formatDec(src.dec)}
        </span>
      </div>
      <div class="spec-state">
        {view
          ? view.state === 'running'
            ? `Measuring: ${view.measured} of ${view.total} exposures · ${usable} used`
            : view.state === 'done'
              ? `${usable} points from ${view.total} exposures`
              : `Failed: ${view.error}`
          : 'Finding the exposures of this place…'}
      </div>
      {view?.no_spectrum && <div class="partial">Saturated in most exposures: no SPHEREx spectrum</div>}
      <SpectrumChart points={points} />
      <div class="spec-dets">
        {perDet.map((n, i) => (
          <span key={i}>
            <span class="sw" style={{ background: `hsl(${DET_HUE[i]},80%,62%)` }} />D{i + 1} {n}
          </span>
        ))}
      </div>
      {why.size > 0 && <div class="mono-s">Not used: {[...why.entries()].map(([k, n]) => `${n} ${k}`).join(' · ')}</div>}
      {error && (
        <div class="mono-s" style={{ color: 'var(--amber)' }}>
          {error}
        </div>
      )}
      {view?.state === 'done' && points.length > 0 && (
        <a
          class="lnk"
          download={`spectrum-${formatRA(src.ra, true)}${formatDec(src.dec, true)}.csv`}
          href={`data:text/csv;charset=utf-8,${encodeURIComponent(csv(src, points))}`}
        >
          Download the points (CSV)
        </a>
      )}
    </div>
  );
}

interface Identified {
  simbad?: { state: string; name?: string; type?: string; sp?: string | null; sep?: number; error?: string };
  gaia?: {
    state: string;
    source_id?: string;
    g?: number | null;
    bp_rp?: number | null;
    parallax?: number | null;
    ruwe?: number | null;
    error?: string;
  };
}

/** What SIMBAD and Gaia DR3 call a place, asked once per selection. */
function useIdentify(src: Source | null): Identified | null | 'asking' {
  const [v, setV] = useState<Identified | null | 'asking'>(null);
  useEffect(() => {
    setV(null);
    if (!src) return;
    setV('asking');
    const ctl = new AbortController();
    const q = new URLSearchParams({ ra: src.ra.toFixed(6), dec: src.dec.toFixed(6) });
    if (src.pmra || src.pmdec) {
      q.set('pmra', String(src.pmra ?? 0));
      q.set('pmdec', String(src.pmdec ?? 0));
    }
    fetch(`/api/v1/identify?${q}`, { signal: ctl.signal })
      .then((r) => (r.ok ? r.json() : null))
      .then((d: Identified | null) => setV(d))
      .catch(() => {});
    return () => ctl.abort();
  }, [src?.ra, src?.dec, src?.pmra, src?.pmdec]);
  return v;
}

export function ObjectTab({ src, onSpectrum }: { src: Source | null; onSpectrum: () => void }) {
  const id = useIdentify(src);
  if (!src) return <div class="mono-s">Click a source in the picture</div>;
  const pm = src.pmra || src.pmdec ? Math.hypot(src.pmra ?? 0, src.pmdec ?? 0) : 0;
  const sb = id && id !== 'asking' ? id.simbad : undefined;
  const ga = id && id !== 'asking' ? id.gaia : undefined;
  const plx = ga?.state === 'ok' && ga.parallax != null && ga.parallax >= 3 && (ga.ruwe ?? 9) <= 1.4 ? ga.parallax : null;
  return (
    <div class="okv">
      <div class="orow">
        <span class="faint">Name</span>
        <span>{src.name ?? (sb?.state === 'ok' ? sb.name : id === 'asking' ? 'asking SIMBAD…' : 'no catalogue name here')}</span>
      </div>
      <div class="orow">
        <span class="faint">Position</span>
        <span class="mono-s">
          {formatRA(src.ra)} {formatDec(src.dec)}
          {src.pmra || src.pmdec ? ' (J2000)' : ''}
        </span>
      </div>
      {pm > 0 && (
        <div class="orow">
          <span class="faint">Proper motion</span>
          <span>
            {pm >= 1000 ? `${(pm / 1000).toFixed(2)} ″/yr` : `${pm.toFixed(0)} mas/yr`}; measured at its place on each exposure's date
          </span>
        </div>
      )}
      <div class="orow">
        <span class="faint">SIMBAD</span>
        <span>
          {id === 'asking'
            ? 'asking…'
            : sb?.state === 'ok'
              ? `${sb.name}${sb.type ? ` · ${sb.type}` : ''}${sb.sp ? ` · ${sb.sp}` : ''} (${sb.sep}″)`
              : sb?.state === 'none'
                ? 'nothing within 6″'
                : 'not reachable'}
        </span>
      </div>
      <div class="orow">
        <span class="faint">Gaia DR3</span>
        <span>
          {id === 'asking'
            ? 'asking…'
            : ga?.state === 'ok'
              ? `${ga.source_id}${ga.g != null ? ` · G ${ga.g}` : ''}${ga.bp_rp != null ? ` · BP−RP ${ga.bp_rp}` : ''}${ga.parallax != null ? ` · parallax ${ga.parallax} mas` : ''}${ga.ruwe != null ? ` · RUWE ${ga.ruwe}` : ''}`
              : ga?.state === 'none'
                ? 'nothing within 6″'
                : 'not reachable'}
        </span>
      </div>
      {plx !== null && (
        <div class="orow">
          <span class="faint">Distance</span>
          <span>
            {(1000 / plx).toFixed(1)} pc · {((1000 / plx) * 3.2616).toFixed(1)} light-years (Gaia parallax)
          </span>
        </div>
      )}
      <div class="orow">
        <span class="faint">Spectrum</span>
        <span>
          <button class="lnk" onClick={onSpectrum}>
            Measure in every exposure
          </button>
        </span>
      </div>
    </div>
  );
}

export interface Anomaly {
  n: number;
  ra: number;
  dec: number;
  pmra: number;
  pmdec: number;
  moves: boolean;
  snr: number | null;
  score: number;
  points: number;
  drivers: { lo: number; hi: number; z: number; share: number }[];
  shape: (number | null)[];
  shape_err: (number | null)[];
  model: number[];
  resid_z: (number | null)[];
  identified?: {
    simbad?: { state: string; name?: string; type?: string; sp?: string | null; sep?: number };
    gaia?: { state: string; source_id?: string; g?: number | null; bp_rp?: number | null; parallax?: number | null; ruwe?: number | null };
  };
}

export interface AnomalyField {
  sources: number;
  with_spectrum: number;
  median: number | null;
  scatter: number | null;
  threshold: number | null;
  reason: string;
  bins: number[];
  edges: number[];
  scores: number[];
  anomalies: Anomaly[];
  exposures: number;
  seconds: number;
}

/** The driving bins in words: "excess at 1.51–1.66 µm (+27σ, 31 % of the misfit)". */
export function driverWords(a: Anomaly): string[] {
  return a.drivers.map(
    (d) =>
      `${d.z > 0 ? 'excess' : 'deficit'} at ${d.lo.toFixed(2)}–${d.hi.toFixed(2)} µm (${d.z > 0 ? '+' : '−'}${Math.abs(d.z).toFixed(0)}σ, ${Math.round(d.share * 100)} % of the misfit)`,
  );
}

function PassportChart({ a, f }: { a: Anomaly; f: AnomalyField }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    const dpr = Math.min(2, devicePixelRatio || 1);
    const W = cv.clientWidth;
    const H = cv.clientHeight;
    cv.width = W * dpr;
    cv.height = H * dpr;
    const c = cv.getContext('2d')!;
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.clearRect(0, 0, W, H);
    const L = 34;
    const R = 8;
    const T = 8;
    const B = 22;
    const lx = (lam: number) => L + ((Math.log(lam) - Math.log(0.75)) / (Math.log(5) - Math.log(0.75))) * (W - L - R);
    const vals = [...a.model, ...a.shape.filter((v): v is number => v != null)];
    const hi = Math.max(...vals) * 1.15;
    const lo = Math.min(0, ...vals);
    const y = (v: number) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
    c.strokeStyle = 'rgba(140,225,240,0.18)';
    c.fillStyle = 'rgba(160,185,194,0.9)';
    c.font = '500 10px ui-monospace, Menlo, monospace';
    for (const lam of [1, 2, 3, 4, 5]) {
      c.beginPath();
      c.moveTo(lx(lam), T);
      c.lineTo(lx(lam), H - B);
      c.stroke();
      c.fillText(String(lam), lx(lam) - 3, H - B + 12);
    }
    c.fillText('µm', W - R - 14, H - 3);
    // the misfit's drivers, shaded
    for (const d of a.drivers) {
      c.fillStyle = d.z > 0 ? 'rgba(255,90,168,0.16)' : 'rgba(53,216,238,0.16)';
      c.fillRect(lx(d.lo), T, lx(d.hi) - lx(d.lo), H - T - B);
    }
    // the ordinary star of the field, fitted to this source
    c.strokeStyle = 'rgba(214,231,237,0.85)';
    c.lineWidth = 1.5;
    c.beginPath();
    a.model.forEach((v, i) => (i ? c.lineTo(lx(f.bins[i]!), y(v)) : c.moveTo(lx(f.bins[i]!), y(v))));
    c.stroke();
    c.fillStyle = '#ffb454';
    c.strokeStyle = 'rgba(255,180,84,0.6)';
    a.shape.forEach((v, i) => {
      if (v == null) return;
      const e = a.shape_err[i] ?? 0;
      const x = lx(f.bins[i]!);
      c.beginPath();
      c.moveTo(x, y(v - e));
      c.lineTo(x, y(v + e));
      c.stroke();
      c.beginPath();
      c.arc(x, y(v), 2.6, 0, 7);
      c.fill();
    });
  }, [a, f]);
  return <canvas ref={ref} class="spec-chart small" aria-label="The source's spectrum shape against the ordinary star of the field" />;
}

export function Passport({ a, f }: { a: Anomaly; f: AnomalyField }) {
  const sb = a.identified?.simbad;
  const ga = a.identified?.gaia;
  return (
    <div class="passport">
      <div class="spec-head">
        <b>Anomaly {a.n}</b>
        <span class="mono-s">
          score {a.score.toFixed(2)} · threshold {f.threshold?.toFixed(2)} (field median {f.median?.toFixed(2)})
        </span>
      </div>
      <ul class="drivers">
        {driverWords(a).map((w) => (
          <li key={w}>{w}</li>
        ))}
      </ul>
      <PassportChart a={a} f={f} />
      <div class="mono-s">
        <span style={{ color: '#ffb454' }}>●</span> this source (shape, f / Σf) · <span style={{ color: '#d6e7ed' }}>—</span> the ordinary
        star of the field, fitted to it
      </div>
      <div class="okv">
        <div class="orow">
          <span class="faint">Moves</span>
          <span>
            {a.moves
              ? `yes: ${(Math.hypot(a.pmra, a.pmdec) / 1000).toFixed(2)} ″/yr (search between passes)`
              : 'no (stationary over the passes)'}
          </span>
        </div>
        <div class="orow">
          <span class="faint">SIMBAD</span>
          <span>
            {sb?.state === 'ok'
              ? `${sb.name}${sb.type ? ` · ${sb.type}` : ''}${sb.sp ? ` · ${sb.sp}` : ''} (${sb.sep}″)`
              : sb?.state === 'none'
                ? 'nothing within 6″'
                : 'not reachable'}
          </span>
        </div>
        <div class="orow">
          <span class="faint">Gaia DR3</span>
          <span>
            {ga?.state === 'ok'
              ? `${ga.source_id}${ga.g != null ? ` · G ${ga.g}` : ''}${ga.bp_rp != null ? ` · BP−RP ${ga.bp_rp}` : ''}${ga.parallax != null ? ` · parallax ${ga.parallax} mas` : ''}${ga.ruwe != null ? ` · RUWE ${ga.ruwe}` : ''}`
              : ga?.state === 'none'
                ? 'nothing within 6″'
                : 'not reachable'}
          </span>
        </div>
        <div class="orow">
          <span class="faint">Points</span>
          <span>
            {a.points} measurements{a.snr != null ? ` · S/N ${a.snr} in the white stack` : ''}
          </span>
        </div>
      </div>
    </div>
  );
}
