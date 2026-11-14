import { useEffect, useRef, useState } from 'preact/hooks';
import type { Source, Stamp } from './types';

/**
 * The pixels behind every point of a spectrum, as the service keeps them (GET /api/v1/spectrum/stamps, asked from index `from` on while the measurement runs).
 * 404 means the spectrum was not asked for yet (or the service has no such route): asked again for a while, then given up.
 */
export function useStamps(src: Source | null, running: boolean): { stamps: Map<number, Stamp>; absent: boolean } {
  const [stamps, setStamps] = useState<Map<number, Stamp>>(new Map());
  const [absent, setAbsent] = useState(false);
  const runningRef = useRef(running);
  runningRef.current = running;
  useEffect(() => {
    setStamps(new Map());
    setAbsent(false);
    if (!src) return;
    let stop = false;
    const held = new Map<number, Stamp>();
    let next = 0;
    let misses = 0;
    const q = new URLSearchParams({ ra: src.ra.toFixed(6), dec: src.dec.toFixed(6) });
    if (src.pmra || src.pmdec) {
      q.set('pmra', String(src.pmra ?? 0));
      q.set('pmdec', String(src.pmdec ?? 0));
    }
    (async () => {
      for (let round = 0; round < 4000 && !stop; round++) {
        try {
          const r = await fetch(`/api/v1/spectrum/stamps?${q}&from=${next}`, { cache: 'no-store' });
          if (stop) return;
          if (r.status === 404) {
            if (++misses > 12) return setAbsent(true);
          } else if (r.ok) {
            const d = (await r.json()) as { state: string; total: number; stamps: Stamp[] };
            for (const s of d.stamps) {
              held.set(s.i, s);
              next = Math.max(next, s.i + 1);
            }
            if (d.stamps.length) setStamps(new Map(held));
            if (d.state !== 'running' && held.size >= (d.total ?? 0)) return;
            if (d.state !== 'running' && !d.stamps.length) return;
          }
        } catch {
          // asked again
        }
        await new Promise((res) => setTimeout(res, runningRef.current ? 1500 : 600));
      }
    })();
    return () => {
      stop = true;
    };
  }, [src?.ra, src?.dec, src?.pmra, src?.pmdec]);
  return { stamps, absent };
}

type Grid = (number | null)[][];

function percentiles(g: Grid, lo: number, hi: number): [number, number] {
  const f = g.flat().filter((v): v is number => v != null && Number.isFinite(v)).sort((a, b) => a - b);
  if (!f.length) return [0, 1];
  return [f[Math.floor(f.length * lo)]!, f[Math.min(f.length - 1, Math.floor(f.length * hi))]!];
}

const sq = (t: number) => Math.round(255 * Math.sqrt(Math.max(0, Math.min(1, t))));

function Panel({ title, g, paint, box }: { title: string; g: Grid; paint: (v: number | null, y: number, x: number) => [number, number, number]; box?: number }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const n = g.length;
  useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    const c = cv.getContext('2d')!;
    const im = c.createImageData(n, n);
    for (let y = 0; y < n; y++)
      for (let x = 0; x < n; x++) {
        const [r, gg, b] = paint(g[y]![x] ?? null, y, x);
        im.data.set([r, gg, b, 255], (y * n + x) * 4);
      }
    c.putImageData(im, 0, 0);
    if (box && box < n) {
      c.strokeStyle = '#ffb454';
      c.lineWidth = 0.7;
      const o = (n - box) / 2;
      c.strokeRect(o + 0.5, o + 0.5, box - 1, box - 1);
    }
  }, [g, paint, box, n]);
  return (
    <figure class="spec-stamp">
      <canvas ref={ref} width={n} height={n} title={title} />
      <figcaption>{title}</figcaption>
    </figure>
  );
}

/**
 * The four pictures of one exposure of a spectrum: the file's pixels around the source (the box of the fit outlined), the pixels the fit used, the model (flux × PSF + sky)
 * and what is left (residual). An exposure the fit did not use shows the file's pixels only, with the reason in `note`.
 */
export function StampPanels({ stamp, note }: { stamp: Stamp | undefined; note?: string }) {
  if (!stamp || stamp.status === 0) return note ? <div class="mono-s">{note}</div> : null;
  const raw = stamp.raw!;
  const [lo, hi] = percentiles(raw, 0.02, 0.995);
  const rawPaint = (v: number | null): [number, number, number] => {
    if (v == null) return [255, 255, 255];
    const g = sq((v - lo) / (hi - lo || 1));
    return [g, g, g];
  };
  const fit = stamp.status === 2 && stamp.data && stamp.model && stamp.resid && stamp.used;
  let dataPaint: ((v: number | null, y: number, x: number) => [number, number, number]) | null = null;
  let modelPaint: ((v: number | null) => [number, number, number]) | null = null;
  let resPaint: ((v: number | null, y: number, x: number) => [number, number, number]) | null = null;
  if (fit) {
    const sky = stamp.sky ?? 0;
    const top = Math.max(1e-9, ...stamp.model!.flat().filter((v): v is number => v != null && Number.isFinite(v))) - sky;
    const used = stamp.used!;
    const off: [number, number, number] = [70, 80, 86];
    dataPaint = (v, y, x) => {
      if (!used[y]![x] || v == null) return off;
      const g = sq((v - sky) / (top || 1));
      return [g, g, g];
    };
    modelPaint = (v) => {
      const g = sq(((v ?? sky) - sky) / (top || 1));
      return [g, g, g];
    };
    resPaint = (v, y, x) => {
      if (!used[y]![x] || v == null) return off;
      const t = Math.max(-1, Math.min(1, v / (0.25 * (top || 1))));
      return t > 0 ? [Math.round(30 + 200 * t), Math.round(20 + 110 * t), 10] : [10, Math.round(20 + 90 * -t), Math.round(30 + 200 * -t)];
    };
  }
  return (
    <div class="spec-stamps">
      <Panel title="file pixels" g={raw} paint={rawPaint} box={fit ? stamp.data!.length : undefined} />
      {fit && dataPaint && modelPaint && resPaint && (
        <>
          <Panel title="pixels used" g={stamp.data!} paint={dataPaint} />
          <Panel title="flux × PSF" g={stamp.model!} paint={modelPaint} />
          <Panel title="residual" g={stamp.resid!} paint={resPaint} />
        </>
      )}
      {note && <div class="mono-s spec-stamp-note">{note}</div>}
    </div>
  );
}
