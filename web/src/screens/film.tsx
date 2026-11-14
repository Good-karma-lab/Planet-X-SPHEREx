import { useEffect, useRef, useState } from 'preact/hooks';
import { mjdToDate } from '../lib/field';
import { parseSxp, type SxpPart } from '../lib/sxp';

interface FrameMeta {
  visit: number;
  det: number;
  mjd: number;
  exposures: number;
}

interface FilmMeta {
  grid: { n: number; step_arcsec: number };
  pass: { mjd0: number; mjd1: number };
  frames: FrameMeta[];
  exposures: number;
}

const FRAME_MS = 600;
// motion encoding: 1 + 254 × clip((z + 5) / 20); amber from z = 3.5, full from z = 7
const zOf = (v: number) => ((v - 1) / 254) * 20 - 5;

function utc(mjd: number): string {
  const d = mjdToDate(mjd);
  return d.toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }) + ' UTC';
}

export function FilmView({ ra, dec, fov, mjd, playing, onStatus }: { ra: number; dec: number; fov: number; mjd?: number; playing: boolean; onStatus: (s: string) => void }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [film, setFilm] = useState<{ meta: FilmMeta; part: SxpPart } | null>(null);
  const [motionOnly, setMotionOnly] = useState(false);
  const motionRef = useRef(motionOnly);
  motionRef.current = motionOnly;
  const playRef = useRef(playing);
  playRef.current = playing;

  useEffect(() => {
    let stop = false;
    setFilm(null);
    (async () => {
      onStatus('asking for the film of the pass…');
      const r = await fetch(`/api/v1/film?ra=${ra}&dec=${dec}&fov=${fov}${mjd ? `&mjd=${mjd}` : ''}`, { cache: 'no-store' });
      const body = (await r.json()) as { key?: string; state?: string; note?: string; job?: { id: string } };
      if (body.state === 'none') return onStatus(body.note ?? 'no film here');
      let state = body.state;
      while (!stop && state !== 'done') {
        const j = (await (await fetch(`/api/v1/jobs/${body.job!.id}`, { cache: 'no-store' })).json()) as { state: string; phase: string; error: string | null };
        if (j.state === 'error' || j.state === 'cancelled') return onStatus(`film failed: ${j.error ?? j.state}`);
        onStatus(`film: ${j.phase}`);
        state = j.state;
        if (state !== 'done') await new Promise((res) => setTimeout(res, 1000));
      }
      if (stop) return;
      const p = parseSxp(await (await fetch(`/api/v1/film/${body.key}/parts/film`)).arrayBuffer());
      setFilm({ meta: p.meta as unknown as FilmMeta, part: p });
    })().catch((e) => onStatus(`film failed: ${String(e)}`));
    return () => {
      stop = true;
    };
  }, [ra, dec, fov, mjd]);

  useEffect(() => {
    if (!film || !canvas.current) return;
    const { meta, part } = film;
    const n = meta.grid.n;
    const frames = meta.frames.length;
    const sky = part.arrays['sky']!.data as Uint8Array;
    const mot = part.arrays['motion']!.data as Uint8Array;
    const c = canvas.current;
    c.width = n;
    c.height = n;
    const ctx = c.getContext('2d')!;
    const img = ctx.createImageData(n, n);
    let k = 0;
    let last = -1;
    let raf = 0;
    const t0 = performance.now();
    const draw = (f: number) => {
      const off = f * n * n;
      const d = img.data;
      for (let i = 0; i < n * n; i++) {
        const s = sky[off + i]!;
        const m = mot[off + i]!;
        let r: number, g: number, b: number;
        if (motionRef.current) {
          const z = m ? zOf(m) : 0;
          const a = m ? Math.min(1, Math.max(0, (z - 3.5) / 3.5)) : 0;
          r = m ? 18 + 237 * a : 40;
          g = m ? 18 + 159 * a : 44;
          b = m ? 18 + 41 * a : 50;
        } else {
          const v = s ? s : 0;
          r = g = b = v;
          if (!s) [r, g, b] = [0, 0, 0];
          if (m) {
            const z = zOf(m);
            const a = Math.min(1, Math.max(0, (z - 3.5) / 3.5));
            if (a > 0) {
              r = r + (255 - r) * a;
              g = g + (177 - g) * a;
              b = b + (59 - b) * a * 0.2;
            }
          }
        }
        d[4 * i] = r;
        d[4 * i + 1] = g;
        d[4 * i + 2] = b;
        d[4 * i + 3] = 255;
      }
      ctx.putImageData(img, 0, 0);
      const fm = meta.frames[f]!;
      onStatus(`visit ${f + 1} of ${frames} · ${utc(fm.mjd)} · D${fm.det} · ${fm.exposures} exposures · ${motionRef.current ? 'motion only' : 'sky, motion in amber'}`);
    };
    const tick = () => {
      if (playRef.current) k = Math.floor((performance.now() - t0) / FRAME_MS) % frames;
      const key = k * 2 + (motionRef.current ? 1 : 0);
      if (key !== last) {
        last = key;
        draw(k);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [film]);

  return (
    <div class="film">
      <canvas ref={canvas} class="film-canvas" data-testid="film" />
      <label class="film-switch">
        <input type="checkbox" checked={motionOnly} onChange={(e) => setMotionOnly((e.target as HTMLInputElement).checked)} /> Motion only
      </label>
    </div>
  );
}
