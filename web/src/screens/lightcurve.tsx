import { useEffect, useRef, useState } from 'preact/hooks';
import { brightnessLine } from './brightness-line';
import type { Source } from './spectrum';

interface Part<P> {
  state: 'ok' | 'none' | 'unavailable' | 'pending';
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
/** GET /api/v1/brightness: the ring photometry of a (saturated) star in every pass and detector of a finished field. */
interface Brightness {
  state: string;
  error?: string;
  result?: {
    passes: { index: number; mjd: number; detectors: Record<string, { flux_mjy: number; err_mjy: number; ratio_to_first: number | null; ratio_err: number | null } | null> }[];
    control: { stars: number; scatter: Record<string, number> };
  };
}
interface Curves {
  ztf: Part<ZtfPoint>;
  neowise: Part<NeowisePoint>;
}

const year = (mjd: number) => 2000 + (mjd - 51544.5) / 365.25;
const DET_COLOURS: Record<string, string> = { D1: '#b36cff', D2: '#5f7bff', D3: '#5fe3d6', D4: '#8ee26b', D5: '#ffc24d', D6: '#ff6b6b' };
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

const dmag = (ratio: number) => -2.5 * Math.log10(ratio);

/** SPHEREx's own points: the brightness change since pass 1 (mag, brighter up) per pass and detector, with errors, over the ZTF curve (relative to its level near pass 1) in grey. */
function SpherexChart({ b, ztf }: { b: NonNullable<Brightness['result']>; ztf: ZtfPoint[] }) {
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
    const pts: { x: number; y: number; e: number; d: string }[] = [];
    for (const p of b.passes)
      for (const [d, v] of Object.entries(p.detectors))
        if (v && v.ratio_to_first && v.ratio_to_first > 0)
          pts.push({ x: year(p.mjd), y: dmag(v.ratio_to_first), e: v.ratio_err ? (1.0857 * v.ratio_err) / v.ratio_to_first : 0, d });
    if (!pts.length) return;
    // ZTF behind: relative to its median within 40 days of the first pass
    const m0 = b.passes[0]!.mjd;
    const near = ztf.filter((q) => Math.abs(q.mjd - m0) < 40).map((q) => q.mag).sort((x, y) => x - y);
    const ref0 = near.length ? near[Math.floor(near.length / 2)]! : null;
    const zs = ref0 === null ? [] : ztf.filter((q) => q.band === 'zr' || q.band === 'zg').map((q) => [year(q.mjd), q.mag - ref0] as [number, number]);
    const xs = [...pts.map((p) => p.x), ...zs.map((z) => z[0])];
    const ys = [...pts.flatMap((p) => [p.y - p.e, p.y + p.e]), ...zs.map((z) => z[1])].sort((a, z) => a - z);
    const x0 = Math.min(...xs) - 0.2;
    const x1 = Math.max(...xs) + 0.2;
    const y0 = Math.min(ys[Math.floor(0.02 * (ys.length - 1))]!, -0.1) - 0.1;
    const y1 = Math.max(ys[Math.floor(0.98 * (ys.length - 1))]!, 0.1) + 0.1;
    const L = 34;
    const Bm = 18;
    const X = (v: number) => L + ((v - x0) / (x1 - x0)) * (W - L - 6);
    const Y = (m: number) => 6 + ((m - y0) / (y1 - y0)) * (H - Bm - 6); // fainter down
    c.fillStyle = 'rgba(160,185,194,0.9)';
    c.font = '500 10px ui-monospace, Menlo, monospace';
    for (let yr = Math.ceil(x0); yr <= x1; yr += Math.max(1, Math.round((x1 - x0) / 5))) c.fillText(String(yr), X(yr) - 12, H - 4);
    c.fillText(`${y0.toFixed(1)}`, 2, 14);
    c.fillText(`${y1.toFixed(1)}`, 2, H - Bm);
    c.fillText('SPHEREx, mag since pass 1', L + 4, 16);
    c.strokeStyle = 'rgba(160,185,194,0.25)';
    c.beginPath();
    c.moveTo(L, Y(0));
    c.lineTo(W - 6, Y(0));
    c.stroke();
    c.fillStyle = 'rgba(190,200,205,0.35)';
    for (const [x, y] of zs) {
      c.beginPath();
      c.arc(X(x), Y(y), 1.4, 0, 7);
      c.fill();
    }
    for (const p of pts) {
      c.strokeStyle = c.fillStyle = DET_COLOURS[p.d] ?? '#fff';
      c.lineWidth = 1.2;
      if (p.e > 0) {
        c.beginPath();
        c.moveTo(X(p.x), Y(p.y - p.e));
        c.lineTo(X(p.x), Y(p.y + p.e));
        c.stroke();
      }
      c.beginPath();
      c.arc(X(p.x), Y(p.y), 3, 0, 7);
      c.fill();
    }
  }, [b, ztf]);
  return <canvas ref={ref} class="lc-chart" />;
}

/** ZTF and NEOWISE light curves of the selected source (IRSA), with the SPHEREx years shaded. */
export function LightCurves({ src, field }: { src: Source; field?: { ra: number; dec: number; fov: number } }) {
  const [c, setC] = useState<Curves | null>(null);
  const [br, setBr] = useState<Brightness | null>(null);
  // SPHEREx's own brightness of the source in every pass and detector (the service measures it once the field is finished; until then it says waiting)
  useEffect(() => {
    setBr(null);
    if (!field || src.moving) return; // a moving body has no fixed place to measure and no variable-star verdict
    const ctl = new AbortController();
    const q = new URLSearchParams({ ra: src.ra.toFixed(6), dec: src.dec.toFixed(6), fov: String(field.fov), field_ra: field.ra.toFixed(6), field_dec: field.dec.toFixed(6), pmra: String(src.pmra ?? 0), pmdec: String(src.pmdec ?? 0) });
    let timer = 0;
    // the field may still be building: ask again every few seconds until the service has the answer
    const ask = (tries: number) =>
      fetch(`/api/v1/brightness?${q}`, { signal: ctl.signal })
        .then((r) => (r.ok ? r.json() : null))
        .then((d: Brightness | null) => {
          setBr(d);
          if (d && (d.state === 'running' || d.state === 'waiting') && tries < 90) timer = window.setTimeout(() => ask(tries + 1), 3000);
        })
        .catch(() => {});
    ask(0);
    return () => {
      ctl.abort();
      window.clearTimeout(timer);
    };
  }, [src.ra, src.dec, src.pmra, src.pmdec, field?.ra, field?.dec, field?.fov]);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setC(null);
    if (src.moving) return setBusy(false); // ZTF and NEOWISE sit at a fixed place; a moving body is not there
    setBusy(true);
    const ctl = new AbortController();
    const q = new URLSearchParams({ ra: src.ra.toFixed(6), dec: src.dec.toFixed(6) });
    if (src.pmra || src.pmdec) {
      q.set('pmra', String(src.pmra ?? 0));
      q.set('pmdec', String(src.pmdec ?? 0));
    }
    let timer = 0;
    // a survey that has not answered within a few seconds is `pending` on the service and keeps running there: ask again
    const ask = (tries: number) =>
      fetch(`/api/v1/lightcurves?${q}`, { signal: ctl.signal })
        .then((r) => (r.ok ? r.json() : null))
        .then((d: Curves | null) => {
          setC(d);
          const pending = !!d && (d.ztf.state === 'pending' || d.neowise.state === 'pending');
          if (pending && tries < 60) timer = window.setTimeout(() => ask(tries + 1), 2000);
          else setBusy(false);
        })
        .catch(() => setBusy(false));
    ask(0);
    return () => {
      ctl.abort();
      window.clearTimeout(timer);
    };
  }, [src.ra, src.dec, src.pmra, src.pmdec]);
  const say = (p: Part<unknown> | undefined, what: string) =>
    !p
      ? ''
      : p.state === 'none'
        ? `${what}: no source within its match radius`
        : p.state === 'unavailable'
          ? `${what}: IRSA did not answer`
          : '';
  if (src.moving) {
    const m = src.moving;
    return (
      <div class="lc">
        <div class="h2">Brightness</div>
        <div class="mono-s">
          A moving body is not a variable: its brightness changes with its distance from the Sun and the Earth and with its phase.
          {m.V != null ? ` JPL predicts V ${m.V.toFixed(1)} for it in this pass.` : ''}
        </div>
        {m.visits.length > 0 && (
          <div class="mono-s">
            {m.visits.map((v) => `visit ${v.n}: ${v.how === 'found' ? 'found' : 'recovered'} at S/N ${v.z.toFixed(1)}`).join(' · ')}
          </div>
        )}
      </div>
    );
  }
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
      {br?.state === 'done' && br.result && (
        <>
          <SpherexChart b={br.result} ztf={c?.ztf.state === 'ok' ? c.ztf.points : []} />
          <div class="mono-s">{brightnessLine(src.name || 'This source', br.result) ?? 'SPHEREx brightness: too few passes to compare'}</div>
        </>
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
