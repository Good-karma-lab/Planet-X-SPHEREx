import { useEffect, useRef, useState } from 'preact/hooks';
import type { Source } from './spectrum';

interface Part<P> {
  state: 'ok' | 'none' | 'unavailable';
  error?: string;
  points: P[];
}
interface ZtfPoint {
  mjd: number;
  mag: number;
  err: number;
  band: string;
}
interface NeowisePoint {
  mjd: number;
  w1: number | null;
  w2: number | null;
}
interface Curves {
  ztf: Part<ZtfPoint>;
  neowise: Part<NeowisePoint>;
}

const year = (mjd: number) => 2000 + (mjd - 51544.5) / 365.25;
const COLOURS: Record<string, string> = { zg: '#6fdc8c', zr: '#ff7b7b', zi: '#c99bff', W1: '#ffb454', W2: '#ff6b9e' };

function Chart({ series, label }: { series: { name: string; pts: [number, number][] }[]; label: string }) {
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
    const all = series.flatMap((s) => s.pts);
    if (!all.length) return;
    const xs = all.map((p) => p[0]);
    const ys = all.map((p) => p[1]).sort((a, b) => a - b);
    const x0 = Math.min(...xs, 2025) - 0.3;
    const x1 = Math.max(...xs, 2026.7) + 0.3;
    const y0 = ys[Math.floor(0.01 * (ys.length - 1))]! - 0.15;
    const y1 = ys[Math.floor(0.99 * (ys.length - 1))]! + 0.15;
    const L = 34;
    const B = 18;
    const X = (v: number) => L + ((v - x0) / (x1 - x0)) * (W - L - 6);
    const Y = (m: number) => 6 + ((m - y0) / (y1 - y0)) * (H - B - 6); // brighter up
    // the SPHEREx years
    c.fillStyle = 'rgba(95,227,214,0.10)';
    c.fillRect(X(2025.3), 6, X(2026.8) - X(2025.3), H - B - 6);
    c.fillStyle = 'rgba(160,185,194,0.9)';
    c.font = '500 10px ui-monospace, Menlo, monospace';
    for (let yr = Math.ceil(x0); yr <= x1; yr += Math.max(1, Math.round((x1 - x0) / 6))) c.fillText(String(yr), X(yr) - 12, H - 4);
    c.fillText(y0.toFixed(1), 2, 14);
    c.fillText(y1.toFixed(1), 2, H - B);
    c.fillText(label, L + 4, 16);
    for (const s of series) {
      c.fillStyle = COLOURS[s.name] ?? '#fff';
      for (const [x, y] of s.pts) {
        c.beginPath();
        c.arc(X(x), Y(y), 1.6, 0, 7);
        c.fill();
      }
    }
  }, [series]);
  return <canvas ref={ref} class="lc-chart" />;
}

/** ZTF and NEOWISE light curves of the selected source (IRSA), with the SPHEREx years shaded. */
export function LightCurves({ src }: { src: Source }) {
  const [c, setC] = useState<Curves | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setC(null);
    setBusy(true);
    const ctl = new AbortController();
    const q = new URLSearchParams({ ra: src.ra.toFixed(6), dec: src.dec.toFixed(6) });
    if (src.pmra || src.pmdec) {
      q.set('pmra', String(src.pmra ?? 0));
      q.set('pmdec', String(src.pmdec ?? 0));
    }
    fetch(`/api/v1/lightcurves?${q}`, { signal: ctl.signal })
      .then((r) => (r.ok ? r.json() : null))
      .then((d: Curves | null) => {
        setC(d);
        setBusy(false);
      })
      .catch(() => setBusy(false));
    return () => ctl.abort();
  }, [src.ra, src.dec, src.pmra, src.pmdec]);
  const say = (p: Part<unknown> | undefined, what: string) =>
    !p
      ? ''
      : p.state === 'none'
        ? `${what}: no source within its match radius`
        : p.state === 'unavailable'
          ? `${what}: IRSA did not answer`
          : '';
  return (
    <div class="lc">
      <div class="h2">Light curves</div>
      {busy && <div class="mono-s">asking IRSA for ZTF and NEOWISE…</div>}
      {c?.ztf.state === 'ok' && (
        <Chart
          label="ZTF (optical, mag)"
          series={['zg', 'zr', 'zi'].map((b) => ({
            name: b,
            pts: c.ztf.points.filter((p) => p.band === b).map((p) => [year(p.mjd), p.mag] as [number, number]),
          }))}
        />
      )}
      {c?.neowise.state === 'ok' && (
        <Chart
          label="NEOWISE (W1 3.4 µm, W2 4.6 µm, mag)"
          series={[
            { name: 'W1', pts: c.neowise.points.filter((p) => p.w1 != null).map((p) => [year(p.mjd), p.w1!] as [number, number]) },
            { name: 'W2', pts: c.neowise.points.filter((p) => p.w2 != null).map((p) => [year(p.mjd), p.w2!] as [number, number]) },
          ]}
        />
      )}
      {c && (
        <div class="mono-s">
          {[say(c.ztf, 'ZTF'), say(c.neowise, 'NEOWISE')].filter(Boolean).join(' · ')}
          {c.ztf.state === 'ok' || c.neowise.state === 'ok' ? ' · shaded: the SPHEREx years' : ''}
        </div>
      )}
    </div>
  );
}
