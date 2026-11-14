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

export interface KnownBody {
  name: string;
  v: number | null;
  frames: { k: number; x: number; y: number; inside: boolean }[];
  expected: number[];
  /** SPHEREx detector images of the pass (all detectors) that hold the body */
  planes?: number;
  /** the expected frames on which the film's motion map shows something at the predicted place (z ≥ 4 within 2 grid pixels) */
  seen: number[];
  /** the motion significance stacked along JPL's track over the expected visits: Σ z / √N at the predicted pixel, and the same
   *  at four places 20 grid pixels aside (what chance gives) */
  stackZ?: number;
  stackN?: number;
  controlZ?: number;
}

const SEEN_Z = 4.0;

function utc(mjd: number): string {
  const d = mjdToDate(mjd);
  return d.toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }) + ' UTC';
}

export function FilmView({
  ra,
  dec,
  fov,
  mjd,
  playing,
  onStatus,
  onBodies,
}: {
  ra: number;
  dec: number;
  fov: number;
  mjd?: number;
  playing: boolean;
  onStatus: (s: string) => void;
  onBodies?: (b: { state: string; bodies: KnownBody[]; vLimit: number }) => void;
}) {
  const overlay = useRef<HTMLCanvasElement>(null);
  const bodiesRef = useRef<KnownBody[]>([]);
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
        const j = (await (await fetch(`/api/v1/jobs/${body.job!.id}`, { cache: 'no-store' })).json()) as {
          state: string;
          phase: string;
          error: string | null;
        };
        if (j.state === 'error' || j.state === 'cancelled') return onStatus(`film failed: ${j.error ?? j.state}`);
        onStatus(`film: ${j.phase}`);
        state = j.state;
        if (state !== 'done') await new Promise((res) => setTimeout(res, 1000));
      }
      if (stop) return;
      const p = parseSxp(await (await fetch(`/api/v1/film/${body.key}/parts/film`)).arrayBuffer());
      setFilm({ meta: p.meta as unknown as FilmMeta, part: p });
      // the known bodies JPL predicts in the field, then on which of their expected visits the motion map shows them
      bodiesRef.current = [];
      onBodies?.({ state: 'running', bodies: [], vLimit: 20.5 });
      let pause = 2000;
      for (let i = 0; i < 40 && !stop; i++) {
        const b = (await (await fetch(`/api/v1/film/${body.key}/bodies`, { cache: 'no-store' })).json()) as {
          state: string;
          bodies: Omit<KnownBody, 'seen'>[];
          v_limit?: number;
        };
        if (stop) return;
        if (b.state === 'done' || b.state === 'unavailable') {
          const n = (p.meta as unknown as FilmMeta).grid.n;
          const mot = p.arrays['motion']!.data as Uint8Array;
          const list: KnownBody[] = b.bodies.map((kb) => {
            const seen = kb.expected.filter((k) => {
              const f = kb.frames[k]!;
              let best = 0;
              for (let dy = -2; dy <= 2; dy++)
                for (let dx = -2; dx <= 2; dx++) {
                  const x = Math.round(f.x) + dx;
                  const y = Math.round(f.y) + dy;
                  if (x < 0 || y < 0 || x >= n || y >= n) continue;
                  best = Math.max(best, mot[k * n * n + y * n + x]!);
                }
              return best > 0 && zOf(best) >= SEEN_Z;
            });
            // the track stacked: z at the predicted pixel of every expected visit, summed over √N (a body too faint for one visit can
            // stand out when its visits are added); the same stack 20 pixels aside on four sides is the chance level
            const zAt = (k: number, x: number, y: number) => {
              const xi = Math.round(x);
              const yi = Math.round(y);
              if (xi < 0 || yi < 0 || xi >= n || yi >= n) return null;
              const v = mot[k * n * n + yi * n + xi]!;
              return v ? zOf(v) : null;
            };
            const stack = (dx: number, dy: number) => {
              let sum = 0;
              let cnt = 0;
              for (const k of kb.expected) {
                const z = zAt(k, kb.frames[k]!.x + dx, kb.frames[k]!.y + dy);
                if (z !== null) {
                  sum += z;
                  cnt++;
                }
              }
              return { z: cnt ? sum / Math.sqrt(cnt) : 0, n: cnt };
            };
            const on = stack(0, 0);
            const off = [stack(20, 0), stack(-20, 0), stack(0, 20), stack(0, -20)];
            const controlZ = Math.sqrt(off.reduce((a, o) => a + o.z * o.z, 0) / off.length);
            return { ...kb, seen, stackZ: on.z, stackN: on.n, controlZ };
          });
          bodiesRef.current = list;
          onBodies?.({ state: b.state, bodies: list, vLimit: b.v_limit ?? 20.5 });
          return;
        }
        await new Promise((res) => setTimeout(res, pause));
        pause = Math.min(10000, pause * 1.4);
      }
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
      drawBodies(f);
      const fm = meta.frames[f]!;
      onStatus(
        `visit ${f + 1} of ${frames} · ${utc(fm.mjd)} · D${fm.det} · ${fm.exposures} exposures · ${motionRef.current ? 'motion only' : 'sky, motion in amber'}`,
      );
    };
    // the known bodies on this frame: a ring where JPL predicts them, solid amber when the motion map shows them there
    const drawBodies = (f: number) => {
      const ov = overlay.current;
      if (!ov) return;
      const w = ov.clientWidth;
      const dpr = Math.min(2, devicePixelRatio || 1);
      if (ov.width !== Math.round(w * dpr)) {
        ov.width = Math.round(w * dpr);
        ov.height = Math.round(w * dpr);
      }
      const o = ov.getContext('2d')!;
      o.setTransform(dpr, 0, 0, dpr, 0, 0);
      o.clearRect(0, 0, w, w);
      const sc = w / n;
      o.font = '600 11px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
      for (const b of bodiesRef.current) {
        const fr = b.frames[f];
        if (!fr || !fr.inside) continue;
        const seen = b.seen.includes(f);
        const x = (fr.x + 0.5) * sc;
        const y = (fr.y + 0.5) * sc;
        o.setLineDash(seen ? [] : [3, 3]);
        o.lineWidth = 3;
        o.strokeStyle = 'rgba(0,0,0,0.6)';
        o.beginPath();
        o.arc(x, y, 12, 0, 2 * Math.PI);
        o.stroke();
        o.lineWidth = 1.5;
        o.strokeStyle = seen ? '#ffb454' : 'rgba(220,235,240,0.8)';
        o.stroke();
        o.setLineDash([]);
        if ((b.v ?? 99) <= 19.5 || seen) {
          const label = b.name.replace(/\s*\(.*\)\s*$/, '');
          o.lineWidth = 3;
          o.strokeStyle = '#000';
          o.fillStyle = seen ? '#ffd08a' : '#d6e7ed';
          o.strokeText(label, x + 15, y - 8);
          o.fillText(label, x + 15, y - 8);
        }
      }
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
      <canvas ref={overlay} class="film-canvas" style={{ background: 'transparent', pointerEvents: 'none' }} />
      <label class="film-switch">
        <input type="checkbox" checked={motionOnly} onChange={(e) => setMotionOnly((e.target as HTMLInputElement).checked)} /> Motion only
      </label>
    </div>
  );
}
