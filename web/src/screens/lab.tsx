import { useEffect, useRef, useState } from 'preact/hooks';
import { FieldClient, monthYear, type FieldMeta } from '../lib/field';
import { formatHash, type Route } from '../lib/route';
import type { SxpPart } from '../lib/sxp';
import { frameAt } from '../viewer/pendulum';
import { Renderer, wallLayout, type Panel, type StackName } from '../viewer/renderer';

const SIZES = [2, 5, 10, 20, 60];
type View = 'picture' | 'bands' | 'det';
const VIEWS: { key: View; name: string; tip: string }[] = [
  { key: 'picture', name: 'Picture', tip: 'The passes one after another in spectral colour: what moved jumps, the rest stands still.' },
  { key: 'bands', name: 'Wall of bands', tip: 'One panel per named band: in which light the object is bright.' },
  { key: 'det', name: 'Wall of detectors', tip: 'One panel per detector: a real object jumps in all six at once.' },
];
const DETECTORS = [
  { name: 'very near infrared', range: '0.75–1.11 µm' },
  { name: 'near infrared', range: '1.11–1.64 µm' },
  { name: 'near infrared', range: '1.64–2.42 µm' },
  { name: 'mid infrared', range: '2.42–3.82 µm' },
  { name: 'mid infrared', range: '3.82–4.42 µm' },
  { name: 'mid infrared', range: '4.42–5.00 µm' },
];

function hueRgb(h: number): [number, number, number] {
  const f = (n: number) => {
    const k = (n + h / 30) % 12;
    return 0.5 - 0.5 * Math.max(-1, Math.min(k - 3, 9 - k, 1));
  };
  return [f(0), f(8), f(4)];
}

interface Label {
  x: number;
  y: number;
  w: number;
  text: string;
  sub: string;
}

export function Lab({ route }: { route: Route }) {
  const ra = Number(route.params['ra']);
  const dec = Number(route.params['dec']);
  const fov = SIZES.includes(Number(route.params['fov'])) ? Number(route.params['fov']) : 5;
  const view: View = (['picture', 'bands', 'det'] as const).find((v) => v === route.params['view']) ?? 'picture';
  const canvas = useRef<HTMLCanvasElement>(null);
  const clientRef = useRef<FieldClient | null>(null);
  const [phase, setPhase] = useState('');
  const [meta, setMeta] = useState<FieldMeta | null>(null);
  const [shown, setShown] = useState<{ pass: number; stack: StackName } | null>(null);
  const [labels, setLabels] = useState<Label[]>([]);
  const [playing, setPlaying] = useState(true);
  const [fixed, setFixed] = useState<number | null>(null);
  const valid = Number.isFinite(ra) && Number.isFinite(dec);
  const state = useRef({ view, playing, fixed, meta: null as FieldMeta | null });
  state.current = { view, playing, fixed, meta };

  useEffect(() => {
    if (!valid || !canvas.current) return;
    const r = new Renderer(canvas.current);
    const pending: { name: string; part: SxpPart }[] = [];
    const place = (name: string, part: SxpPart) => {
      const k = Number(part.meta['pass']);
      const first = Object.values(part.arrays)[0]!;
      const n = first.shape[first.shape.length - 1]!;
      const np = state.current.meta?.passes.length ?? 0;
      if (r.size !== n || r.passes < Math.max(np, k + 1)) {
        if (r.size === n && r.passes > 0) {
          pending.push({ name, part });
          return; // a field with more passes than allocated: re-allocate once the meta says how many
        }
        r.setup(n, Math.max(np, k + 1, 4));
      }
      const stack = name.split('_')[0] as StackName;
      const arr = part.arrays[stack === 'picture' ? 'rgb' : stack === 'quick' ? 'white' : stack]!;
      r.upload(stack, k, arr.data as Uint16Array);
      if (part.arrays['sat']) r.uploadSat(k, part.arrays['sat'].data as Uint8Array);
    };
    const client = new FieldClient({
      onPhase: setPhase,
      onError: (t) => setPhase(`failed: ${t}`),
      onMeta: (m) => {
        setMeta(m);
        state.current.meta = m;
        for (const p of m.passes) r.setWhiteBalance(p.index, p.white_balance.kr, p.white_balance.kb);
        while (pending.length) {
          const x = pending.shift()!;
          place(x.name, x.part);
        }
      },
      onPart: place,
    });
    clientRef.current = client;
    void client.open(ra, dec, fov);

    let raf = 0;
    const t0 = performance.now();
    let lastLabels = '';
    const tick = () => {
      const st = state.current;
      const { w, h } = r.begin();
      const stack: StackName = st.view === 'picture' ? (r.loaded('picture').length >= 2 || r.loaded('quick').length === 0 ? 'picture' : 'quick') : st.view === 'bands' ? 'bands' : 'dets';
      const loaded = r.loaded(stack);
      const out: Label[] = [];
      if (loaded.length) {
        let a = loaded[0]!;
        let b = a;
        let t = 0;
        if (st.fixed !== null && loaded.includes(st.fixed)) a = b = st.fixed;
        else if (st.playing && loaded.length > 1) {
          const f = frameAt(performance.now() - t0, loaded.length);
          a = loaded[f.a]!;
          b = loaded[f.b]!;
          t = f.t;
        }
        let panels: (Panel & { label?: string; sub?: string })[] = [];
        if (st.view === 'picture') {
          const s = Math.min(w, h);
          panels = [{ x: (w - s) / 2, y: (h - s) / 2, w: s, h: s, stack, mode: stack === 'picture' ? 'colour' : 'layer' }];
        } else if (st.view === 'bands') {
          const bands = st.meta?.bands ?? [];
          panels = wallLayout(bands.length, w, h).map((c, i) => ({ x: c.x, y: c.y, w: c.size, h: c.size, stack, mode: 'layer', layer: i, tint: hueRgb(bands[i]!.hue), noData: [0, 0, 0], label: bands[i]!.name, sub: bands[i]!.label }));
        } else {
          panels = wallLayout(6, w, h).map((c, i) => ({ x: c.x, y: c.y, w: c.size, h: c.size, stack, mode: 'layer', layer: i, noData: [0, 0, 0], label: `D${i + 1} ${DETECTORS[i]!.name}`, sub: DETECTORS[i]!.range }));
        }
        for (const p of panels) {
          r.panel(p, a, b, t);
          if (p.label) out.push({ x: p.x, y: p.y, w: p.w, text: p.label, sub: p.sub ?? '' });
        }
        setShown((s) => (s && s.pass === a && s.stack === stack ? s : { pass: a, stack }));
      }
      const key = JSON.stringify(out);
      if (key !== lastLabels) {
        lastLabels = key;
        setLabels(out);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      client.stop();
      clientRef.current = null;
    };
  }, [ra, dec, fov, valid]);

  useEffect(() => {
    const c = clientRef.current;
    if (!c) return;
    if (view === 'bands' || fov <= 10) c.want('bands_');
    if (view === 'det' || fov <= 10) c.want('dets_');
  }, [view, fov, meta]);

  if (!valid) return <div class="lab-empty">Open a place from the globe, or give ra and dec in the address.</div>;

  const set = (params: Record<string, string>) => {
    location.hash = formatHash({ screen: 'lab', params: { ...route.params, ...params } });
  };
  const passMeta = meta?.passes.find((p) => p.index === shown?.pass);
  const what = shown?.stack === 'picture' ? 'colour' : shown?.stack === 'quick' ? 'white light' : shown?.stack === 'bands' ? 'bands' : 'detectors';
  const status = phase || (shown && meta ? `Pass ${shown.pass + 1} of ${meta.passes.length} · ${passMeta ? monthYear(passMeta.mjd_mid) : ''} · ${what} · ${meta.exposures} exposures` : '');
  return (
    <div class="lab">
      <div class="lab-picture">
        <div class="picture-wrap">
          <canvas ref={canvas} class="picture" data-testid="picture" />
          {labels.map((l) => (
            <div class="panel-label" style={{ left: `${l.x + 4}px`, top: `${l.y + 3}px`, maxWidth: `${l.w - 8}px` }}>
              {l.text}
              <span>{l.sub}</span>
            </div>
          ))}
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
            <button key={s} class={`chip ${s === fov ? 'on' : ''}`} onClick={() => set({ fov: String(s) })} data-testid={`fov-${s}`}>{s}′</button>
          ))}
        </div>
        <div class="row">
          <span class="label">View</span>
          {VIEWS.map((v) => (
            <button key={v.key} class={`chip ${v.key === view ? 'on' : ''}`} title={v.tip} onClick={() => set({ view: v.key })} data-testid={`view-${v.key}`}>{v.name}</button>
          ))}
        </div>
        <div class="row dim">RA {ra.toFixed(5)} · Dec {dec.toFixed(5)}</div>
      </aside>
    </div>
  );
}
