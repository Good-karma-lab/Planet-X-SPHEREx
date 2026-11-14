import { useEffect, useLayoutEffect, useRef, useState } from 'preact/hooks';
import { aim } from '../lib/aim';
import { FieldClient, mjdToDate, type FieldMeta } from '../lib/field';
import { formatHash, parseHash, type Route } from '../lib/route';
import { formatDec, formatRA, parseDec, parseRA } from '../lib/sky';
import type { SxpPart } from '../lib/sxp';
import { icon } from '../ui/icons';
import { passLevels, starBalance } from '../viewer/balance';
import { ARCHIVE, ArchiveSet } from '../viewer/archive';
import { FADE_MS, HOLD_MS, order } from '../viewer/pendulum';
import { PER_PASS, Renderer, wallLayout, type Panel, type StackName } from '../viewer/renderer';
import { atEpoch } from '../lib/sky';
import { FRAME_MS, loadBodies, loadFilm, utc, type Film, type KnownBody } from './film';
import { NeossatCard } from './neossat';
import { LightCurves } from './lightcurve';
import { ObjectTab, Passport, SpectrumTab, driverWords, type AnomalyField, type Source } from './spectrum';
import { halfToFloat } from '../lib/sxp';
import { Portrait, type PortraitData } from './portrait';
import { ContextRows, FilmTracks, drawTracks, useFilmTracks, type FilmTrack } from './search';

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
  artifacts: boolean;
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
/** The designation JPL and CADC understand: the number of a numbered body, else its provisional designation. */
function sbdbName(name: string): string {
  const m = name.match(/^\s*\(?(\d+)\)?\s/);
  if (m) return m[1]!;
  const d = name.match(/^([CPI]\/\d{4} [A-Z]+\d*)/);
  return d ? d[1]! : name.replace(/\s*\(.*\)\s*$/, '');
}

function ssoisUrl(name: string, mjd: number | null): string {
  const day = (m: number) => new Date((m - 40587) * 86400000).toISOString().slice(0, 10).replace(/-/g, '+');
  const m0 = (mjd ?? 61000) - 15;
  const m1 = (mjd ?? 61000) + 15;
  return `https://www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/ssos/ssos.pl?object=${encodeURIComponent(sbdbName(name))}&search=bynameall&epoch1=${day(m0)}&epoch2=${day(m1)}&eunits=bothdays&extres=no&xyres=no&format=tsv`;
}

interface RedRow {
  ra: number;
  dec: number;
  colour: number;
  lower_limit: boolean;
  snr_long: number;
  moves: boolean;
  pmra?: number;
  pmdec?: number;
  ra_j2000?: number;
  dec_j2000?: number;
}
interface RedView {
  state: 'waiting' | 'done' | 'failed';
  error?: string;
  result: { sources: number; median_colour: number | null; red: RedRow[] } | null;
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
const CHECK_SHORT: Record<string, string> = { same: 'Gaia ✓', none: 'no Gaia motion', pending: 'Gaia…', still: 'centroid shift' };

/** The uncertainty of a track's speed (″/yr) from the scatter of its places about the fitted line: rms / √Σ(t − t̄)². */
function speedErr(t: Track): number {
  const yrs = t.mjd.map((m) => m / 365.25);
  const mean = yrs.reduce((a, b) => a + b, 0) / Math.max(1, yrs.length);
  const ss = yrs.reduce((a, y) => a + (y - mean) ** 2, 0);
  return ss > 0 ? t.rms_arcsec / Math.sqrt(ss) : 0;
}

/** A track's speed as shown everywhere: the measured rate with its uncertainty. */
function rateText(t: Track): string {
  const e = speedErr(t);
  return `${t.speed.toFixed(1)}${e >= 0.05 ? ` ± ${e.toFixed(1)}` : ''}″/yr`;
}

/** Ecliptic latitude (degrees) of a place: asteroids crowd within about 10° of the ecliptic. */
function eclipticLat(ra: number, dec: number): number {
  const e = (23.4393 * Math.PI) / 180;
  const a = (ra * Math.PI) / 180;
  const d = (dec * Math.PI) / 180;
  return (Math.asin(Math.sin(d) * Math.cos(e) - Math.cos(d) * Math.sin(e) * Math.sin(a)) * 180) / Math.PI;
}

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
            {rateText(t)} toward {t.pa.toFixed(0)}° · {t.passes.length} passes{t.saturated ? ' · saturated core' : ''}
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
    reference: p['ref'] === '1',
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
    artifacts: p['art'] === '1',
  };

  const set = (params: Record<string, string | null>) => {
    // from the address as it is now (two quick clicks before a render must both count)
    const next: Record<string, string> = { ...parseHash(location.hash).params };
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
  const detsData = useRef(new Map<number, { n: number; dets: Uint16Array }>());
  const satData = useRef(new Map<number, Uint8Array>());
  const [portraitTick, setPortraitTick] = useState(0);
  const selRef = useRef<Source | null>(null);
  selRef.current = sel;
  const [mv, setMv] = useState<MoversView | null>(null);
  const mvRef = useRef<MoversView | null>(null);
  mvRef.current = mv;
  const [an, setAn] = useState<AnomalyView | null>(null);
  const anRef = useRef<AnomalyView | null>(null);
  anRef.current = an;
  const [selAn, setSelAn] = useState<number | null>(null);
  const dragFrom = useRef<{ x: number; y: number } | null>(null);
  const dragged = useRef(false);
  const [dragBox, setDragBox] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const [redv, setRedv] = useState<RedView | null>(null);
  const redRef = useRef<RedView | null>(null);
  redRef.current = redv;
  const filmMjd = film ? film : null;
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
  const speed = [0.25, 0.5, 1, 2, 4].includes(Number(p['spd'])) ? Number(p['spd']) : 1;
  const setSpeed = (v: number) => set({ spd: v === 1 ? null : String(v) });
  const [filmNote, setFilmNote] = useState('');
  const [filmData, setFilmData] = useState<Film | null>(null);
  const filmRef = useRef<Film | null>(null);
  filmRef.current = filmData;
  const bodiesRef = useRef<{ state: string; bodies: KnownBody[]; vLimit: number } | null>(null);
  bodiesRef.current = filmBodies;
  const rendRef = useRef<Renderer | null>(null);
  const layoutRef = useRef<{
    rects: { x: number; y: number; w: number; h: number }[];
    inset: { x: number; y: number; w: number; h: number; zoom: { cx: number; cy: number; s: number } } | null;
  }>({ rects: [], inset: null });
  const [visit, setVisit] = useState(0);
  const [insetBox, setInsetBox] = useState<{ x: number; y: number; w: number; h: number } | null>(null);
  const artMap = useRef(new Map<number, HTMLCanvasElement>());
  const artRef = useRef<HTMLCanvasElement | null>(null);
  const [stage, setStage] = useState({ w: 400, h: 400 });
  const stageRef = useRef<HTMLDivElement>(null);
  const [copied, setCopied] = useState('');
  const ft = useFilmTracks(filmData?.key ?? null);
  const ftRef = useRef<FilmTrack[] | null>(null);
  ftRef.current = ft.state === 'ok' ? ft.tracks : null;
  const [trackSel, setTrackSel] = useState(-1);
  const trackSelRef = useRef(-1);
  trackSelRef.current = trackSel;
  const [raIn, setRaIn] = useState('');
  const [decIn, setDecIn] = useState('');
  const stretch = Number(p['lin']) > 0 && Number(p['lin']) < 5 ? Number(p['lin']) : 0.4;
  const setStretch = (v: number) => set({ lin: Math.abs(v - 0.4) < 0.005 ? null : v.toFixed(2) });
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
    const el = stageRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      const w = Math.max(160, Math.floor(el.clientWidth));
      const h = Math.max(160, Math.floor(el.clientHeight));
      setStage({ w, h });
      setSide(Math.min(w, h));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [valid]);

  useEffect(() => {
    if (!valid || !canvas.current) return;
    const r = new Renderer(canvas.current);
    setMeta(null);
    setShown(null);
    state.current.meta = null;
    pictures.current.clear();
    detsData.current.clear();
    satData.current.clear();
    artMap.current.clear();
    artRef.current = null;
    setSel(null);
    const pending: { name: string; part: SxpPart }[] = [];
    let diffPart: SxpPart | null = null;
    const balanced = new Set<number>();
    const noised = new Set<number>();
    let trims: { p995: number; p999: number } | null = null;
    let shownPass = 0;
    let shownVisit = 0;
    rendRef.current = r;
    // what the 2′ zoom looks at: the selected source, else the test target in the field (places at 2025.9)
    const zoomTarget = (): [number, number] | null => {
      const sl = selRef.current;
      if (sl) return sl.pmra || sl.pmdec ? atEpoch(sl.ra, sl.dec, sl.pmra ?? 0, sl.pmdec ?? 0, 2025.9) : [sl.ra, sl.dec];
      const m = state.current.meta;
      if (!m) return null;
      const half = (m.grid.n * m.grid.step_arcsec) / 2 / 3600;
      for (const o of targets.current) {
        const [tra, tdec] = atEpoch(o.ra, o.dec, o.pmra ?? 0, o.pmdec ?? 0, 2025.9);
        if (
          Math.abs(tdec - m.grid.dec0) < half &&
          Math.abs(((tra - m.grid.ra0 + 540) % 360) - 180) * Math.cos((tdec * Math.PI) / 180) < half
        )
          return [tra, tdec];
      }
      return null;
    };
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
      if (part.arrays['sat']) {
        r.uploadSat(k, part.arrays['sat'].data as Uint8Array);
        satData.current.set(k, part.arrays['sat'].data as Uint8Array);
      }
      if (stack === 'dets') {
        detsData.current.set(k, { n, dets: arr.data as Uint16Array });
        setPortraitTick((t) => t + 1);
      }
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
        if (name.startsWith('flags_')) {
          // the artifact layer: red where the pipeline flagged the pixel in many exposures of the pass (any flag of the census)
          const fl = part.arrays['flags'];
          if (!fl) return;
          const [layers, n] = [fl.shape[0]!, fl.shape[fl.shape.length - 1]!];
          // artifacts only: not SOURCE (a real source) nor OVERFLOW (saturation, drawn white)
          const names = (part.meta['names'] as string[] | undefined) ?? [];
          const use = Array.from({ length: layers }, (_, k) => k).filter((k) => !['SOURCE', 'OVERFLOW'].includes(names[k] ?? ''));
          const c = document.createElement('canvas');
          c.width = c.height = n;
          const g = c.getContext('2d')!;
          const img = g.createImageData(n, n);
          const d = fl.data as Uint8Array;
          for (let i = 0; i < n * n; i++) {
            let v = 0;
            for (const k of use) v = Math.max(v, d[k * n * n + i]!);
            const a = Math.max(0, Math.min(1, (v / 255 - 0.25) / 0.5));
            img.data[4 * i] = 255;
            img.data[4 * i + 1] = 40;
            img.data[4 * i + 2] = 40;
            img.data[4 * i + 3] = Math.round(200 * a);
          }
          g.putImageData(img, 0, 0);
          artMap.current.set(Number(part.meta['pass']), c);
          if (!artRef.current) artRef.current = c;
          return;
        }
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
      const out: typeof labels = [];
      const rects: { x: number; y: number; w: number; h: number }[] = [];
      let inset: { x: number; y: number; w: number; h: number; zoom: { cx: number; cy: number; s: number } } | null = null;
      const F = filmRef.current;
      const days = st.film !== null;
      // the 2′ zoom: on the selected source, else the test target in the field (its place at 2025.9)
      const zoomOn = (): { cx: number; cy: number; s: number } | null => {
        const m = st.meta;
        if (!m || st.view !== 'picture' || st.opts.reference) return null;
        const fovArc = m.grid.n * m.grid.step_arcsec;
        if (fovArc <= 150) return null;
        const o = zoomTarget();
        if (!o) return null;
        const cosd = Math.cos((m.grid.dec0 * Math.PI) / 180);
        const cx = 0.5 - ((((o[0] - m.grid.ra0 + 540) % 360) - 180) * cosd * 3600) / fovArc;
        const cy = 0.5 - ((o[1] - m.grid.dec0) * 3600) / fovArc;
        if (cx < 0 || cy < 0 || cx > 1 || cy > 1) return null;
        return { cx, cy, s: 120 / fovArc };
      };
      if (days) {
        if (F && r.filmVisits) {
          const nv = F.meta.frames.length;
          let k = 0;
          let k2 = 0;
          let t = 0;
          if (st.frame !== null) k = k2 = Math.min(st.frame, nv - 1);
          else {
            const pos = clock / FRAME_MS;
            k = Math.floor(pos) % nv;
            k2 = (k + 1) % nv;
            const x = pos - Math.floor(pos);
            if (st.opts.morph && x > 0.6) {
              const y = (x - 0.6) / 0.4;
              t = y * y * (3 - 2 * y);
            }
          }
          const det = F.meta.frames[t < 0.5 ? k : k2]!.det;
          const tint = hslRgb(DETECTORS[det - 1]?.hue ?? 0);
          const base: Panel = { x: 0, y: 0, w, h, stack: 'film', mode: 'layer', layer: 0, tint, noData: [0, 0, 0], time: 'pendulum' };
          r.panel(base, k, k2, t);
          rects.push({ x: 0, y: 0, w, h });
          const z = zoomOn();
          if (z) {
            const S = Math.round(Math.min(220, w * 0.26));
            inset = { x: 8, y: 8, w: S, h: S, zoom: z };
            r.panel({ ...base, ...inset }, k, k2, t);
          }
          shownVisit = t < 0.5 ? k : k2;
          setVisit((v) => (v === shownVisit ? v : shownVisit));
        }
      } else {
        const allOn = st.bands.length === BANDS.length;
        let stack: StackName;
        if (st.view === 'picture')
          stack = allOn ? (r.loaded('picture').length >= 2 || r.loaded('quick').length === 0 ? 'picture' : 'quick') : 'bands';
        else stack = st.view === 'bands' ? 'bands' : 'dets';
        const loaded = r.loaded(stack);
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
            const z = zoomOn();
            if (z) {
              const S = Math.round(Math.min(220, w * 0.26));
              inset = { x: 8, y: 8, w: S, h: S, zoom: z };
              panels.push({ ...panels[0]!, ...inset });
            }
          } else if (st.view === 'bands') {
            const shownBands = BANDS.map((bd, i) => ({ bd, i })).filter((x) => st.bands.includes(x.bd.key));
            panels = wallLayout(shownBands.length, w, h).map((c, j) => {
              const { bd, i } = shownBands[j]!;
              return {
                x: c.x,
                y: c.y,
                w: c.size,
                h: c.size,
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
                y: c.y,
                w: c.size,
                h: c.size,
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
            if (!q.zoom) rects.push({ x: q.x, y: q.y, w: q.w, h: q.h });
            if (q.label) out.push({ x: q.x, y: q.y + q.h - 20, w: q.w, text: q.label, sub: q.sub ?? '', hue: q.hue ?? 0 });
          }
          setShown((s) => (s && s.pass === a && s.stack === stack ? s : { pass: a, stack }));
        }
      }
      layoutRef.current = { rects, inset };
      drawRings();
      const key = JSON.stringify(out) + (inset ? `${inset.w}` : '');
      if (key !== lastLabels) {
        lastLabels = key;
        setLabels(out);
        setInsetBox(inset ? { x: inset.x, y: inset.y, w: inset.w, h: inset.h } : null);
      }
      raf = requestAnimationFrame(tick);
    };

    // ---- marks on the picture: one marker per object ----------------------------------------------------------------------
    type Mark = {
      ra: number;
      dec: number;
      kind: 'target' | 'track' | 'anomaly' | 'red' | 'body';
      name: string;
      lines: string[];
      colour: string;
      fill: string;
      rr: number;
      path?: [number, number][];
      motion?: [number, number];
      dashed?: boolean;
      selected?: boolean;
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
      const m = st.meta;
      const F = filmRef.current;
      const days = st.film !== null;
      const grid = days && F ? F.meta.grid : m?.grid;
      if (!grid) return;
      const { rects, inset } = layoutRef.current;
      // artifacts: where the pipeline flagged the pixel in many exposures of the season shown
      const art = artMap.current.get(shownPass) ?? artRef.current;
      if (st.opts.artifacts && art && (days || st.view === 'picture')) {
        ctx.globalAlpha = 0.85;
        ctx.imageSmoothingEnabled = true;
        for (const box of rects) ctx.drawImage(art, box.x, box.y, box.w, box.h);
        ctx.globalAlpha = 1;
      }
      const fovArc = grid.n * grid.step_arcsec;
      const cosd = Math.cos((grid.dec0 * Math.PI) / 180);
      const uvOf = (ra: number, dec: number): [number, number] => [
        0.5 - ((((ra - grid.ra0 + 540) % 360) - 180) * cosd * 3600) / fovArc,
        0.5 - ((dec - grid.dec0) * 3600) / fovArc,
      ];
      // the date of what is shown: the season's middle, or the visit's time in the Days scale
      const shownMjd = days && F ? (F.meta.frames[shownVisit]?.mjd ?? F.meta.pass.mjd0) : (m?.passes[shownPass] ?? m?.passes[0])?.mjd_mid;
      if (shownMjd === undefined) return;
      const year = 2000 + (shownMjd - 51544.5) / 365.25;
      const marks: Mark[] = [];
      if (st.opts.marker && m) {
        for (const o of targets.current) {
          const [tra, tdec] = atEpoch(o.ra, o.dec, o.pmra ?? 0, o.pmdec ?? 0, year);
          const [u, v] = uvOf(tra, tdec);
          if (u < -0.05 || v < -0.05 || u > 1.05 || v > 1.05) continue;
          marks.push({
            ra: tra,
            dec: tdec,
            kind: 'target',
            name: o.n,
            lines: [],
            colour: '#7ff0e4',
            fill: '#d8fffa',
            rr: 18,
            path: m.passes.map((pp) => atEpoch(o.ra, o.dec, o.pmra ?? 0, o.pmdec ?? 0, 2000 + (pp.mjd_mid - 51544.5) / 365.25)),
            motion: (o.pmra ?? 0) || (o.pmdec ?? 0) ? [o.pmra ?? 0, o.pmdec ?? 0] : undefined,
          });
        }
      }
      const mvv = mvRef.current;
      if (st.opts.movers && mvv?.result && !days) {
        for (const t of mvv.result.tracks) {
          const yrs = year - 2025.9;
          const decN = t.dec_2025_9 + (t.v_north * yrs) / 3600;
          const raN = t.ra_2025_9 + (t.v_east * yrs) / 3600 / Math.cos((decN * Math.PI) / 180);
          const line = `${rateText(t)} · ${CHECK_SHORT[t.check] ?? t.check}${t.saturated ? ' · saturated core' : ''}`;
          const same = marks.find(
            (k) => k.kind === 'target' && Math.hypot((k.ra - raN) * Math.cos((decN * Math.PI) / 180), k.dec - decN) * 3600 < 12,
          );
          if (same) {
            same.lines.push(line);
            same.path = t.ra.map((x, i) => [x, t.dec[i]!]);
            same.motion = [t.v_east * 1000, t.v_north * 1000];
          } else
            marks.push({
              ra: raN,
              dec: decN,
              kind: 'track',
              name: 'Mover',
              lines: [line],
              colour: '#ffb454',
              fill: '#ffd08a',
              rr: 18,
              path: t.ra.map((x, i) => [x, t.dec[i]!]),
              motion: [t.v_east * 1000, t.v_north * 1000],
              dashed: t.check === 'pending',
            });
        }
      }
      const near = (ra: number, dec: number, arcsec: number) =>
        marks.find((k) => Math.hypot((k.ra - ra) * Math.cos((dec * Math.PI) / 180), k.dec - dec) * 3600 < arcsec);
      const anv = anRef.current;
      if (st.opts.anomalies && anv?.result) {
        for (const a of anv.result.anomalies) {
          const [ara, adec] = atEpoch(a.ra, a.dec, a.pmra, a.pmdec, year);
          const k = near(ara, adec, 10);
          if (k) k.lines.push(`spectral anomaly ${a.n}`);
          else
            marks.push({
              ra: ara,
              dec: adec,
              kind: 'anomaly',
              name: `Anomaly ${a.n}`,
              lines: [],
              colour: '#ff7ac8',
              fill: '#ffb0dc',
              rr: 15,
            });
        }
      }
      const rv = redRef.current;
      if (rv?.result) {
        rv.result.red.forEach((x, i) => {
          const [rra, rdec] =
            x.moves && x.ra_j2000 != null && x.dec_j2000 != null
              ? atEpoch(x.ra_j2000, x.dec_j2000, x.pmra ?? 0, x.pmdec ?? 0, year)
              : [x.ra, x.dec];
          const k = near(rra, rdec, 10);
          if (k) k.lines.push(`red source ${i + 1}`);
          else
            marks.push({
              ra: rra,
              dec: rdec,
              kind: 'red',
              name: `Red source ${i + 1}`,
              lines: [],
              colour: '#ff6b6b',
              fill: '#ffb3b3',
              rr: 15,
            });
        });
      }
      // known bodies of the Days film: a ring where JPL predicts them, solid when the visit's motion map shows them there
      const fb = bodiesRef.current;
      if (days && F && fb && st.opts.marker) {
        const n = F.meta.grid.n;
        for (const b of fb.bodies) {
          const fr = b.frames[shownVisit];
          if (!fr || !fr.inside) continue;
          const seen = b.seen.includes(shownVisit);
          const ra = grid.ra0 - ((fr.x + 0.5 - n / 2) * grid.step_arcsec) / 3600 / cosd;
          const dec = grid.dec0 - ((fr.y + 0.5 - n / 2) * grid.step_arcsec) / 3600;
          const label = (b.v ?? 99) <= 19.5 || seen ? b.name.replace(/\s*\(.*\)\s*$/, '') : '';
          marks.push({
            ra,
            dec,
            kind: 'body',
            name: label,
            lines: label ? [`V ${b.v?.toFixed(1) ?? '—'}${seen ? ' · seen on this visit' : ''}`] : [],
            colour: seen ? '#ffb454' : 'rgba(220,235,240,0.85)',
            fill: seen ? '#ffd08a' : '#d6e7ed',
            rr: 12,
            dashed: !seen,
          });
        }
      }
      const fts = ftRef.current;
      if (days && F && fts && st.opts.movers) {
        drawTracks(ctx, fts, { scale: wcss / F.meta.grid.n, k: shownVisit, rings: true, trail: false, selected: trackSelRef.current }, 1);
      }
      const sl = selRef.current;
      let selMark: Mark | null = null;
      if (sl) {
        const [sra, sdec] = sl.pmra || sl.pmdec ? atEpoch(sl.ra, sl.dec, sl.pmra ?? 0, sl.pmdec ?? 0, year) : [sl.ra, sl.dec];
        const k = near(sra, sdec, 8);
        if (k) k.selected = true;
        else
          selMark = { ra: sra, dec: sdec, kind: 'track', name: '', lines: [], colour: '#ffb454', fill: '#ffd08a', rr: 13, selected: true };
      }
      const all = selMark ? [...marks, selMark] : marks;
      const placed: { x0: number; y0: number; x1: number; y1: number }[] = [];
      const ring = (x: number, y: number, mk: Mark, rr: number, ticks: boolean) => {
        const shape = () => {
          ctx.beginPath();
          ctx.arc(x, y, rr, 0, 2 * Math.PI);
          if (ticks)
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
        ctx.setLineDash(mk.dashed ? [4, 3] : []);
        ctx.lineWidth = mk.selected ? 4.5 : 3.5;
        ctx.strokeStyle = 'rgba(0,0,0,0.7)';
        shape();
        ctx.lineWidth = mk.selected ? 2.4 : 1.6;
        ctx.strokeStyle = mk.colour;
        shape();
        ctx.setLineDash([]);
      };
      const label = (x: number, y: number, rr: number, mk: Mark, box: { x: number; y: number; w: number; h: number }) => {
        const lines = [mk.name, ...mk.lines].filter(Boolean);
        if (!lines.length) return;
        ctx.font = '600 11px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
        const tw = Math.max(...lines.map((l, i) => ctx.measureText(l).width * (i ? 0.92 : 1)));
        const th = 13 * lines.length;
        // right of the ring, else left; moved down past labels already placed
        let lx = x + rr + 8;
        if (lx + tw > box.x + box.w - 2) lx = x - rr - 8 - tw;
        let ly = y - rr * 0.35 - 9;
        for (let tries = 0; tries < 8; tries++) {
          const hit = placed.find((p) => lx < p.x1 && lx + tw > p.x0 && ly < p.y1 && ly + th > p.y0);
          if (!hit) break;
          ly = hit.y1 + 2;
        }
        ly = Math.max(box.y + 2, Math.min(box.y + box.h - th - 2, ly));
        placed.push({ x0: lx, y0: ly, x1: lx + tw, y1: ly + th });
        lines.forEach((l, i) => {
          ctx.font = i ? '500 10.5px ui-monospace, Menlo, monospace' : '600 11px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
          ctx.lineWidth = 3;
          ctx.strokeStyle = '#000';
          ctx.fillStyle = mk.fill;
          ctx.strokeText(l, lx, ly + 10 + 13 * i);
          ctx.fillText(l, lx, ly + 10 + 13 * i);
        });
      };
      const pathOf = (mk: Mark, box: { x: number; y: number; w: number; h: number }, rr: number, x: number, y: number) => {
        if (!st.opts.trajectory || (!mk.path && !mk.motion)) return;
        const pts = (mk.path ?? []).map(([pra, pdec]) => {
          const [u, v] = uvOf(pra, pdec);
          return [box.x + u * box.w, box.y + v * box.h] as const;
        });
        const span = pts.length > 1 ? Math.hypot(pts[pts.length - 1]![0] - pts[0]![0], pts[pts.length - 1]![1] - pts[0]![1]) : 0;
        if (span > 2.5 * rr) {
          // a path long enough to leave the ring: the places on the seasons' dates, joined
          ctx.setLineDash([3, 3]);
          ctx.lineWidth = 3;
          ctx.strokeStyle = 'rgba(0,0,0,0.55)';
          ctx.beginPath();
          pts.forEach(([px, py], i) => (i ? ctx.lineTo(px, py) : ctx.moveTo(px, py)));
          ctx.stroke();
          ctx.lineWidth = 1.4;
          ctx.strokeStyle = mk.colour;
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.fillStyle = mk.colour;
          for (const [px, py] of pts) {
            ctx.beginPath();
            ctx.arc(px, py, 2.4, 0, 2 * Math.PI);
            ctx.fill();
          }
        } else if (mk.motion) {
          // too short to see beside the star (and never drawn inside a saturated core): an arrow from the ring along the motion
          const ux = -mk.motion[0];
          const uy = -mk.motion[1];
          const un = Math.hypot(ux, uy) || 1;
          const dx = ux / un;
          const dy = uy / un;
          const x0 = x + dx * (rr + 4);
          const y0 = y + dy * (rr + 4);
          const x1 = x + dx * (rr + 24);
          const y1 = y + dy * (rr + 24);
          const arrow = () => {
            ctx.beginPath();
            ctx.moveTo(x0, y0);
            ctx.lineTo(x1, y1);
            ctx.lineTo(x1 - dx * 7 - dy * 4, y1 - dy * 7 + dx * 4);
            ctx.moveTo(x1, y1);
            ctx.lineTo(x1 - dx * 7 + dy * 4, y1 - dy * 7 - dx * 4);
            ctx.stroke();
          };
          ctx.lineWidth = 3;
          ctx.strokeStyle = 'rgba(0,0,0,0.6)';
          arrow();
          ctx.lineWidth = 1.6;
          ctx.strokeStyle = mk.colour;
          arrow();
        }
      };
      const wall = !days && st.view !== 'picture';
      const underInset = (x: number, y: number) =>
        !!inset && x > inset.x - 4 && y > inset.y - 4 && x < inset.x + inset.w + 4 && y < inset.y + inset.h + 4;
      ctx.save();
      for (const box of rects) {
        if (wall) {
          // a thin frame around every panel of a wall
          ctx.strokeStyle = 'rgba(160,200,215,0.28)';
          ctx.lineWidth = 1;
          ctx.strokeRect(box.x + 0.5, box.y + 0.5, box.w - 1, box.h - 1);
        }
        ctx.save();
        ctx.beginPath();
        ctx.rect(box.x, box.y, box.w, box.h);
        ctx.clip();
        for (const mk of all) {
          const [u, v] = uvOf(mk.ra, mk.dec);
          const x = box.x + u * box.w;
          const y = box.y + v * box.h;
          if (x < box.x - 30 || y < box.y - 30 || x > box.x + box.w + 30 || y > box.y + box.h + 30) continue;
          if (wall) {
            if (mk.kind !== 'target' && !mk.selected) continue;
            ring(x, y, mk, Math.max(8, Math.min(14, box.w / 22)), mk.kind === 'target');
            continue;
          }
          if (underInset(x, y)) continue;
          ring(x, y, mk, mk.rr, mk.kind === 'target');
          pathOf(mk, box, mk.rr, x, y);
        }
        if (!wall)
          for (const mk of all) {
            const [u, v] = uvOf(mk.ra, mk.dec);
            const x = box.x + u * box.w;
            const y = box.y + v * box.h;
            if (x < box.x || y < box.y || x > box.x + box.w || y > box.y + box.h || underInset(x, y)) continue;
            label(x, y, mk.rr, mk, box);
          }
        ctx.restore();
      }
      if (inset) {
        // the 2′ zoom: the selected object ringed, scaled with the zoom
        const mk = all.find((k) => k.selected) ?? all.find((k) => k.kind === 'target');
        if (mk) {
          const [u, v] = uvOf(mk.ra, mk.dec);
          const x = inset.x + ((u - inset.zoom.cx) / inset.zoom.s + 0.5) * inset.w;
          const y = inset.y + ((v - inset.zoom.cy) / inset.zoom.s + 0.5) * inset.h;
          ctx.save();
          ctx.beginPath();
          ctx.rect(inset.x, inset.y, inset.w, inset.h);
          ctx.clip();
          ring(x, y, { ...mk, selected: false }, Math.min(inset.w * 0.2, mk.rr / inset.zoom.s), false);
          ctx.restore();
        }
      }
      ctx.restore();
      if (!st.opts.rings || wall) return;
      // speed rings, centred on the field: how far a body of that speed moves between the first and the last season
      // (in the Days scale: between the first and the last visit, in ″ per hour)
      let dt: number;
      let speeds: number[];
      let unit: string;
      if (days && F) {
        dt = (F.meta.frames[F.meta.frames.length - 1]!.mjd - F.meta.frames[0]!.mjd) * 24;
        speeds = [0.3, 1, 3, 10, 30, 100];
        unit = '″/h';
      } else if (m && m.passes.length >= 2) {
        dt = (m.passes[m.passes.length - 1]!.mjd_mid - m.passes[0]!.mjd_mid) / 365.25;
        speeds = [1, 3, 10, 30, 100, 300];
        unit = '″/yr';
      } else return;
      ctx.strokeStyle = 'rgba(255,180,84,0.75)';
      ctx.fillStyle = 'rgba(255,200,130,0.95)';
      ctx.font = '600 10px ui-monospace, Menlo, monospace';
      ctx.lineWidth = 1;
      for (const v of speeds) {
        const rpx = ((v * dt) / fovArc) * wcss;
        if (rpx < 14 || rpx > wcss * 0.72) continue;
        ctx.beginPath();
        ctx.setLineDash([4, 4]);
        ctx.arc(wcss / 2, hcss / 2, rpx, 0, 2 * Math.PI);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillText(`${v}${unit}`, wcss / 2 + rpx * 0.7071 + 3, hcss / 2 - rpx * 0.7071 - 3);
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
    if (opts.artifacts) c.want('flags_');
    if (tab === 'obj') c.want('dets_');
  }, [view, time, fov, meta, bands.length, opts.artifacts, tab]);

  // the Days scale: the visits of the pass shown (the film), drawn by the lab's renderer; no reload of the field
  useEffect(() => {
    setFilmData(null);
    setFilmBodies(null);
    setFilmNote('');
    if (!valid || film === null) return;
    let stop = false;
    const stopped = () => stop;
    void (async () => {
      try {
        const res = await loadFilm(ra, dec, fov, film || undefined, (t) => !stop && setFilmNote(t), stopped);
        if (stop || !res) return;
        if (res.state === 'none') {
          setFilmNote(res.note);
          return;
        }
        const f = res.film;
        const r = rendRef.current;
        if (r) {
          r.setupFilm(f.meta.grid.n, f.meta.frames.length, 6.15 / f.meta.grid.step_arcsec);
          f.sky.forEach((a, k) => r.uploadFilm(k, a));
          r.setFilmNoise(f.background, f.sigma);
        }
        setFilmNote('');
        setFrame((x) => (x !== null && x >= f.meta.frames.length ? null : x));
        setFilmData(f);
        setFilmBodies({ state: 'running', bodies: [], vLimit: 20.5 });
        const b = await loadBodies(f, stopped);
        if (!stop && b) setFilmBodies(b);
      } catch (e) {
        if (!stop) setFilmNote(`the visits could not be loaded: ${String(e)}`);
      }
    })();
    return () => {
      stop = true;
    };
  }, [ra, dec, fov, valid, film]);

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
    setRedv(null);
    if (!valid || !meta) return;
    void followAnomalies(false);
    return () => {
      anPoll.current++;
    };
  }, [ra, dec, fov, valid, meta]);
  const startAnomalies = () => followAnomalies(true);
  const findRed = async () => {
    setRedv({ state: 'waiting', error: 'busy', result: null });
    try {
      const r = await fetch(`/api/v1/red?ra=${ra}&dec=${dec}&fov=${fov}`, { cache: 'no-store' });
      setRedv(r.ok ? ((await r.json()) as RedView) : { state: 'failed', error: `the sky service answered ${r.status}`, result: null });
    } catch (e) {
      setRedv({ state: 'failed', error: String(e), result: null });
    }
  };
  const pickRed = (k: number) => {
    const x = redv?.result?.red[k];
    if (!x) return;
    setSelAn(null);
    setSel(
      x.moves && x.ra_j2000 != null && x.dec_j2000 != null
        ? { ra: x.ra_j2000, dec: x.dec_j2000, pmra: x.pmra, pmdec: x.pmdec, name: `Red source ${k + 1}` }
        : { ra: x.ra, dec: x.dec, name: `Red source ${k + 1}` },
    );
    setTab('spec');
  };
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
      const n = state.current.film !== null ? (filmRef.current?.meta.frames.length ?? 0) : (meta?.passes.length ?? 0);
      if (e.code === 'Space') {
        e.preventDefault();
        setFrame(null);
        setPlaying((x) => !x);
      } else if (e.key === '[' || e.key === ']') {
        const steps = [0.25, 0.5, 1, 2, 4];
        const i = steps.indexOf(speed);
        setSpeed(steps[Math.max(0, Math.min(steps.length - 1, i + (e.key === ']' ? 1 : -1)))]!);
      } else if ((e.code === 'ArrowRight' || e.code === 'ArrowLeft') && n) {
        setPlaying(false);
        const cur = state.current.film !== null ? visit : (shown?.pass ?? 0);
        setFrame((f) => ((f ?? cur) + (e.code === 'ArrowRight' ? 1 : n - 1)) % n);
      }
    };
    window.addEventListener('keydown', on);
    return () => window.removeEventListener('keydown', on);
  }, [meta, shown, speed, p, visit]);

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
    if (insetBox && !opts.reference) {
      const lx = e.clientX - rect.left;
      const ly = e.clientY - rect.top;
      if (lx >= insetBox.x && ly >= insetBox.y && lx <= insetBox.x + insetBox.w && ly <= insetBox.y + insetBox.h) return; // the zoom itself
    }
    let gx = ((e.clientX - rect.left) / rect.width) * m.grid.n;
    let gy = ((e.clientY - rect.top) / rect.height) * m.grid.n;
    const toSky = (x: number, y: number): [number, number] => [
      (m.grid.ra0 - ((x - m.grid.n / 2) * m.grid.step_arcsec) / 3600 / Math.cos((m.grid.dec0 * Math.PI) / 180) + 360) % 360,
      m.grid.dec0 - ((y - m.grid.n / 2) * m.grid.step_arcsec) / 3600,
    ];
    const [cra, cdec] = toSky(gx, gy);
    const q = passes.find((x) => x.index === (shown?.pass ?? 0)) ?? passes[0];
    const year = q ? 2000 + (q.mjd_mid - 51544.5) / 365.25 : 2025.9;
    const tol = Math.max(12, (14 / rect.width) * m.grid.n * m.grid.step_arcsec);
    const sep = (ra1: number, de1: number) => Math.hypot((ra1 - cra) * Math.cos((cdec * Math.PI) / 180), de1 - cdec) * 3600;
    // a click on an anomaly opens its passport; on a red source, its spectrum
    for (const a of an?.result?.anomalies ?? []) {
      const [ara, adec] = atEpoch(a.ra, a.dec, a.pmra, a.pmdec, year);
      if (sep(ara, adec) < tol) return pickAnomaly(a.n);
    }
    const reds = redv?.result?.red ?? [];
    for (let k = 0; k < reds.length; k++) {
      const x = reds[k]!;
      const [rra, rdec] =
        x.moves && x.ra_j2000 != null && x.dec_j2000 != null
          ? atEpoch(x.ra_j2000, x.dec_j2000, x.pmra ?? 0, x.pmdec ?? 0, year)
          : [x.ra, x.dec];
      if (sep(rra, rdec) < tol) return pickRed(k);
    }
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
          ? `${time === 'sum' ? 'All seasons co-added' : `Season ${shown.pass + 1} of ${passes.length} · ${passMeta ? season(passMeta.mjd0) : ''}`} · ${colourWord}${time === 'diff' ? (shown.pass === 0 ? ' · Difference: reference season' : ' · Difference against season 1') : ''} · ${meta.exposures} exposures${time === 'loop' && seq ? ` · passes ${seq.slice(0, -2)}` : ''}${phase ? ` · ${phase}` : ''}${Math.abs(eclipticLat(ra, dec)) < 10 ? ' · near the ecliptic: asteroids likely here, switch to Days' : ''}`
          : phase;
  const frameIndex = frame ?? shown?.pass ?? 0;
  const wallView = view !== 'picture' && film === null;
  /** The portrait of a source at (ra, dec) [J2000 with motion] from the detector stacks of the season shown. */
  const portraitOf = (src: { ra: number; dec: number; pmra?: number; pmdec?: number }, label: string) => {
    void portraitTick;
    if (!meta) return null;
    const k = shown?.pass ?? 0;
    const d = detsData.current.get(k) ?? detsData.current.values().next().value;
    if (!d) return <div class="mono-s">Portrait: loading the detector pictures…</div>;
    const q = passes.find((x) => x.index === k) ?? passes[0];
    const year = q ? 2000 + (q.mjd_mid - 51544.5) / 365.25 : 2025.9;
    const [pra, pdec] = src.pmra || src.pmdec ? atEpoch(src.ra, src.dec, src.pmra ?? 0, src.pmdec ?? 0, year) : [src.ra, src.dec];
    const g = meta.grid;
    const gx = g.n / 2 - ((((pra - g.ra0 + 540) % 360) - 180) * Math.cos((g.dec0 * Math.PI) / 180) * 3600) / g.step_arcsec;
    const gy = g.n / 2 - ((pdec - g.dec0) * 3600) / g.step_arcsec;
    if (gx < 0 || gy < 0 || gx >= g.n || gy >= g.n) return null;
    const data: PortraitData = { n: d.n, dets: d.dets, sat: satData.current.get(k), stepArcsec: g.step_arcsec };
    return <Portrait data={data} x={gx} y={gy} label={label} />;
  };
  const half = fov / 120;
  const inField = targets.current.find((o) => {
    const [tra, tdec] = atEpoch(o.ra, o.dec, o.pmra ?? 0, o.pmdec ?? 0, 2025.9);
    return Math.abs(tdec - dec) < half && Math.abs(((tra - ra + 540) % 360) - 180) * Math.cos((dec * Math.PI) / 180) < half;
  });
  const fieldName = inField ? inField.n : 'Sky field';
  const beta = eclipticLat(ra, dec);
  const nVisits = filmData?.meta.frames.length ?? 0;
  const bodiesSeen = filmBodies?.bodies.filter((b) => b.seen.length).length ?? 0;
  const daysStatus =
    film === null
      ? ''
      : !filmData
        ? filmNote || 'asking for the visits of the pass…'
        : `Days · pass of ${season(filmData.meta.pass.mjd0)} · ${nVisits} visits · visit ${visit + 1} · ${utc(filmData.meta.frames[visit]?.mjd ?? 0)} · ` +
          (filmBodies?.state === 'running'
            ? 'asking JPL which known asteroids cross the field…'
            : filmBodies
              ? `${filmBodies.bodies.length} known asteroids predicted, ${bodiesSeen} seen in the visits`
              : '') +
          (Math.abs(beta) > 25 ? ' · far from the ecliptic: few asteroids expected here' : '');
  const passMjd = (passes.find((q) => q.index === (shown?.pass ?? 0)) ?? passes[0])?.mjd_mid;
  const toDays = () => {
    setFrame(null);
    set({ film: passMjd ? passMjd.toFixed(1) : '0', view: null });
  };
  const toMonths = () => {
    setFrame(null);
    set({ film: null });
  };

  const dchk = (
    id: string,
    ico: string,
    text: string,
    title: string,
    checked: boolean,
    onChange: ((v: boolean) => void) | null,
    why = 'not available in this view',
  ) => (
    <label class={`chk ${onChange ? '' : 'disabled'}`} title={onChange ? title : `${title} (${why})`}>
      <input
        type="checkbox"
        id={id}
        aria-label={text}
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
          <div class="vtop">
            <div class="vtitle" data-testid="field-title">
              <b>{fieldName}</b>
              <span>
                {formatRA(ra)} {formatDec(dec)}
              </span>
            </div>
            <label class="fovsel" title="Field of view">
              <span>Field</span>
              <select
                class="inp"
                value={String(fov)}
                onChange={(e) => set({ fov: (e.target as HTMLSelectElement).value })}
                data-testid="fov"
              >
                {SIZES.map((s) => (
                  <option key={s} value={String(s)}>
                    {s}′
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div class="vstage" ref={stageRef}>
            <div
              class="vsq"
              style={{
                width: `${wallView ? stage.w : side}px`,
                height: `${wallView ? stage.h : side}px`,
                cursor: view === 'picture' ? 'crosshair' : undefined,
              }}
              onClick={(e) => {
                if (dragged.current) {
                  dragged.current = false;
                  return;
                }
                pick(e);
              }}
              onPointerDown={(e) => {
                if (film !== null || view !== 'picture' || !meta) return;
                const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
                dragFrom.current = { x: e.clientX - r.left, y: e.clientY - r.top };
              }}
              onPointerMove={(e) => {
                const f = dragFrom.current;
                if (!f) return;
                const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
                const x = e.clientX - r.left;
                const y = e.clientY - r.top;
                if (Math.hypot(x - f.x, y - f.y) > 12) setDragBox({ x0: f.x, y0: f.y, x1: x, y1: y });
              }}
              onPointerUp={() => {
                const b = dragBox;
                dragFrom.current = null;
                setDragBox(null);
                if (!b || !meta) return;
                dragged.current = true;
                // open the dragged box as a new field: its centre, the smallest field size that holds it
                const n = meta.grid.n;
                const sc = n / side;
                const cx = ((b.x0 + b.x1) / 2) * sc;
                const cy = ((b.y0 + b.y1) / 2) * sc;
                const sizeArcmin = (Math.max(Math.abs(b.x1 - b.x0), Math.abs(b.y1 - b.y0)) * sc * meta.grid.step_arcsec) / 60;
                const nf = SIZES.find((z) => z >= sizeArcmin) ?? 60;
                const nra =
                  (meta.grid.ra0 - ((cx - n / 2) * meta.grid.step_arcsec) / 3600 / Math.cos((meta.grid.dec0 * Math.PI) / 180) + 360) % 360;
                const ndec = meta.grid.dec0 - ((cy - n / 2) * meta.grid.step_arcsec) / 3600;
                set({ ra: nra.toFixed(5), dec: ndec.toFixed(5), fov: String(nf) });
              }}
            >
              {dragBox && (
                <div
                  class="dragbox"
                  style={{
                    left: `${Math.min(dragBox.x0, dragBox.x1)}px`,
                    top: `${Math.min(dragBox.y0, dragBox.y1)}px`,
                    width: `${Math.abs(dragBox.x1 - dragBox.x0)}px`,
                    height: `${Math.abs(dragBox.y1 - dragBox.y0)}px`,
                  }}
                />
              )}
              <canvas
                ref={canvas}
                class={`vid ${wallView ? 'wall' : ''}`}
                role="img"
                aria-label="Video of the field built from real SPHEREx exposures"
                data-testid="picture"
              />
              <canvas ref={overlay} class="ov" />
              {labels.map((l) => (
                <div class="panel-label" style={{ left: `${l.x + 6}px`, top: `${l.y + 2}px`, maxWidth: `${l.w - 10}px` }}>
                  <span class="sw" style={{ background: `hsl(${l.hue},80%,60%)`, boxShadow: `0 0 8px hsl(${l.hue},80%,60%)` }} />
                  <b>{l.text}</b>
                  <em>{l.sub}</em>
                </div>
              ))}
              {film === null && view === 'picture' && archStep !== null && archStep < ARCHIVE.length && (
                <canvas ref={archBig} class="archive-big" />
              )}
              {insetBox && view === 'picture' && !opts.reference && (
                <div
                  class="zoombox"
                  style={{ left: `${insetBox.x}px`, top: `${insetBox.y}px`, width: `${insetBox.w}px`, height: `${insetBox.h}px` }}
                  title="2′ around the selected object (the test target until you click a source), from the same frames as the picture"
                >
                  <span>2′ zoom</span>
                </div>
              )}
              {view === 'picture' && opts.reference && !(archStep !== null && archStep < ARCHIVE.length) && (
                <div
                  class="refbox"
                  title="DSS2 red (POSS-II plates, 1985–1999) of the same field, the test target ringed where it was then"
                >
                  <canvas ref={refCanvas} width={150} height={150} />
                  <span>Reference · DSS2 red · 1985–1999</span>
                </div>
              )}
              {film === null && view === 'picture' && archStep !== null && archStep < ARCHIVE.length ? (
                <div class="vlabel">
                  {ARCHIVE[archStep]!.name} · {ARCHIVE[archStep]!.years}
                </div>
              ) : film !== null ? (
                filmData && (
                  <div class="vlabel">
                    SPHEREx · visit {visit + 1} of {filmData.meta.frames.length} · {utc(filmData.meta.frames[visit]?.mjd ?? 0)} · D
                    {filmData.meta.frames[visit]?.det}
                  </div>
                )
              ) : (
                view === 'picture' &&
                passMeta && <div class="vlabel">{time === 'sum' ? 'all seasons' : `SPHEREx · ${season(passMeta.mjd0)}`}</div>
              )}
              {film !== null && !filmData && <div class="vwait">{filmNote || 'asking for the visits of the pass…'}</div>}
            </div>
          </div>
          <div class="vbottom" role="group" aria-label={view === 'det' ? 'Detectors' : 'Spectral bands'}>
            {view === 'det'
              ? DETECTORS.map((d, i) => (
                  <button
                    key={i}
                    class={`bchip ${dets.includes(i + 1) ? 'on' : ''}`}
                    style={{ '--c': `hsl(${d.hue},80%,60%)` }}
                    aria-pressed={dets.includes(i + 1)}
                    onClick={() => toggleDet(i + 1)}
                    title={`Detector D${i + 1}: ${d.name}, ${d.range}`}
                  >
                    <b>D{i + 1}</b>
                    <em>{d.range}</em>
                  </button>
                ))
              : BANDS.map((bd) => (
                  <button
                    key={bd.key}
                    class={`bchip ${bands.includes(bd.key) ? 'on' : ''}`}
                    style={{ '--c': `hsl(${bd.hue},80%,60%)` }}
                    aria-pressed={bands.includes(bd.key)}
                    onClick={() => toggleBand(bd.key)}
                    data-testid={`band-${bd.key}`}
                    title={`${bd.name}: ${bd.band}`}
                  >
                    <span class="ti" dangerouslySetInnerHTML={{ __html: icon(bd.icon, 14) }} />
                    <b>{bd.name}</b>
                  </button>
                ))}
            <span class="bchip-acts">
              <button class="lnk" onClick={() => set(view === 'det' ? { dets: null } : { bands: null })} title="Switch every one on">
                All
              </button>
              <button
                class="lnk"
                onClick={() => set(view === 'det' ? { dets: '1' } : { bands: BANDS[0]!.key })}
                title={view === 'det' ? 'Only D1' : 'Only Starlight'}
              >
                None
              </button>
            </span>
          </div>
        </div>
        <aside class="panel">
          <div class="panel-head">
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
                    class={time === k && film === null ? 'on' : ''}
                    title={film !== null ? `${tip} (Months scale only)` : tip}
                    disabled={film !== null}
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
                    film !== null ? null : (v) => set({ sat: v ? null : '0' }),
                    'the visits of the Days scale carry no saturation layer: their brightest pixels are drawn at full white',
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
                    opts.anomalies && !!an?.result,
                    an?.result ? (v) => set({ ano: v ? null : '0' }) : null,
                    'no anomaly search has been run for this field yet: “Find spectral anomalies here” runs it',
                  )}
                  {dchk(
                    'maskChk',
                    'warning',
                    'Artifacts',
                    'Artifacts: red where the SPHEREx pipeline flagged the pixel in many exposures of the season shown',
                    opts.artifacts,
                    wallView ? null : (v) => set({ art: v ? '1' : null }),
                    'shown on the Picture (and the Days scale), not on the walls',
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
                    (v) => set({ ref: v ? '1' : null }),
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
                    title="Playback speed: the time each season is shown ([ and ] change it)"
                  >
                    {[0.25, 0.5, 1, 2, 4].map((v) => (
                      <option value={String(v)}>{`Speed ${v}× · ${Math.round((HOLD_MS + FADE_MS) / v)} ms`}</option>
                    ))}
                  </select>
                  <button
                    class="lnk"
                    onClick={() => {
                      const done = (t: string) => {
                        setCopied(t);
                        setTimeout(() => setCopied(''), 2000);
                      };
                      try {
                        navigator.clipboard.writeText(location.href).then(
                          () => done('Copied'),
                          () => done('Copy blocked: use the address bar'),
                        );
                      } catch {
                        done('Copy blocked: use the address bar');
                      }
                    }}
                    title="Copy a link to this exact view"
                  >
                    <span dangerouslySetInnerHTML={{ __html: icon('link', 16) }} style={{ display: 'contents' }} />
                    {copied || 'Copy link'}
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
                <div
                  class="srow"
                  title="Red sources: bright at 3.8–5 µm and faint at 1.1–1.6 µm, against the field's own stars (cold brown dwarfs and the like)"
                >
                  <b>Red sources</b>
                  <span class="st">
                    {!redv
                      ? '3.8–5 µm against 1.1–1.6 µm'
                      : redv.state === 'waiting'
                        ? redv.error === 'busy'
                          ? 'measuring…'
                          : 'starts when the field is finished'
                        : redv.state === 'failed'
                          ? `failed: ${redv.error}`
                          : `${redv.result!.red.length} of ${redv.result!.sources} sources far redder than the field's stars`}
                  </span>
                  <button class="btn sm" disabled={!meta || redv?.state === 'done'} onClick={() => void findRed()} data-testid="red">
                    Find
                  </button>
                </div>
                {redv?.result?.red.map((x, k) => (
                  <button
                    key={k}
                    class="anrow"
                    onClick={() => pickRed(k)}
                    title="Select this source: its spectrum is measured in every exposure"
                  >
                    <b>Red source {k + 1}</b>
                    <span class="t">
                      {redv.result!.median_colour
                        ? `${x.lower_limit ? '> ' : ''}${(x.colour / redv.result!.median_colour).toFixed(1)}× redder than the field's stars`
                        : ''}
                      {x.moves && x.pmra != null ? ` · moves ${(Math.hypot(x.pmra, x.pmdec ?? 0) / 1000).toFixed(1)}″/yr` : ''}
                    </span>
                    <span class="faint">S/N {x.snr_long.toFixed(0)}</span>
                  </button>
                ))}
                {film !== null && filmData && ft.state === 'ok' && (
                  <FilmTracks
                    compact
                    tracks={ft.tracks}
                    control={ft.control}
                    nVisits={filmData.meta.frames.length}
                    fov={fov}
                    sel={trackSel}
                    onSel={setTrackSel}
                    trail={false}
                    onTrail={() => {}}
                    blink={false}
                    onBlink={() => {}}
                    onAround={(t, f) => {
                      const g = filmData.meta.grid;
                      const pt = t.pts[(t.pts.length - 1) >> 1]!;
                      const cosd = Math.cos((g.dec0 * Math.PI) / 180);
                      const nra = (g.ra0 - ((pt.x + 0.5 - g.n / 2) * g.step_arcsec) / 3600 / cosd + 360) % 360;
                      const ndec = g.dec0 - ((pt.y + 0.5 - g.n / 2) * g.step_arcsec) / 3600;
                      set({ ra: nra.toFixed(5), dec: ndec.toFixed(5), fov: String(f) });
                    }}
                  />
                )}
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
                            : `${filmBodies.bodies.length} predicted (V ≤ ${filmBodies.vLimit}) · ${filmBodies.bodies.filter((b) => b.seen.length).length} seen on at least one visit · ${filmBodies.bodies.filter((b) => (b.stackZ ?? 0) >= 5).length} stand out when their visits are stacked along JPL's track (z ≥ 5)`}
                      </span>
                    </div>
                    {filmBodies.bodies.some((b) => b.name.includes('3I/ATLAS')) && <NeossatCard />}
                    {[...filmBodies.bodies]
                      .sort((a, b) => (b.stackZ ?? 0) - (a.stackZ ?? 0) || (a.v ?? 99) - (b.v ?? 99))
                      .slice(0, 10)
                      .map((b) => (
                        <div key={b.name} class="mvrow body" style={{ cursor: 'default' }}>
                          <b>
                            {b.name} · V {b.v?.toFixed(1) ?? '—'}
                          </b>
                          <span class="faint">
                            seen {b.seen.length} of {b.expected.length} film visits
                            {b.stackZ != null && b.stackN
                              ? ` · along its track z ${b.stackZ.toFixed(1)} (chance ${b.controlZ?.toFixed(1)})`
                              : ''}
                            {b.planes != null ? ` · in ${b.planes} detector images` : ''}
                          </span>
                          <span class="bodylinks">
                            <a
                              target="_blank"
                              rel="noopener"
                              href={`https://ssd.jpl.nasa.gov/tools/sbdb_lookup.html#/?sstr=${encodeURIComponent(sbdbName(b.name))}`}
                              title="The body's orbit and data at JPL's Small-Body Database"
                            >
                              JPL
                            </a>
                            <a
                              target="_blank"
                              rel="noopener"
                              href={ssoisUrl(b.name, filmMjd)}
                              title="Other telescopes' images of this body around the same dates (CADC's Solar System Object Image Search)"
                            >
                              SSOIS
                            </a>
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
                <>
                  {portraitOf(
                    an.result.anomalies.find((a) => a.n === selAn)!,
                    `Anomaly ${selAn}`,
                  )}
                  <Passport a={an.result.anomalies.find((a) => a.n === selAn)!} f={an.result} />
                  <ContextRows
                    ra={an.result.anomalies.find((a) => a.n === selAn)!.ra}
                    dec={an.result.anomalies.find((a) => a.n === selAn)!.dec}
                  />
                </>
              ) : (
                <>
                  {sel && portraitOf(sel, sel.name ?? 'selected source')}
                  <ObjectTab src={sel} onSpectrum={() => setTab('spec')} />
                  {sel && <ContextRows ra={sel.ra} dec={sel.dec} epoch={2000} />}
                  {sel && <LightCurves src={sel} />}
                  {sel && (
                    <div class="xlinks">
                      <span class="faint">Look it up:</span>
                      <a
                        target="_blank"
                        rel="noopener"
                        href={`https://simbad.cds.unistra.fr/simbad/sim-coo?Coord=${sel.ra.toFixed(5)}+${sel.dec.toFixed(5)}&Radius=10&Radius.unit=arcsec`}
                      >
                        SIMBAD
                      </a>
                      <a
                        target="_blank"
                        rel="noopener"
                        href={`https://vizier.cds.unistra.fr/viz-bin/VizieR-4?-c=${sel.ra.toFixed(5)}+${sel.dec.toFixed(5)}&-c.rs=10`}
                      >
                        VizieR
                      </a>
                      <a
                        target="_blank"
                        rel="noopener"
                        href={`https://www.legacysurvey.org/viewer?ra=${sel.ra.toFixed(5)}&dec=${sel.dec.toFixed(5)}&layer=ls-dr10&zoom=15`}
                      >
                        Legacy Survey
                      </a>
                      <a
                        target="_blank"
                        rel="noopener"
                        href={`https://irsa.ipac.caltech.edu/applications/finderchart/?locstr=${sel.ra.toFixed(5)}+${sel.dec.toFixed(5)}`}
                      >
                        IRSA finder chart
                      </a>
                    </div>
                  )}
                </>
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
              <div class="seg sm scale" role="group" aria-label="Time scale">
                <button
                  class={film === null ? 'on' : ''}
                  aria-pressed={film === null}
                  onClick={toMonths}
                  title="Months: the passes (seasons) of the field, about six months apart: stars that move over the year"
                  data-testid="scale-months"
                >
                  Months
                </button>
                <button
                  class={film !== null ? 'on' : ''}
                  aria-pressed={film !== null}
                  onClick={toDays}
                  disabled={!passes.length && film === null}
                  title="Days: the visits inside the pass shown, a few hours to days apart: asteroids hop from visit to visit"
                  data-testid="scale-days"
                >
                  Days
                </button>
              </div>
              <div class="tl-mid">
                {film === null ? (
                  <>
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
                  </>
                ) : (
                  <>
                    <div class="tl-labels ticks">
                      {(filmData?.meta.frames ?? []).map((fr, i, all) => {
                        const mid = (all.length - 1) >> 1;
                        const label = i === 0 || i === all.length - 1 || (i === mid && all.length > 4);
                        return (
                          <span
                            key={i}
                            class={`${visit === i ? 'on' : ''} ${label ? '' : 'tick'}`}
                            style={{
                              left: all.length > 1 ? `${(i / (all.length - 1)) * 100}%` : '0%',
                              transform: `translateX(${i === 0 ? 0 : i === all.length - 1 ? -100 : -50}%)`,
                            }}
                            title={`visit ${i + 1}: ${utc(fr.mjd)} · D${fr.det} · ${fr.exposures} exposures`}
                            onClick={() => {
                              setPlaying(false);
                              setFrame(i);
                            }}
                          >
                            {label
                              ? mjdToDate(fr.mjd).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' })
                              : '·'}
                          </span>
                        );
                      })}
                    </div>
                    <input
                      type="range"
                      min={0}
                      max={Math.max(0, nVisits - 1)}
                      step={1}
                      value={frame ?? visit}
                      aria-label="Visit"
                      disabled={!filmData}
                      onInput={(e) => {
                        setPlaying(false);
                        setFrame(Number((e.target as HTMLInputElement).value));
                      }}
                    />
                  </>
                )}
              </div>
            </div>
            <div class="status" data-testid="lab-status" title={film !== null ? daysStatus : status}>
              {film !== null ? daysStatus : status}
            </div>
            <div class="readout" />
          </div>
        </aside>
      </div>
    </div>
  );
}
