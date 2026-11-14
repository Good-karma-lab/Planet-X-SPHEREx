import { useEffect, useLayoutEffect, useRef, useState } from 'preact/hooks';
import { aim } from '../lib/aim';
import { FieldClient, mjdToDate, type FieldMeta } from '../lib/field';
import { formatHash, type Route } from '../lib/route';
import { formatDec, formatRA, parseDec, parseRA } from '../lib/sky';
import type { SxpPart } from '../lib/sxp';
import { icon } from '../ui/icons';
import { passLevels, starBalance } from '../viewer/balance';
import { ARCHIVE, ArchiveSet } from '../viewer/archive';
import { FADE_MS, HOLD_MS, order } from '../viewer/pendulum';
import { PER_PASS, Renderer, wallLayout, type Panel, type StackName } from '../viewer/renderer';
import { atEpoch } from '../lib/sky';
import { FilmView, type KnownBody } from './film';
import { ObjectTab, Passport, SpectrumTab, driverWords, type AnomalyField, type Source } from './spectrum';
import { halfToFloat } from '../lib/sxp';

const SIZES = [2, 5, 10, 20, 60];
type View = 'picture' | 'bands' | 'det';
type Time = 'loop' | 'diff' | 'sum';
type Tab = 'ctl' | 'obj' | 'spec';

const ICON_DET =
  '<svg viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="8" aria-hidden="true"><rect x="28" y="64" width="62" height="56" rx="8"/><rect x="97" y="64" width="62" height="56" rx="8"/><rect x="166" y="64" width="62" height="56" rx="8"/><rect x="28" y="136" width="62" height="56" rx="8"/><rect x="97" y="136" width="62" height="56" rx="8"/><rect x="166" y="136" width="62" height="56" rx="8"/></svg>';

const VIEWS: { id: View; name: string; icon: string; tip: string }[] = [
  {
    id: 'picture',
    name: 'Picture',
    icon: icon('square', 22),
    tip: 'One picture of the field in the light of the bands you choose: the quickest way to see what moves.',
  },
  {
    id: 'bands',
    name: 'Wall of bands',
    icon: icon('squares-four', 22),
    tip: 'One panel for each named band: what the object is made of and in which light it is bright.',
  },
  {
    id: 'det',
    name: 'Wall of detectors',
    icon: ICON_DET,
    tip: 'One panel for each of the six detectors: an artefact lives in one detector; a real object moves the same way in all six.',
  },
];

const BANDS = [
  { key: 'star', name: 'Starlight', band: '0.75–1.1 µm', hue: 48, icon: 'sun' },
  {
    key: 'h2o',
    name: 'Water vapor',
    band: '1.4·1.9·2.7 µm',
    hue: 205,
    icon: 'drop',
  },
  {
    key: 'ch4',
    name: 'Methane',
    band: '1.6 and 3.3 µm',
    hue: 24,
    icon: 'flame',
  },
  {
    key: 'ice',
    name: 'Water ice',
    band: '≈3.0 µm',
    hue: 185,
    icon: 'snowflake',
  },
  {
    key: 'pah',
    name: 'Dust (PAH)',
    band: '3.3 µm',
    hue: 140,
    icon: 'dots-nine',
  },
  { key: 'co2', name: 'CO₂', band: '4.2–4.3 µm', hue: 275, icon: 'cloud-fog' },
  { key: 'co', name: 'CO', band: '4.6–4.8 µm', hue: 345, icon: 'cloud-slash' },
];

const DETECTORS = [
  { name: 'very near infrared', range: '0.75–1.11 µm', hue: 270 },
  { name: 'near infrared', range: '1.11–1.64 µm', hue: 220 },
  { name: 'near infrared', range: '1.64–2.42 µm', hue: 175 },
  { name: 'mid infrared', range: '2.42–3.82 µm', hue: 95 },
  { name: 'mid infrared', range: '3.82–4.42 µm', hue: 40 },
  { name: 'mid infrared', range: '4.42–5.00 µm', hue: 5 },
];

/** hsl(h, 80 %, 60 %) as linear-ish rgb 0..1 (the band colour of the tabs, used to tint panels and blend bands). */
function hslRgb(h: number): [number, number, number] {
  const s = 0.8;
  const l = 0.6;
  const k = (n: number) => (n + h / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const f = (n: number) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
  return [f(0), f(8), f(4)];
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
/** A season by the month it starts: "Aug ’25". */
const season = (mjd: number) => {
  const d = mjdToDate(mjd);
  return `${MONTHS[d.getUTCMonth()]} ’${String(d.getUTCFullYear()).slice(2)}`;
};

interface Opts {
  trajectory: boolean;
  reference: boolean;
  archive: boolean;
  anomalies: boolean;
  movers: boolean;
  marker: boolean;
  sharp: boolean;
  trim: 'fixed' | 'p999' | 'p995' | 'adaptive';
  boost: boolean;
  sat: boolean;
  rings: boolean;
  morph: boolean;
  mono: boolean;
}

interface Track {
  passes: number[];
  mjd: number[];
  ra: number[];
  dec: number[];
  ra_2025_9: number;
  dec_2025_9: number;
  speed: number;
  pa: number;
  v_east: number;
  v_north: number;
  rms_arcsec: number;
  saturated: boolean;
  check: 'same' | 'none' | 'pending' | 'still';
  gaia: { source_id: string; pm: number; pa?: number; g?: number | null; offset_arcsec: number } | null;
}
interface AnomalyView {
  state: 'waiting' | 'idle' | 'running' | 'done' | 'failed';
  phase?: string;
  done: number;
  total: number;
  error: string;
  result: AnomalyField | null;
}
interface MoversView {
  state: 'waiting' | 'running' | 'done' | 'failed';
  error: string;
  result?: {
    passes: number;
    sources_per_pass: number[];
    stationary: number;
    weak: number;
    still: number;
    tracks: Track[];
    chance?: { trials: number; mean: number; any: number };
  };
}

const CHECK_WORD: Record<string, string> = {
  same: 'Gaia: same motion',
  none: 'no Gaia motion',
  pending: 'not checked yet',
};

/** The Movers rows of the Searches list: the state of the search between passes and one row per track. */
function MoversRows({ mv, names, onPick }: { mv: MoversView | null; names: string[]; onPick: (t: Track, name: string) => void }) {
  const r = mv?.result;
  const state = !mv
    ? 'waiting for the field'
    : mv.state === 'waiting'
      ? 'starts when the field is finished'
      : mv.state === 'failed'
        ? `failed: ${mv.error}`
        : !r
          ? 'searching…'
          : r.passes < 3
            ? `${r.passes} passes: a track needs 3`
            : `${r.tracks.length} track${r.tracks.length === 1 ? '' : 's'} · ${r.sources_per_pass.join(' / ')} sources per pass${r.still ? ` · ${r.still} centroid shift${r.still === 1 ? '' : 's'} rejected` : ''}${r.weak ? ` · ${r.weak} weak` : ''}${r.chance?.trials ? ` · chance ${r.chance.mean.toFixed(2)} tracks per field (${r.chance.trials} trials)` : ''}`;
  return (
    <>
      <div
        class="srow"
        title="Search between passes: sources that move in a straight line over the seasons (3–120 ″/yr), checked against Gaia DR3 afterwards"
      >
        <b>Movers</b>
        <span class="st">{state}</span>
      </div>
      {r?.tracks.map((t, i) => (
        <button
          key={i}
          class="mvrow"
          onClick={() => onPick(t, names[i] ?? '')}
          title="Select this mover: its spectrum is measured along its track"
        >
          <b>{names[i] || 'unnamed'}</b>
          <span>
            {t.speed.toFixed(1)}″/yr toward {t.pa.toFixed(0)}° · {t.passes.length} passes
          </span>
          <span class="faint">{CHECK_WORD[t.check] ?? t.check}</span>
        </button>
      ))}
    </>
  );
}

export function Lab({ route }: { route: Route }) {
  const p = route.params;
  const ra = Number(p['ra']);
  const dec = Number(p['dec']);
  const valid = Number.isFinite(ra) && Number.isFinite(dec);
  const fov = SIZES.includes(Number(p['fov'])) ? Number(p['fov']) : 5;
  const view: View = (['picture', 'bands', 'det'] as const).find((v) => v === p['view']) ?? 'picture';
  const time: Time = (['loop', 'diff', 'sum'] as const).find((v) => v === p['time']) ?? 'loop';
  const film = p['film'] ? Number(p['film']) : null;
  const allBands = BANDS.map((b) => b.key);
  const bands = p['bands'] ? p['bands'].split(',').filter((k) => allBands.includes(k)) : allBands;
  const dets = p['dets']
    ? p['dets']
        .split(',')
        .map(Number)
        .filter((d) => d >= 1 && d <= 6)
    : [1, 2, 3, 4, 5, 6];
  const opts: Opts = {
    reference: p['ref'] !== '0',
    archive: p['arch'] === '1',
    anomalies: p['ano'] !== '0',
    movers: p['movers'] !== '0',
    marker: p['marker'] !== '0',
    sharp: p['sharp'] !== '0',
    trim: p['trim'] === 'p999' || p['trim'] === 'p995' || p['trim'] === 'adaptive' ? p['trim'] : 'fixed',
    boost: p['boost'] === '1',
    sat: p['sat'] !== '0',
    rings: p['rings'] === '1',
    morph: p['morph'] === '1',
    trajectory: p['traj'] !== '0',
    mono: p['blend'] === 'mono',
  };

  const set = (params: Record<string, string | null>) => {
    const next: Record<string, string> = { ...p };
    for (const [k, v] of Object.entries(params)) {
      if (v === null) delete next[k];
      else next[k] = v;
    }
    location.hash = formatHash({ screen: 'lab', params: next });
  };

  const targets = useRef<
    {
      n: string;
      ra: number;
      dec: number;
      pmra: number | null;
      pmdec: number | null;
    }[]
  >([]);
  useEffect(() => {
    void fetch('/catalog.json')
      .then((r) => r.json())
      .then(
        (d: {
          catalog: {
            n: string;
            ra: number;
            dec: number;
            pmra: number | null;
            pmdec: number | null;
            b?: number;
          }[];
        }) => {
          targets.current = d.catalog.filter((o) => o.b);
        },
      )
      .catch(() => {});
  }, []);
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const overlay = useRef<HTMLCanvasElement>(null);
  const clientRef = useRef<FieldClient | null>(null);
  const [side, setSide] = useState(400);
  const [tab, setTab] = useState<Tab>('ctl');
  const [sel, setSel] = useState<Source | null>(null);
  const pictures = useRef(new Map<number, { n: number; rgb: Uint16Array }>());
  const selRef = useRef<Source | null>(null);
  selRef.current = sel;
  const [mv, setMv] = useState<MoversView | null>(null);
  const mvRef = useRef<MoversView | null>(null);
  mvRef.current = mv;
  const [an, setAn] = useState<AnomalyView | null>(null);
  const anRef = useRef<AnomalyView | null>(null);
  anRef.current = an;
  const [selAn, setSelAn] = useState<number | null>(null);
  const [filmBodies, setFilmBodies] = useState<{ state: string; bodies: KnownBody[]; vLimit: number } | null>(null);
  const archive = useRef<ArchiveSet | null>(null);
  const [archTick, setArchTick] = useState(0);
  const [archStep, setArchStep] = useState<number | null>(null);
  const refCanvas = useRef<HTMLCanvasElement>(null);
  const archBig = useRef<HTMLCanvasElement>(null);
  const [phase, setPhase] = useState('');
  const [meta, setMeta] = useState<FieldMeta | null>(null);
  const [shown, setShown] = useState<{ pass: number; stack: StackName } | null>(null);
  const [labels, setLabels] = useState<
    {
      x: number;
      y: number;
      w: number;
      text: string;
      sub: string;
      hue: number;
    }[]
  >([]);
  const [playing, setPlaying] = useState(true);
  const [frame, setFrame] = useState<number | null>(null);
  const [speed, setSpeed] = useState(1);
  const [filmStatus, setFilmStatus] = useState('');
  const [raIn, setRaIn] = useState('');
  const [decIn, setDecIn] = useState('');
  const [stretch, setStretch] = useState(0.4);
  const [recording, setRecording] = useState(false);
  const state = useRef({
    view,
    time,
    bands,
    dets,
    opts,
    playing,
    frame,
    speed,
    stretch,
    film,
    meta: null as FieldMeta | null,
  });
  state.current = {
    view,
    time,
    bands,
    dets,
    opts,
    playing,
    frame,
    speed,
    stretch,
    film,
    meta,
  };

  useEffect(() => {
    if (valid) {
      setRaIn(formatRA(ra));
      setDecIn(formatDec(dec));
      aim.set({ ra, dec, fov }, 'lab');
    }
  }, [ra, dec, fov, valid]);

  // the picture is always square and as large as the free space allows
  useLayoutEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSide(Math.max(160, Math.floor(Math.min(el.clientWidth, el.clientHeight) - 24))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (!valid || !canvas.current) return;
    const r = new Renderer(canvas.current);
    pictures.current.clear();
    setSel(null);
    const pending: { name: string; part: SxpPart }[] = [];
    let diffPart: SxpPart | null = null;
    const balanced = new Set<number>();
    const noised = new Set<number>();
    let trims: { p995: number; p999: number } | null = null;
    let shownPass = 0;
    const uploadDiff = () => {
      if (!diffPart || !r.size || !state.current.meta) return;
      const names = ['white', ...state.current.meta.bands.map((b) => b.key)];
      r.uploadDiff(names.length, new Map(Object.entries(diffPart.arrays).map(([k, a]) => [k, a.data as Uint8Array])), names);
    };
    const place = (name: string, part: SxpPart) => {
      const k = Number(part.meta['pass']);
      const first = Object.values(part.arrays)[0]!;
      const n = first.shape[first.shape.length - 1]!;
      const np = state.current.meta?.passes.length ?? 0;
      if (r.size !== n || r.passes < Math.max(np, k + 1)) {
        if (r.size === n && r.passes > 0) {
          pending.push({ name, part });
          return;
        }
        r.setup(n, Math.max(np, k + 1, 4));
      }
      const stack = name.split('_')[0] as StackName;
      const arr = part.arrays[stack === 'picture' ? 'rgb' : stack === 'quick' ? 'white' : stack]!;
      r.upload(stack, k, arr.data as Uint16Array);
      if (part.arrays['sat']) r.uploadSat(k, part.arrays['sat'].data as Uint8Array);
      if (stack === 'picture' || !noised.has(k)) {
        const lv = passLevels(arr.data as Uint16Array, n, PER_PASS[stack]);
        if (lv) {
          noised.add(k);
          r.setNoise(k, lv.bg, lv.sigma);
          if (stack === 'picture' && k === 0) trims = { p995: lv.p995, p999: lv.p999 };
        }
      }
      if (stack === 'picture') pictures.current.set(k, { n, rgb: arr.data as Uint16Array });
      if (stack === 'picture') {
        const wb = starBalance(arr.data as Uint16Array, n, part.arrays['sat']?.data as Uint8Array | undefined);
        if (wb) {
          balanced.add(k);
          r.setWhiteBalance(k, wb.kr, wb.kb);
        }
      }
    };
    const client = new FieldClient({
      onPhase: setPhase,
      onError: (t) => setPhase(`failed: ${t}`),
      onMeta: (m) => {
        setMeta(m);
        state.current.meta = m;
        r.setDetectorPixel(6.15 / m.grid.step_arcsec);
        for (const q of m.passes) if (!balanced.has(q.index)) r.setWhiteBalance(q.index, q.white_balance.kr, q.white_balance.kb);
        while (pending.length) {
          const x = pending.shift()!;
          place(x.name, x.part);
        }
        uploadDiff();
      },
      onPart: (name, part) => {
        if (name === 'diff') {
          diffPart = part;
          uploadDiff();
          return;
        }
        place(name, part);
      },
    });
    clientRef.current = client;
    void client.open(ra, dec, fov);

    let raf = 0;
    let clock = 0;
    let last = performance.now();
    let lastLabels = '';
    const tick = () => {
      const now = performance.now();
      const st = state.current;
      if (st.playing && st.frame === null) clock += (now - last) * st.speed;
      last = now;
      r.boost = 2.4;
      r.whiteCores = st.opts.sat;
      r.sharp = st.opts.sharp ? 16 : 0;
      r.hi = st.opts.boost
        ? 40
        : st.opts.trim !== 'fixed' && trims
          ? Math.min(400, Math.max(0.8, st.opts.trim === 'p999' ? trims.p999 : st.opts.trim === 'p995' ? trims.p995 : 1.5 * trims.p999))
          : 400;
      r.soft = st.stretch;
      const { w, h } = r.begin();
      const allOn = st.bands.length === BANDS.length;
      let stack: StackName;
      if (st.view === 'picture')
        stack = allOn ? (r.loaded('picture').length >= 2 || r.loaded('quick').length === 0 ? 'picture' : 'quick') : 'bands';
      else stack = st.view === 'bands' ? 'bands' : 'dets';
      const loaded = r.loaded(stack);
      const out: typeof labels = [];
      if (loaded.length) {
        let a = loaded[0]!;
        let b = a;
        let t = 0;
        if (st.time === 'sum') a = b = loaded[0]!;
        else if (st.frame !== null && loaded.includes(st.frame)) a = b = st.frame;
        else if (loaded.length > 1) {
          const seq = order(loaded.length);
          const slot = HOLD_MS + FADE_MS;
          const k = Math.floor(clock / slot) % seq.length;
          const within = clock % slot;
          a = loaded[seq[k]!]!;
          b = loaded[seq[(k + 1) % seq.length]!]!;
          if (within >= HOLD_MS && st.opts.morph) {
            const x = (within - HOLD_MS) / FADE_MS;
            t = x * x * (3 - 2 * x);
          } else if (within >= HOLD_MS) {
            t = 0;
          }
        }
        const timeMode = st.time === 'diff' ? 'difference' : st.time === 'sum' ? 'coadd' : 'pendulum';
        let panels: (Panel & { label?: string; sub?: string; hue?: number })[] = [];
        if (st.view === 'picture') {
          const mask = BANDS.reduce((m, bd, i) => (st.bands.includes(bd.key) ? m | (1 << i) : m), 0);
          panels = [
            stack === 'bands'
              ? {
                  x: 0,
                  y: 0,
                  w,
                  h,
                  stack,
                  mode: 'blend',
                  bandMask: mask,
                  hues: BANDS.map((bd) => hslRgb(bd.hue)),
                  mono: st.opts.mono,
                  time: timeMode,
                  diffLayer: 0,
                }
              : {
                  x: 0,
                  y: 0,
                  w,
                  h,
                  stack,
                  mode: stack === 'picture' && !st.opts.mono ? 'colour' : 'layer',
                  time: timeMode,
                  diffLayer: 0,
                },
          ];
          if (stack === 'picture' && st.opts.mono) panels[0]!.stack = r.loaded('quick').length ? 'quick' : 'picture';
        } else if (st.view === 'bands') {
          const shownBands = BANDS.map((bd, i) => ({ bd, i })).filter((x) => st.bands.includes(x.bd.key));
          panels = wallLayout(shownBands.length, w, h).map((c, j) => {
            const { bd, i } = shownBands[j]!;
            return {
              x: c.x,
              y: c.y + 22,
              w: c.size,
              h: c.size - 22,
              stack,
              mode: 'layer',
              layer: i,
              tint: hslRgb(bd.hue),
              noData: [0, 0, 0],
              time: timeMode,
              diffLayer: i + 1,
              label: bd.name,
              sub: bd.band,
              hue: bd.hue,
            };
          });
        } else {
          const shownDets = st.dets.slice().sort();
          panels = wallLayout(shownDets.length, w, h).map((c, j) => {
            const d = shownDets[j]! - 1;
            return {
              x: c.x,
              y: c.y + 22,
              w: c.size,
              h: c.size - 22,
              stack,
              mode: 'layer',
              layer: d,
              noData: [0, 0, 0],
              time: timeMode,
              diffLayer: 0,
              label: `D${d + 1} ${DETECTORS[d]!.name}`,
              sub: DETECTORS[d]!.range,
              hue: DETECTORS[d]!.hue,
            };
          });
        }
        shownPass = t < 0.5 ? a : b;
        for (const q of panels) {
          r.panel(q, a, b, t);
          if (q.label)
            out.push({
              x: q.x,
              y: q.y - 20,
              w: q.w,
              text: q.label,
              sub: q.sub ?? '',
              hue: q.hue ?? 0,
            });
        }
        setShown((s) => (s && s.pass === a && s.stack === stack ? s : { pass: a, stack }));
      }
      drawRings();
      const key = JSON.stringify(out);
      if (key !== lastLabels) {
        lastLabels = key;
        setLabels(out);
      }
      raf = requestAnimationFrame(tick);
    };

    const drawRings = () => {
      const ov = overlay.current;
      const st = state.current;
      if (!ov) return;
      const wcss = ov.clientWidth;
      const hcss = ov.clientHeight;
      if (ov.width !== Math.round(wcss * devicePixelRatio)) {
        ov.width = Math.round(wcss * devicePixelRatio);
        ov.height = Math.round(hcss * devicePixelRatio);
      }
      const ctx = ov.getContext('2d')!;
      ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
      ctx.clearRect(0, 0, wcss, hcss);
      if (st.film !== null) return; // the film draws its own picture and marks
      const m = st.meta;
      if (m && st.opts.marker && st.view === 'picture' && targets.current.length) {
        // the marker: a ring and the name of a test target at its place on the date of the season shown
        const q = m.passes[shownPass] ?? m.passes[0]!;
        const year = 2000 + (q.mjd_mid - 51544.5) / 365.25;
        const scale = wcss / m.grid.n;
        for (const o of targets.current) {
          const [tra, tdec] = atEpoch(o.ra, o.dec, o.pmra ?? 0, o.pmdec ?? 0, year);
          const dx = (-(((tra - m.grid.ra0 + 540) % 360) - 180) * Math.cos((m.grid.dec0 * Math.PI) / 180) * 3600) / m.grid.step_arcsec;
          const dy = (-(tdec - m.grid.dec0) * 3600) / m.grid.step_arcsec;
          const x = (m.grid.n / 2 + dx) * scale;
          const y = (m.grid.n / 2 + dy) * scale;
          if (x < 0 || y < 0 || x > wcss || y > hcss) continue;
          const rr = 18;
          ctx.save();
          ctx.lineWidth = 3.5;
          ctx.strokeStyle = 'rgba(0,0,0,0.7)';
          const ring = () => {
            ctx.beginPath();
            ctx.arc(x, y, rr, 0, 2 * Math.PI);
            for (const [ax, ay] of [
              [1, 0],
              [-1, 0],
              [0, 1],
              [0, -1],
            ] as const) {
              ctx.moveTo(x + ax * (rr - 4), y + ay * (rr - 4));
              ctx.lineTo(x + ax * (rr + 6), y + ay * (rr + 6));
            }
            ctx.stroke();
          };
          ring();
          ctx.lineWidth = 1.6;
          ctx.strokeStyle = '#7ff0e4';
          ring();
          ctx.font = '600 11px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
          ctx.lineWidth = 3;
          ctx.strokeStyle = '#000';
          ctx.fillStyle = '#fff';
          ctx.strokeText(o.n, x + rr + 8, y - rr * 0.35);
          ctx.fillText(o.n, x + rr + 8, y - rr * 0.35);
          if (st.opts.trajectory && m.passes.length > 1) {
            // the trajectory: where the target is on the date of every season, joined
            const pts = m.passes.map((pp) => {
              const [pra, pdec] = atEpoch(o.ra, o.dec, o.pmra ?? 0, o.pmdec ?? 0, 2000 + (pp.mjd_mid - 51544.5) / 365.25);
              return [
                (m.grid.n / 2 -
                  ((((pra - m.grid.ra0 + 540) % 360) - 180) * Math.cos((m.grid.dec0 * Math.PI) / 180) * 3600) / m.grid.step_arcsec) *
                  scale,
                (m.grid.n / 2 - ((pdec - m.grid.dec0) * 3600) / m.grid.step_arcsec) * scale,
              ] as const;
            });
            ctx.setLineDash([3, 3]);
            ctx.lineWidth = 1.4;
            ctx.strokeStyle = 'rgba(127,240,228,0.9)';
            ctx.beginPath();
            pts.forEach(([px, py], i) => (i ? ctx.lineTo(px, py) : ctx.moveTo(px, py)));
            ctx.stroke();
            ctx.setLineDash([]);
            ctx.fillStyle = '#7ff0e4';
            for (const [px, py] of pts) {
              ctx.beginPath();
              ctx.arc(px, py, 2.4, 0, 2 * Math.PI);
              ctx.fill();
            }
          }
          ctx.restore();
        }
      }
      const sl = selRef.current;
      if (m && sl && !sl.name && st.view === 'picture') {
        const scale = wcss / m.grid.n;
        const x =
          (m.grid.n / 2 -
            ((((sl.ra - m.grid.ra0 + 540) % 360) - 180) * Math.cos((m.grid.dec0 * Math.PI) / 180) * 3600) / m.grid.step_arcsec) *
          scale;
        const y = (m.grid.n / 2 - ((sl.dec - m.grid.dec0) * 3600) / m.grid.step_arcsec) * scale;
        ctx.save();
        ctx.lineWidth = 3.5;
        ctx.strokeStyle = 'rgba(0,0,0,0.7)';
        ctx.beginPath();
        ctx.arc(x, y, 14, 0, 2 * Math.PI);
        ctx.stroke();
        ctx.lineWidth = 1.6;
        ctx.strokeStyle = '#ffb454';
        ctx.stroke();
        ctx.restore();
      }
      const mvv = mvRef.current;
      if (m && st.opts.movers && st.view === 'picture' && mvv?.result) {
        const scale = wcss / m.grid.n;
        const toXY = (ra: number, dec: number): [number, number] => [
          (m.grid.n / 2 - ((((ra - m.grid.ra0 + 540) % 360) - 180) * Math.cos((m.grid.dec0 * Math.PI) / 180) * 3600) / m.grid.step_arcsec) *
            scale,
          (m.grid.n / 2 - ((dec - m.grid.dec0) * 3600) / m.grid.step_arcsec) * scale,
        ];
        const q = m.passes[shownPass] ?? m.passes[0]!;
        for (const t of mvv.result.tracks) {
          const pts = t.ra.map((r0, i) => toXY(r0, t.dec[i]!));
          const yrs = (q.mjd_mid - 51544.5) / 365.25 + 2000 - 2025.9;
          const decN = t.dec_2025_9 + (t.v_north * yrs) / 3600;
          const [x, y] = toXY(t.ra_2025_9 + (t.v_east * yrs) / 3600 / Math.cos((decN * Math.PI) / 180), decN);
          ctx.save();
          ctx.setLineDash(t.check === 'pending' ? [4, 4] : []);
          ctx.strokeStyle = 'rgba(0,0,0,0.6)';
          ctx.lineWidth = 3.5;
          const path = () => {
            ctx.beginPath();
            pts.forEach(([px, py], i) => (i ? ctx.lineTo(px, py) : ctx.moveTo(px, py)));
            ctx.stroke();
            ctx.beginPath();
            ctx.arc(x, y, 22, 0, 2 * Math.PI);
            ctx.stroke();
          };
          path();
          ctx.strokeStyle = '#ffb454';
          ctx.lineWidth = 1.6;
          path();
          ctx.setLineDash([]);
          ctx.fillStyle = '#ffb454';
          for (const [px, py] of pts) {
            ctx.beginPath();
            ctx.arc(px, py, 2.2, 0, 2 * Math.PI);
            ctx.fill();
          }
          ctx.font = '600 11px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
          ctx.lineWidth = 3;
          ctx.strokeStyle = '#000';
          ctx.fillStyle = '#ffd08a';
          const label = `${t.speed.toFixed(1)}″/yr`;
          ctx.strokeText(label, x + 26, y + 16);
          ctx.fillText(label, x + 26, y + 16);
          ctx.restore();
        }
      }
      const anv = anRef.current;
      if (m && st.opts.anomalies && st.view === 'picture' && anv?.result) {
        const scale = wcss / m.grid.n;
        const q = m.passes[shownPass] ?? m.passes[0]!;
        const year = 2000 + (q.mjd_mid - 51544.5) / 365.25;
        for (const a of anv.result.anomalies) {
          const [ara, adec] = atEpoch(a.ra, a.dec, a.pmra, a.pmdec, year);
          const x =
            (m.grid.n / 2 -
              ((((ara - m.grid.ra0 + 540) % 360) - 180) * Math.cos((m.grid.dec0 * Math.PI) / 180) * 3600) / m.grid.step_arcsec) *
            scale;
          const y = (m.grid.n / 2 - ((adec - m.grid.dec0) * 3600) / m.grid.step_arcsec) * scale;
          ctx.save();
          ctx.lineWidth = 3.5;
          ctx.strokeStyle = 'rgba(0,0,0,0.6)';
          ctx.beginPath();
          ctx.arc(x, y, 16, 0, 2 * Math.PI);
          ctx.stroke();
          ctx.lineWidth = 1.6;
          ctx.strokeStyle = '#ff7ac8';
          ctx.stroke();
          ctx.font = '600 11px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
          ctx.lineWidth = 3;
          ctx.strokeStyle = '#000';
          ctx.fillStyle = '#ffb0dc';
          ctx.strokeText(String(a.n), x + 15, y - 12);
          ctx.fillText(String(a.n), x + 15, y - 12);
          ctx.restore();
        }
      }
      if (!st.opts.rings || !m || st.view !== 'picture' || m.passes.length < 2) return;
      // speed rings: how far a body of that speed moves between the first and the last pass, centred on the field
      const dt = (m.passes[m.passes.length - 1]!.mjd_mid - m.passes[0]!.mjd_mid) / 365.25;
      const fovArcsec = m.grid.n * m.grid.step_arcsec;
      ctx.strokeStyle = 'rgba(255,180,84,0.75)';
      ctx.fillStyle = 'rgba(255,200,130,0.95)';
      ctx.font = '600 10px ui-monospace, Menlo, monospace';
      ctx.lineWidth = 1;
      for (const v of [1, 3, 10, 30, 100, 300]) {
        const rpx = ((v * dt) / fovArcsec) * wcss;
        if (rpx < 14 || rpx > wcss * 0.72) continue;
        ctx.beginPath();
        ctx.setLineDash([4, 4]);
        ctx.arc(wcss / 2, hcss / 2, rpx, 0, 2 * Math.PI);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillText(`${v}″/yr`, wcss / 2 + rpx * 0.7071 + 3, hcss / 2 - rpx * 0.7071 - 3);
      }
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
    if (view === 'bands' || bands.length < BANDS.length || fov <= 10) c.want('bands_');
    if (view === 'det' || fov <= 10) c.want('dets_');
    if (time === 'diff') c.want('diff');
  }, [view, time, fov, meta, bands.length]);

  // the search between passes: asked for once the field is finished; polled until its Gaia checks are done
  useEffect(() => {
    setMv(null);
    if (!valid || !meta) return;
    let stop = false;
    const go = async () => {
      let pause = 3000;
      for (let i = 0; i < 80 && !stop; i++) {
        try {
          const r = await fetch(`/api/v1/movers?ra=${ra}&dec=${dec}&fov=${fov}`, { cache: 'no-store' });
          if (r.ok) {
            const v = (await r.json()) as MoversView;
            if (stop) return;
            setMv(v);
            const pending = v.result?.tracks.some((t) => t.check === 'pending');
            if (v.state === 'failed' || (v.state === 'done' && !pending)) return;
          }
        } catch {
          /* the next round asks again */
        }
        await new Promise((res) => setTimeout(res, pause));
        pause = Math.min(20000, pause * 1.5);
      }
    };
    void go();
    return () => {
      stop = true;
    };
  }, [ra, dec, fov, valid, meta]);
  const trackNames = (mv?.result?.tracks ?? []).map((t) => {
    for (const o of targets.current) {
      const [tra, tdec] = atEpoch(o.ra, o.dec, o.pmra ?? 0, o.pmdec ?? 0, 2025.9);
      if (Math.hypot((tra - t.ra_2025_9) * Math.cos((tdec * Math.PI) / 180), tdec - t.dec_2025_9) * 3600 < 12) return o.n;
    }
    return t.gaia ? `Gaia DR3 ${t.gaia.source_id}` : '';
  });
  const pickTrack = (t: Track, name: string) => {
    // the spectrum of a mover: measured along its own track (motion from the search, place at 2025.9)
    const years = 2025.9 - 2000;
    const dec0 = t.dec_2025_9 - (t.v_north * years) / 3600;
    const ra0 = t.ra_2025_9 - (t.v_east * years) / 3600 / Math.cos((t.dec_2025_9 * Math.PI) / 180);
    setSel({ ra: (ra0 + 360) % 360, dec: dec0, name: name || undefined, pmra: t.v_east * 1000, pmdec: t.v_north * 1000 });
    setTab('spec');
  };

  // the spectral anomaly map: its state when the field opens (a finished map is shown at once); started by the button
  const anPoll = useRef(0);
  const followAnomalies = async (startIt: boolean) => {
    const my = ++anPoll.current;
    let first = startIt;
    for (let i = 0; i < 2000 && my === anPoll.current; i++) {
      try {
        const r = await fetch(`/api/v1/anomalies?ra=${ra}&dec=${dec}&fov=${fov}${first ? '&start=1' : ''}`, { cache: 'no-store' });
        first = false;
        if (r.ok) {
          const v = (await r.json()) as AnomalyView;
          if (my !== anPoll.current) return;
          setAn(v);
          if (v.state !== 'running' && v.state !== 'waiting') return;
        }
      } catch {
        /* asked again on the next round */
      }
      await new Promise((res) => setTimeout(res, 3000));
    }
  };
  useEffect(() => {
    setAn(null);
    setSelAn(null);
    if (!valid || !meta) return;
    void followAnomalies(false);
    return () => {
      anPoll.current++;
    };
  }, [ra, dec, fov, valid, meta]);
  const startAnomalies = () => followAnomalies(true);
  const pickAnomaly = (n: number) => {
    const a = an?.result?.anomalies.find((x) => x.n === n);
    if (!a) return;
    setSel({ ra: a.ra, dec: a.dec, name: `Anomaly ${n}`, pmra: a.pmra || undefined, pmdec: a.pmdec || undefined });
    setSelAn(n);
    setTab('obj');
  };

  // archive images of the field (Reference inset and archive frames): cutouts of older surveys, made once per field
  const fieldKey = meta ? `${meta.grid.ra0},${meta.grid.dec0},${meta.grid.n},${meta.grid.step_arcsec}` : '';
  useEffect(() => {
    if (!meta) return;
    const set_ = new ArchiveSet(meta.grid.ra0, meta.grid.dec0, (meta.grid.n * meta.grid.step_arcsec) / 60, 400, () =>
      setArchTick((t) => t + 1),
    );
    archive.current = set_;
    return () => {
      set_.dispose();
      archive.current = null;
    };
  }, [fieldKey]);
  // the test targets on an archive picture: where they were at the survey's epoch
  const drawTargets = (ctx: CanvasRenderingContext2D, size: number, epoch: number, ring: number) => {
    if (!meta) return;
    const scale = size / meta.grid.n;
    for (const o of targets.current) {
      const [tra, tdec] = atEpoch(o.ra, o.dec, o.pmra ?? 0, o.pmdec ?? 0, epoch);
      const x =
        (meta.grid.n / 2 -
          ((((tra - meta.grid.ra0 + 540) % 360) - 180) * Math.cos((meta.grid.dec0 * Math.PI) / 180) * 3600) / meta.grid.step_arcsec) *
        scale;
      const y = (meta.grid.n / 2 - ((tdec - meta.grid.dec0) * 3600) / meta.grid.step_arcsec) * scale;
      if (x < -ring || y < -ring || x > size + ring || y > size + ring) continue;
      ctx.lineWidth = 3;
      ctx.strokeStyle = 'rgba(0,0,0,0.7)';
      ctx.beginPath();
      ctx.arc(x, y, ring, 0, 2 * Math.PI);
      ctx.stroke();
      ctx.lineWidth = 1.4;
      ctx.strokeStyle = '#ffb454';
      ctx.stroke();
    }
  };
  useEffect(() => {
    const a = archive.current;
    const c = refCanvas.current;
    if (a && c) {
      const ctx = c.getContext('2d')!;
      ctx.drawImage(a.canvases.get('dss2red')!, 0, 0, c.width, c.height);
      drawTargets(ctx, c.width, ARCHIVE[0]!.epoch, 7);
    }
    const big = archBig.current;
    if (a && big && archStep !== null && archStep < ARCHIVE.length) {
      const s = ARCHIVE[archStep]!;
      const px = Math.round(side * Math.min(2, devicePixelRatio || 1));
      if (big.width !== px) big.width = big.height = px;
      const ctx = big.getContext('2d')!;
      ctx.imageSmoothingEnabled = true;
      ctx.drawImage(a.canvases.get(s.id)!, 0, 0, px, px);
      drawTargets(ctx, px, s.epoch, 18 * (px / side));
    }
  }, [archTick, archStep, opts.reference, side, meta]);
  // archive frames blinked before the SPHEREx seasons: archive 1..k, then season 1..N, again
  useEffect(() => {
    if (!opts.archive || !meta || film !== null) {
      setArchStep(null);
      return;
    }
    const n = meta.passes.length;
    let step = 0;
    const show = () => {
      setArchStep(step);
      if (step >= ARCHIVE.length) setFrame(step - ARCHIVE.length);
    };
    show();
    const t = setInterval(
      () => {
        if (!state.current.playing) return;
        step = (step + 1) % (ARCHIVE.length + n);
        show();
      },
      Math.round(1100 / Math.max(0.25, speed)),
    );
    return () => {
      clearInterval(t);
      setArchStep(null);
      setFrame(null);
    };
  }, [opts.archive, meta, film, speed]);

  // keyboard: space plays / pauses, arrows step through the passes
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === 'INPUT') return;
      const n = meta?.passes.length ?? 0;
      if (e.code === 'Space') {
        e.preventDefault();
        setFrame(null);
        setPlaying((x) => !x);
      } else if ((e.code === 'ArrowRight' || e.code === 'ArrowLeft') && n) {
        setPlaying(false);
        setFrame((f) => ((f ?? shown?.pass ?? 0) + (e.code === 'ArrowRight' ? 1 : n - 1)) % n);
      }
    };
    window.addEventListener('keydown', on);
    return () => window.removeEventListener('keydown', on);
  }, [meta, shown]);

  if (!valid) {
    return (
      <div class="screen">
        <div
          class="lab"
          style={{
            display: 'grid',
            placeItems: 'center',
            color: 'var(--muted)',
          }}
        >
          <span>
            <a href="#/globe">Open a place from the globe</a>
          </span>
        </div>
      </div>
    );
  }

  /** One full cycle of the seasons, recorded from the picture as it plays, saved as a WebM file. */
  const recordVideo = async () => {
    const c = canvas.current;
    if (!c || typeof MediaRecorder === 'undefined') return;
    setRecording(true);
    setFrame(null);
    setPlaying(true);
    const stream = c.captureStream(30);
    const type = MediaRecorder.isTypeSupported('video/webm;codecs=vp9') ? 'video/webm;codecs=vp9' : 'video/webm';
    const rec = new MediaRecorder(stream, {
      mimeType: type,
      videoBitsPerSecond: 8e6,
    });
    const chunks: Blob[] = [];
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    const done = new Promise<void>((res) => (rec.onstop = () => res()));
    rec.start();
    const n = Math.max(2, passes.length);
    await new Promise((res) => setTimeout(res, (order(n).length * (HOLD_MS + FADE_MS)) / speed + 200));
    rec.stop();
    await done;
    const url = URL.createObjectURL(new Blob(chunks, { type: 'video/webm' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = `spherex-${formatRA(ra, true)}${formatDec(dec, true)}-${fov}arcmin.webm`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
    setRecording(false);
  };
  /** A click on the picture selects the source there: a test target when one is within 12″ (measured with its motion), else the
   *  brightest pixel within 3 grid pixels of the click in the season shown. */
  const pick = (e: MouseEvent) => {
    const m = meta;
    if (!m || film !== null || view !== 'picture') return;
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    let gx = ((e.clientX - rect.left) / rect.width) * m.grid.n;
    let gy = ((e.clientY - rect.top) / rect.height) * m.grid.n;
    const toSky = (x: number, y: number): [number, number] => [
      (m.grid.ra0 - ((x - m.grid.n / 2) * m.grid.step_arcsec) / 3600 / Math.cos((m.grid.dec0 * Math.PI) / 180) + 360) % 360,
      m.grid.dec0 - ((y - m.grid.n / 2) * m.grid.step_arcsec) / 3600,
    ];
    const [cra, cdec] = toSky(gx, gy);
    const q = passes.find((x) => x.index === (shown?.pass ?? 0)) ?? passes[0];
    const year = q ? 2000 + (q.mjd_mid - 51544.5) / 365.25 : 2025.9;
    for (const o of targets.current) {
      const [tra, tdec] = atEpoch(o.ra, o.dec, o.pmra ?? 0, o.pmdec ?? 0, year);
      if (Math.hypot((tra - cra) * Math.cos((cdec * Math.PI) / 180), tdec - cdec) * 3600 < 12) {
        setSel({
          ra: o.ra,
          dec: o.dec,
          name: o.n,
          pmra: o.pmra ?? 0,
          pmdec: o.pmdec ?? 0,
        });
        setTab('spec');
        return;
      }
    }
    const pic = pictures.current.get(shown?.pass ?? 0) ?? pictures.current.values().next().value;
    if (pic) {
      const N = pic.n * pic.n;
      let best = -Infinity;
      let bx = gx;
      let by = gy;
      for (let dy = -3; dy <= 3; dy++)
        for (let dx = -3; dx <= 3; dx++) {
          const x = Math.floor(gx) + dx;
          const y = Math.floor(gy) + dy;
          if (x < 0 || y < 0 || x >= pic.n || y >= pic.n || dx * dx + dy * dy > 9) continue;
          const i = y * pic.n + x;
          const v = halfToFloat(pic.rgb[i]!) + halfToFloat(pic.rgb[N + i]!) + halfToFloat(pic.rgb[2 * N + i]!);
          if (v > best) {
            best = v;
            bx = x + 0.5;
            by = y + 0.5;
          }
        }
      gx = bx;
      gy = by;
    }
    const [ra2, dec2] = toSky(gx, gy);
    setSelAn(null);
    setSel({ ra: ra2, dec: dec2 });
    setTab('spec');
  };
  const goAim = () => {
    const a = parseRA(raIn);
    const d = parseDec(decIn);
    if (a === null || d === null) return;
    set({ ra: a.toFixed(5), dec: d.toFixed(5) });
  };
  const toggleBand = (key: string) => {
    const next = bands.includes(key) ? bands.filter((k) => k !== key) : [...bands, key];
    set({
      bands: next.length === BANDS.length ? null : next.length ? next.join(',') : BANDS[0]!.key,
    });
  };
  const toggleDet = (d: number) => {
    const next = dets.includes(d) ? dets.filter((x) => x !== d) : [...dets, d];
    set({
      dets: next.length === 6 ? null : next.length ? next.sort().join(',') : '1',
    });
  };
  const passes = meta?.passes ?? [];
  const passMeta = passes.find((q) => q.index === shown?.pass);
  const colourWord =
    shown?.stack === 'picture'
      ? 'spectral colour'
      : shown?.stack === 'quick'
        ? 'white light'
        : bands.length < BANDS.length && view === 'picture'
          ? opts.mono
            ? 'chosen bands, mono sum'
            : 'chosen bands'
          : shown?.stack === 'bands'
            ? 'bands'
            : 'detectors';
  const seq =
    passes.length > 1
      ? order(passes.length)
          .map((i) => i + 1)
          .join('→') +
        '→' +
        '1'
      : '';
  const status =
    phase && !(shown && meta)
      ? phase
      : archStep !== null && archStep < ARCHIVE.length
        ? `Archive · ${ARCHIVE[archStep]!.name} · ${ARCHIVE[archStep]!.years} · then the SPHEREx seasons`
        : shown && meta
          ? `${time === 'sum' ? 'All seasons co-added' : `Season ${shown.pass + 1} of ${passes.length} · ${passMeta ? season(passMeta.mjd0) : ''}`} · ${colourWord}${time === 'diff' ? (shown.pass === 0 ? ' · Difference: reference season' : ' · Difference against season 1') : ''} · ${meta.exposures} exposures${time === 'loop' && seq ? ` · passes ${seq.slice(0, -2)}` : ''}${phase ? ` · ${phase}` : ''}`
          : phase;
  const frameIndex = frame ?? shown?.pass ?? 0;
  const fName = 'Sky field';
  const fCls = ` · ${formatRA(ra)} ${formatDec(dec)}`;

  const dchk = (id: string, ico: string, text: string, title: string, checked: boolean, onChange: ((v: boolean) => void) | null) => (
    <label
      class={`chk ${onChange ? '' : 'disabled'}`}
      title={onChange ? title : `${title} (available once the searches of this field are built)`}
    >
      <input
        type="checkbox"
        id={id}
        checked={checked}
        disabled={!onChange}
        onChange={(e) => onChange?.((e.target as HTMLInputElement).checked)}
      />
      <span dangerouslySetInnerHTML={{ __html: icon(ico, 16) }} style={{ display: 'contents' }} />
      {text}
    </label>
  );

  return (
    <div class="screen">
      <div class="lab">
        <div class="vwrap" ref={wrap}>
          <div
            class="vsq"
            style={{
              width: `${side}px`,
              height: `${side}px`,
              cursor: view === 'picture' && film === null ? 'crosshair' : undefined,
            }}
            onClick={pick}
          >
            <canvas
              ref={canvas}
              class="vid"
              role="img"
              aria-label="Video of the field built from real SPHEREx exposures"
              data-testid="picture"
              style={film !== null ? { visibility: 'hidden' } : undefined}
            />
            <canvas ref={overlay} class="ov" />
            {film === null &&
              labels.map((l) => (
                <div
                  class="panel-label"
                  style={{
                    left: `${l.x + 2}px`,
                    top: `${l.y + 2}px`,
                    maxWidth: `${l.w - 4}px`,
                  }}
                >
                  <span
                    class="sw"
                    style={{
                      background: `hsl(${l.hue},80%,60%)`,
                      boxShadow: `0 0 8px hsl(${l.hue},80%,60%)`,
                    }}
                  />
                  <b>{l.text}</b>
                  <em>{l.sub}</em>
                </div>
              ))}
            {film === null && view === 'picture' && archStep !== null && archStep < ARCHIVE.length && (
              <canvas ref={archBig} class="archive-big" />
            )}
            {film === null && view === 'picture' && opts.reference && !(archStep !== null && archStep < ARCHIVE.length) && (
              <div class="refbox" title="DSS2 red (POSS-II plates, 1985–1999) of the same field">
                <canvas ref={refCanvas} width={150} height={150} />
                <span>DSS2 red · 1985–1999</span>
              </div>
            )}
            {film === null && view === 'picture' && archStep !== null && archStep < ARCHIVE.length ? (
              <div class="vlabel">
                {ARCHIVE[archStep]!.name} · {ARCHIVE[archStep]!.years}
              </div>
            ) : (
              film === null &&
              view === 'picture' &&
              passMeta && <div class="vlabel">{time === 'sum' ? 'all seasons' : `SPHEREx · ${season(passMeta.mjd0)}`}</div>
            )}
            {film !== null && (
              <div style={{ position: 'absolute', inset: 0 }}>
                <FilmView
                  ra={ra}
                  dec={dec}
                  fov={fov}
                  mjd={film || undefined}
                  playing={playing}
                  onStatus={setFilmStatus}
                  onBodies={setFilmBodies}
                />
              </div>
            )}
          </div>
        </div>
        <aside class="panel">
          <div class="panel-head">
            <div class="ptitle">
              <b>{fName}</b>
              <span>{fCls}</span>
            </div>
            <div class="seg sm" role="group" aria-label="Panel">
              {(
                [
                  ['ctl', 'Controls'],
                  ['obj', 'Object'],
                  ['spec', 'Spectrum'],
                ] as const
              ).map(([k, n]) => (
                <button key={k} class={tab === k ? 'on' : ''} aria-pressed={tab === k} onClick={() => setTab(k)}>
                  {n}
                </button>
              ))}
            </div>
          </div>
          {tab === 'ctl' && (
            <div class="pbody">
              <section class="aim">
                <div class="coords">
                  <div class="field">
                    <label for="raIn">RA</label>
                    <input
                      id="raIn"
                      class={`inp coord ${parseRA(raIn) === null ? 'bad' : ''}`}
                      spellcheck={false}
                      value={raIn}
                      onInput={(e) => setRaIn((e.target as HTMLInputElement).value)}
                      onKeyDown={(e) => e.key === 'Enter' && goAim()}
                      onBlur={goAim}
                    />
                  </div>
                  <div class="field">
                    <label for="decIn">Dec</label>
                    <input
                      id="decIn"
                      class={`inp coord ${parseDec(decIn) === null ? 'bad' : ''}`}
                      spellcheck={false}
                      value={decIn}
                      onInput={(e) => setDecIn((e.target as HTMLInputElement).value)}
                      onKeyDown={(e) => e.key === 'Enter' && goAim()}
                      onBlur={goAim}
                    />
                  </div>
                </div>
                <div class="field">
                  <label>FOV</label>
                  <div class="seg fov">
                    {SIZES.map((s) => (
                      <button
                        key={s}
                        class={s === fov ? 'on' : ''}
                        onClick={() => set({ fov: String(s) })}
                        data-testid={`fov-${s}`}
                        title={`${s}′ field`}
                      >
                        {s}′
                      </button>
                    ))}
                  </div>
                </div>
              </section>
              <div class="vswitch">
                {VIEWS.map((v) => (
                  <button
                    key={v.id}
                    class={v.id === view && film === null ? 'on' : ''}
                    title={v.tip}
                    onClick={() =>
                      set({
                        view: v.id === 'picture' ? null : v.id,
                        film: null,
                      })
                    }
                    data-testid={`view-${v.id}`}
                  >
                    <span dangerouslySetInnerHTML={{ __html: v.icon }} style={{ display: 'contents' }} />
                    <span>
                      <b>{v.name}</b>
                    </span>
                  </button>
                ))}
              </div>
              <div class="seg" role="group" aria-label="Time">
                {(
                  [
                    ['loop', 'eye', 'Blink', 'Blink: seasons play one after another. Look for sources that move or vary'],
                    [
                      'diff',
                      'plus-minus',
                      'Difference',
                      'Difference against season 1: per-pixel significance, |z| > 4 (cyan fainter, magenta brighter)',
                    ],
                    ['sum', 'sigma', 'Co-add', 'Co-add all seasons into one frame: the faintest sources emerge'],
                  ] as const
                ).map(([k, ico, n, tip]) => (
                  <button
                    key={k}
                    class={time === k ? 'on' : ''}
                    title={tip}
                    onClick={() => set({ time: k === 'loop' ? null : k })}
                    data-testid={`time-${k}`}
                  >
                    <span dangerouslySetInnerHTML={{ __html: icon(ico, 16) }} style={{ display: 'contents' }} />
                    {n}
                  </button>
                ))}
              </div>
              {time === 'diff' && (
                <div class="diffkey">
                  <span>
                    <span class="sw" style={{ background: 'var(--magenta)' }} />
                    brighter
                  </span>
                  <span>
                    <span class="sw" style={{ background: 'var(--cyan)' }} />
                    fainter
                  </span>
                </div>
              )}
              {view === 'det' ? (
                <>
                  <div class="sechead">
                    <span class="h2">Detectors</span>
                    <span>
                      <button class="lnk" onClick={() => set({ dets: null })}>
                        All
                      </button>
                    </span>
                  </div>
                  <div class="bgrid">
                    {DETECTORS.map((d, i) => (
                      <button
                        key={i}
                        class={`btab ${dets.includes(i + 1) ? 'on' : ''}`}
                        style={{ '--c': `hsl(${d.hue},80%,60%)` }}
                        aria-pressed={dets.includes(i + 1)}
                        onClick={() => toggleDet(i + 1)}
                        title={`Detector D${i + 1}: ${d.name}, ${d.range}`}
                      >
                        <span class="tx">
                          <b>
                            D{i + 1} · {d.name}
                          </b>
                          <em>{d.range}</em>
                        </span>
                      </button>
                    ))}
                  </div>
                </>
              ) : (
                <>
                  <div class="sechead">
                    <span class="h2">Spectral bands</span>
                    <span style={{ display: 'flex', gap: '12px' }}>
                      <button class="lnk" onClick={() => set({ bands: null })} title="Switch every band on">
                        All
                      </button>
                      <button class="lnk" onClick={() => set({ bands: BANDS[0]!.key })} title="Only Starlight">
                        None
                      </button>
                    </span>
                  </div>
                  <div class="bgrid">
                    {BANDS.map((bd) => (
                      <button
                        key={bd.key}
                        class={`btab ${bands.includes(bd.key) ? 'on' : ''}`}
                        style={{ '--c': `hsl(${bd.hue},80%,60%)` }}
                        aria-pressed={bands.includes(bd.key)}
                        onClick={() => toggleBand(bd.key)}
                        data-testid={`band-${bd.key}`}
                      >
                        <span
                          class="ti"
                          dangerouslySetInnerHTML={{
                            __html: icon(bd.icon, 20),
                          }}
                        />
                        <span class="tx">
                          <b>{bd.name}</b>
                          <em>{bd.band}</em>
                        </span>
                      </button>
                    ))}
                    <button
                      class="btab mask"
                      style={{ '--c': 'hsl(0,0%,70%)' }}
                      disabled
                      title="Artifact mask: where the SPHEREx pipeline flagged the pixel in many exposures (available once the mask view is built)"
                    >
                      <span
                        class="ti"
                        dangerouslySetInnerHTML={{
                          __html: icon('warning', 20),
                        }}
                      />
                      <span class="tx">
                        <b>Artifact mask</b>
                        <em>flagged pixels</em>
                      </span>
                    </button>
                  </div>
                </>
              )}
              <div class="dgroup">
                <div class="sechead">
                  <span class="h2">Display</span>
                </div>
                <div class="dchecks">
                  {dchk(
                    'boostChk',
                    'sun',
                    'Brighten',
                    'Brighten: a lower white point, so faint sources stand out (bright stars saturate)',
                    opts.boost,
                    (v) => set({ boost: v ? '1' : null }),
                  )}
                  {dchk(
                    'satChk',
                    'sparkle',
                    'White cores',
                    'Saturated cores white: pixels saturated in most exposures are drawn white, never as missing data',
                    opts.sat,
                    (v) => set({ sat: v ? null : '0' }),
                  )}
                  {dchk(
                    'ringChk',
                    'target',
                    'Marker',
                    'Marker: a ring and the name of the test target in the field, at its place on the date of the season shown',
                    opts.marker,
                    (v) => set({ marker: v ? null : '0' }),
                  )}
                  {dchk(
                    'ringsChk',
                    'target',
                    'Speed rings',
                    'Speed rings: 1, 3, 10, 30, 100 and 300 ″/yr: how far a body of that speed moves between the first and the last season',
                    opts.rings,
                    (v) => set({ rings: v ? '1' : null }),
                  )}
                  {dchk(
                    'trailChk',
                    'path',
                    'Trajectory',
                    'Trajectory: the path of the test target over the seasons, a dot at each season’s date',
                    opts.trajectory,
                    (v) => set({ traj: v ? null : '0' }),
                  )}
                  {dchk(
                    'movChk',
                    'binoculars',
                    'Mover rings',
                    'Mover rings: a ring and a path for every track the search between passes found',
                    opts.movers,
                    (v) => set({ movers: v ? null : '0' }),
                  )}
                  {dchk(
                    'anoChk',
                    'sparkle',
                    'Anomalies',
                    'Anomaly rings: sources whose spectrum differs from an ordinary star of the field',
                    opts.anomalies,
                    an?.result ? (v) => set({ ano: v ? null : '0' }) : null,
                  )}
                  {dchk(
                    'maskChk',
                    'warning',
                    'Artifacts',
                    'Artifact mask: red where the pipeline flagged the pixel in many exposures',
                    false,
                    null,
                  )}
                  {dchk(
                    'morphChk',
                    'waveform',
                    'Interpolate',
                    'Interpolate: cross-fade between seasons instead of stepping',
                    opts.morph,
                    (v) => set({ morph: v ? '1' : null }),
                  )}
                  {dchk(
                    'refChk',
                    'scan',
                    'Reference',
                    'Reference: the DSS2 red picture of the same place (1985–1999), with the test target where it was then',
                    opts.reference,
                    (v) => set({ ref: v ? null : '0' }),
                  )}
                  {dchk(
                    'sharpChk',
                    'sparkle',
                    'Sharpen',
                    'Sharpen: faint stars crisper on screen; boost only, the same gain in every channel (colours never change), never near saturated cores',
                    opts.sharp,
                    (v) => set({ sharp: v ? null : '0' }),
                  )}
                </div>
                <div class="seg" role="group" aria-label="Blend">
                  <button
                    class={opts.mono ? '' : 'on'}
                    onClick={() => set({ blend: null })}
                    title="Each band keeps its colour; bands are screen-blended"
                  >
                    <span dangerouslySetInnerHTML={{ __html: icon('palette', 16) }} style={{ display: 'contents' }} />
                    Colour blend
                  </button>
                  <button
                    class={opts.mono ? 'on' : ''}
                    onClick={() => set({ blend: 'mono' })}
                    title="Sum the bands into one grayscale image"
                  >
                    <span
                      dangerouslySetInnerHTML={{
                        __html: icon('circle-half', 16),
                      }}
                      style={{ display: 'contents' }}
                    />
                    Mono sum
                  </button>
                </div>
                <div class="stretch">
                  <span>Stretch</span>
                  <input
                    type="range"
                    min={-3}
                    max={1.5}
                    step={0.05}
                    value={Math.log(stretch)}
                    aria-label="Stretch"
                    onInput={(e) => setStretch(Math.exp(Number((e.target as HTMLInputElement).value)))}
                    onDblClick={() => setStretch(0.4)}
                    title="Softness of the display law: left shows faint light, right keeps bright stars apart (double-click resets)"
                  />
                  <span class="mono-s">lin {stretch.toFixed(2)}</span>
                  <select
                    class="inp"
                    value={opts.trim}
                    onChange={(e) =>
                      set({
                        trim: (e.target as HTMLSelectElement).value === 'fixed' ? null : (e.target as HTMLSelectElement).value,
                      })
                    }
                    title="Trim of the bright end: a fixed white point, or the field's own 99.9th / 99.5th percentile"
                  >
                    <option value="fixed">Trim fixed</option>
                    <option value="p999">Trim 99.9 %</option>
                    <option value="p995">Trim 99.5 %</option>
                    <option value="adaptive">Trim adaptive</option>
                  </select>
                </div>
                <label
                  class="archsw"
                  title="Archive images: the same field in DSS2 red (1985–1999), 2MASS Ks (1997–2001) and AllWISE W1 (2010), blinked before the SPHEREx seasons"
                >
                  <input
                    type="checkbox"
                    checked={opts.archive}
                    disabled={film !== null}
                    onChange={(e) => set({ arch: (e.target as HTMLInputElement).checked ? '1' : null })}
                  />
                  Archive images
                  <span class="mono-s">{ARCHIVE.length} · 1985–2010</span>
                </label>
                <div class="dacts">
                  <select
                    class="inp"
                    value={String(speed)}
                    onChange={(e) => setSpeed(Number((e.target as HTMLSelectElement).value))}
                    title="Playback speed"
                  >
                    {[0.25, 0.5, 1, 2, 4].map((v) => (
                      <option value={String(v)}>Speed {v}×</option>
                    ))}
                  </select>
                  <button
                    class="lnk"
                    onClick={() => void navigator.clipboard?.writeText(location.href)}
                    title="Copy a link to this exact view"
                  >
                    <span dangerouslySetInnerHTML={{ __html: icon('link', 16) }} style={{ display: 'contents' }} />
                    Copy link
                  </button>
                  <button
                    class="lnk"
                    disabled={recording || film !== null || !passes.length}
                    onClick={() => void recordVideo()}
                    title="Download one full cycle of the seasons as a video (WebM)"
                  >
                    <span
                      dangerouslySetInnerHTML={{
                        __html: icon('download-simple', 16),
                      }}
                      style={{ display: 'contents' }}
                    />
                    {recording ? 'Recording…' : 'Download video'}
                  </button>
                </div>
              </div>
              <div class="searches">
                <div class="sechead">
                  <span class="h2">Searches</span>
                </div>
                {an?.result && (
                  <>
                    <div class="srow" title="Spectral anomaly map: every source's spectrum against the ordinary star of this field">
                      <b>Anomalies</b>
                      <span class="st">
                        {an.result.reason ||
                          `${an.result.anomalies.length} of ${an.result.with_spectrum} spectra above ${an.result.threshold?.toFixed(2)} (median ${an.result.median?.toFixed(2)})`}
                      </span>
                    </div>
                    {an.result.anomalies.map((a) => (
                      <button key={a.n} class="anrow" onClick={() => pickAnomaly(a.n)} title={driverWords(a).join('; ')}>
                        <b>Anomaly {a.n}</b>
                        <span class="t">
                          {a.identified?.simbad?.name ??
                            (a.identified?.gaia?.state === 'ok' ? `Gaia DR3 ${a.identified.gaia.source_id}` : 'not in SIMBAD or Gaia')}
                        </span>
                        <span class="faint">score {a.score.toFixed(1)}</span>
                      </button>
                    ))}
                  </>
                )}
                <MoversRows mv={mv} names={trackNames} onPick={pickTrack} />
                <div class="srow">
                  <b>Film</b>
                  <span class="st">inside a pass: the visits, asteroids hop</span>
                  <button class="btn sm" onClick={() => set({ film: film === null ? '0' : null })} data-testid="film">
                    {film === null ? 'Film' : 'Close film'}
                  </button>
                </div>
                {film !== null && filmBodies && (
                  <>
                    <div
                      class="srow"
                      title="Known solar-system bodies JPL predicts in this field during the pass (Small-Body Identification + Horizons); seen = the film's motion map shows something at the predicted place on that visit"
                    >
                      <b>Known bodies</b>
                      <span class="st">
                        {filmBodies.state === 'running'
                          ? 'asking JPL which bodies cross the field…'
                          : filmBodies.state === 'unavailable'
                            ? 'JPL not reachable; asked again next time'
                            : `${filmBodies.bodies.length} predicted (V ≤ ${filmBodies.vLimit}) · ${filmBodies.bodies.filter((b) => b.seen.length).length} seen on at least one of their visits`}
                      </span>
                    </div>
                    {[...filmBodies.bodies]
                      .sort((a, b) => b.seen.length - a.seen.length || (a.v ?? 99) - (b.v ?? 99))
                      .slice(0, 8)
                      .map((b) => (
                        <div key={b.name} class="mvrow" style={{ cursor: 'default' }}>
                          <b>{b.name}</b>
                          <span>V {b.v?.toFixed(1) ?? '—'}</span>
                          <span class="faint">
                            seen {b.seen.length} of {b.expected.length} visits
                          </span>
                        </div>
                      ))}
                  </>
                )}
              </div>
            </div>
          )}
          {tab === 'obj' && (
            <div class="pbody">
              {selAn !== null && an?.result && an.result.anomalies.find((a) => a.n === selAn) ? (
                <Passport a={an.result.anomalies.find((a) => a.n === selAn)!} f={an.result} />
              ) : (
                <ObjectTab src={sel} onSpectrum={() => setTab('spec')} />
              )}
            </div>
          )}
          {tab === 'spec' && (
            <div class="pbody">
              <SpectrumTab src={sel} />
            </div>
          )}
          <div class="panel-foot">
            {film === null && view === 'picture' && (
              <>
                <button
                  class="btn primary wide"
                  disabled={!an || an.state === 'waiting' || an.state === 'running' || an.state === 'done'}
                  onClick={() => void startAnomalies()}
                  title={
                    an?.state === 'waiting'
                      ? 'Starts once the field is finished'
                      : 'Find spectral anomalies here: sources whose spectrum differs from an ordinary star of the field. Every exposure of the field is measured; a 20′ field takes a few minutes'
                  }
                  data-testid="anomalies"
                >
                  <span dangerouslySetInnerHTML={{ __html: icon('sparkle', 16) }} style={{ display: 'contents' }} />
                  {an?.state === 'running'
                    ? `Finding anomalies · ${an.done} of ${an.total || '…'} exposures`
                    : 'Find spectral anomalies here'}
                </button>
                {an?.state === 'running' && (
                  <div class="prog thin">
                    <i style={{ width: `${an.total ? (100 * an.done) / an.total : 3}%` }} />
                  </div>
                )}
                {an?.state === 'failed' && <div class="mono-s">Anomaly search failed: {an.error}</div>}
              </>
            )}
            <div class="prow">
              <button
                class="play"
                onClick={() => {
                  setFrame(null);
                  setPlaying(!playing);
                }}
                title="Play / pause (Space)"
                dangerouslySetInnerHTML={{
                  __html: icon(playing && frame === null ? 'pause' : 'play', 18),
                }}
              />
              <div class="tl-mid">
                <div class="tl-labels">
                  {passes.map((q, i) => (
                    <span
                      key={q.index}
                      class={frameIndex === q.index ? 'on' : ''}
                      style={{
                        left: passes.length > 1 ? `${(i / (passes.length - 1)) * 100}%` : '0%',
                        transform: `translateX(${i === 0 ? 0 : i === passes.length - 1 ? -100 : -50}%)`,
                      }}
                      onClick={() => {
                        setPlaying(false);
                        setFrame(q.index);
                      }}
                    >
                      {season(q.mjd0)}
                    </span>
                  ))}
                </div>
                <input
                  type="range"
                  min={0}
                  max={Math.max(0, passes.length - 1)}
                  step={1}
                  value={frameIndex}
                  aria-label="Season"
                  onInput={(e) => {
                    setPlaying(false);
                    setFrame(Number((e.target as HTMLInputElement).value));
                  }}
                />
              </div>
            </div>
            <div class="status" data-testid="lab-status" title={film !== null ? filmStatus : status}>
              {film !== null ? filmStatus : status}
            </div>
            <div class="readout" />
          </div>
        </aside>
      </div>
    </div>
  );
}
