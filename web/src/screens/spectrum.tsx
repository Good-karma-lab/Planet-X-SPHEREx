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

export function ObjectTab({ src, onSpectrum }: { src: Source | null; onSpectrum: () => void }) {
  if (!src) return <div class="mono-s">Click a source in the picture</div>;
  const pm = src.pmra || src.pmdec ? Math.hypot(src.pmra ?? 0, src.pmdec ?? 0) : 0;
  return (
    <div class="okv">
      <div class="orow">
        <span class="faint">Name</span>
        <span>{src.name ?? 'no catalogue name here'}</span>
      </div>
      <div class="orow">
        <span class="faint">Position</span>
        <span class="mono-s">
          {formatRA(src.ra)} {formatDec(src.dec)}
          {src.name ? ' (J2000)' : ''}
        </span>
      </div>
      {pm > 0 && (
        <div class="orow">
          <span class="faint">Proper motion</span>
          <span>
            {pm >= 1000 ? `${(pm / 1000).toFixed(2)} ″/yr` : `${pm.toFixed(0)} mas/yr`} (SIMBAD); measured at its place on each exposure's
            date
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
