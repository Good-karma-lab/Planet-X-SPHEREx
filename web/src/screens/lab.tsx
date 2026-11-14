import { useEffect, useRef, useState } from 'preact/hooks';
import { FieldClient, monthYear, type FieldMeta } from '../lib/field';
import { formatHash, type Route } from '../lib/route';
import type { SxpPart } from '../lib/sxp';
import { frameAt } from '../viewer/pendulum';
import { Renderer } from '../viewer/renderer';

const SIZES = [2, 5, 10, 20, 60];

interface Props {
  route: Route;
}

export function Lab({ route }: Props) {
  const ra = Number(route.params['ra']);
  const dec = Number(route.params['dec']);
  const fov = SIZES.includes(Number(route.params['fov'])) ? Number(route.params['fov']) : 5;
  const canvas = useRef<HTMLCanvasElement>(null);
  const [phase, setPhase] = useState('');
  const [meta, setMeta] = useState<FieldMeta | null>(null);
  const [shown, setShown] = useState<{ pass: number; colour: boolean; loaded: number } | null>(null);
  const [playing, setPlaying] = useState(true);
  const [fixed, setFixed] = useState<number | null>(null);
  const valid = Number.isFinite(ra) && Number.isFinite(dec);

  useEffect(() => {
    if (!valid || !canvas.current) return;
    const r = new Renderer(canvas.current);
    let mode: 'none' | 'white' | 'colour' = 'none';
    let n = 0;
    let passes = 0;
    const pictures = new Map<number, SxpPart>();
    const quicks = new Map<number, SxpPart>();
    let wb: Record<number, [number, number]> = {};

    const passesOf = (part: SxpPart) => Number(part.meta['pass']);
    const ensure = (np: number, size: number, m: 'white' | 'colour') => {
      if (mode === m && n === size && passes >= np) return false;
      n = size;
      passes = Math.max(passes, np, 1);
      mode = m;
      r.setup(n, passes, m === 'colour' ? 3 : 1);
      return true;
    };
    const uploadAll = () => {
      if (mode === 'colour') for (const [k, p] of pictures) r.upload(k, { values: p.arrays['rgb']!.data as Uint16Array, sat: p.arrays['sat']!.data as Uint8Array, wb: wb[k] });
      else for (const [k, p] of quicks) r.upload(k, { values: p.arrays['white']!.data as Uint16Array, sat: p.arrays['sat']!.data as Uint8Array });
    };
    const client = new FieldClient({
      onPhase: setPhase,
      onError: (t) => setPhase(`failed: ${t}`),
      onMeta: (m) => {
        setMeta(m);
        wb = Object.fromEntries(m.passes.map((p) => [p.index, [p.white_balance.kr, p.white_balance.kb] as [number, number]]));
        if (mode === 'colour') {
          ensure(m.passes.length, m.grid.n, 'colour');
          uploadAll();
        }
      },
      onPart: (name, part) => {
        const k = passesOf(part);
        if (name.startsWith('quick_')) {
          quicks.set(k, part);
          if (pictures.size < 2) {
            const size = part.arrays['white']!.shape[0]!;
            if (ensure(Math.max(k + 1, quicks.size), size, 'white')) uploadAll();
            else r.upload(k, { values: part.arrays['white']!.data as Uint16Array, sat: part.arrays['sat']!.data as Uint8Array });
          }
        } else if (name.startsWith('picture_')) {
          pictures.set(k, part);
          if (pictures.size >= 2 || (quicks.size === 0 && pictures.size >= 1)) {
            const size = part.arrays['rgb']!.shape[1]!;
            if (ensure(Math.max(k + 1, pictures.size, passes), size, 'colour')) uploadAll();
            else r.upload(k, { values: part.arrays['rgb']!.data as Uint16Array, sat: part.arrays['sat']!.data as Uint8Array, wb: wb[k] });
          }
        }
      },
    });
    void client.open(ra, dec, fov);

    let raf = 0;
    const t0 = performance.now();
    const tick = () => {
      const loaded = r.passesLoaded;
      if (loaded.length) {
        let a = loaded[0]!;
        let b = a;
        let t = 0;
        if (fixedRef.current !== null && loaded.includes(fixedRef.current)) {
          a = b = fixedRef.current;
        } else if (playingRef.current && loaded.length > 1) {
          const f = frameAt(performance.now() - t0, loaded.length);
          a = loaded[f.a]!;
          b = loaded[f.b]!;
          t = f.t;
        }
        r.draw(a, b, t);
        setShown((s) => (s && s.pass === a && s.colour === (mode === 'colour') && s.loaded === loaded.length ? s : { pass: a, colour: mode === 'colour', loaded: loaded.length }));
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      client.stop();
    };
  }, [ra, dec, fov, valid]);

  const playingRef = useRef(playing);
  playingRef.current = playing;
  const fixedRef = useRef<number | null>(fixed);
  fixedRef.current = fixed;

  if (!valid) return <div class="lab-empty">Open a place from the globe, or give ra and dec in the address.</div>;

  const passMeta = meta?.passes.find((p) => p.index === shown?.pass);
  const status = phase
    ? phase
    : shown && meta
      ? `Pass ${shown.pass + 1} of ${meta.passes.length} · ${passMeta ? monthYear(passMeta.mjd_mid) : ''} · ${shown.colour ? 'colour' : 'white light'} · ${meta.exposures} exposures`
      : '';
  const go = (size: number) => {
    location.hash = formatHash({ screen: 'lab', params: { ...route.params, fov: String(size) } });
  };
  return (
    <div class="lab">
      <div class="lab-picture">
        <div class="picture-wrap">
          <canvas ref={canvas} class="picture" data-testid="picture" />
        </div>
        <div class="status-line" data-testid="lab-status">{status}</div>
        <div class="timeline">
          <button class="play" onClick={() => { setFixed(null); setPlaying(!playing); }} aria-label={playing ? 'Pause' : 'Play'}>{playing ? '❚❚' : '▶'}</button>
          {(meta?.passes ?? []).map((p) => (
            <button key={p.index} class={`pass ${shown?.pass === p.index ? 'on' : ''}`} onClick={() => { setPlaying(false); setFixed(p.index); }} title={`MJD ${p.mjd0.toFixed(1)}–${p.mjd1.toFixed(1)}`}>
              {monthYear(p.mjd_mid)}
            </button>
          ))}
        </div>
      </div>
      <aside class="lab-panel">
        <div class="row">
          <span class="label">Field</span>
          {SIZES.map((s) => (
            <button key={s} class={`chip ${s === fov ? 'on' : ''}`} onClick={() => go(s)} data-testid={`fov-${s}`}>{s}′</button>
          ))}
        </div>
        <div class="row dim">RA {ra.toFixed(5)} · Dec {dec.toFixed(5)}</div>
      </aside>
    </div>
  );
}
