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
import {
  ContextRows,
  DeepSumPanel,
  DistantCanvas,
  DistantPanel,
  LinkPanel,
  PairButton,
  PairCard,
  drawTracks,
  hitTrack,
  useDistant,
  useFilmTracks,
  type FilmTrack,
  type ChancePairs,
  type Pair,
} from './search';
import { useDistantPlan } from './search/distant';
import { FPX_AUTO } from '../lib/flags';
import { useAsteroidSearch } from './asteroids';

const SIZES = [2, 5, 10, 20, 60];
type View = 'picture' | 'bands' | 'det';
type Time = 'loop' | 'diff' | 'sum';
type Tab = 'ctl' | 'cand' | 'obj' | 'spec';

const ICON_DET =
  '<svg viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="8" aria-hidden="true"><rect x="28" y="64" width="62" height="56" rx="8"/><rect x="97" y="64" width="62" height="56" rx="8"/><rect x="166" y="64" width="62" height="56" rx="8"/><rect x="28" y="136" width="62" height="56" rx="8"/><rect x="97" y="136" width="62" height="56" rx="8"/><rect x="166" y="136" width="62" height="56" rx="8"/></svg>';

/** one line on a view button; the full name is its tooltip */
const VIEW_WORD: Record<string, string> = { picture: 'Picture', bands: 'Bands', det: 'Detectors' };
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

/** the detectors whose range holds a band (D1 0.75–1.11, D2 1.11–1.64, D3 1.64–2.42, D4 2.42–3.82, D5 3.82–4.42, D6 4.42–5.00 µm) */
const BAND_DETS: Record<string, number[]> = { star: [1], h2o: [2, 3, 4], ch4: [2, 4], ice: [4], pah: [4], co2: [5], co: [6] };
/** a band's name on a tile too small for the full one */
const BAND_SHORT: Record<string, string> = { star: 'Star', h2o: 'H₂O', ch4: 'CH₄', ice: 'Ice', pah: 'Dust', co2: 'CO₂', co: 'CO' };
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

/** Short band names for a small picture (the full name stays in the tooltip). */
const SHORT: Record<string, string> = { star: 'Star', h2o: 'H₂O', ch4: 'CH₄', ice: 'Ice', pah: 'Dust', co2: 'CO₂', co: 'CO' };

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
/** A season by its middle month: "Aug ’25". */
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
  red: boolean;
  marker: boolean;
  sharp: boolean;
  trim: 'fixed' | 'p999' | 'p995' | 'adaptive';
  boost: boolean;
  sat: boolean;
  rings: boolean;
  morph: boolean;
  mono: boolean;
  artifacts: boolean;
  motion: boolean;
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
  /** 'red': found by the brown dwarf scan in the red bands (3.8–5 µm), where the white stacks do not show it */
  source?: 'red';
  /** the catalogue name the service found for it */
  name?: string;
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
interface DwarfRow {
  ra?: number;
  dec?: number;
  ra_j2000?: number;
  dec_j2000?: number;
  pmra?: number;
  pmdec?: number;
  pm?: number;
  pa?: number;
  colour?: number;
  status?: string;
  simbad?: { state: string; name?: string; type?: string };
}
interface DwarfView {
  state: string;
  error?: string;
  result?: {
    candidates: DwarfRow[];
    known: DwarfRow[];
    red_sources?: number;
    counts?: { red_sources: number; moving_red: number; candidates: number; known_brown_dwarfs: number; known_other: number };
  };
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
    passes_left_out?: number[];
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

/** The Gaia check said honestly: "Gaia ✓ 10.4" when the measured rate agrees within 2σ, else the tension in σ. */
function gaiaWord(t: Track): string {
  if (t.check !== 'same' || !t.gaia) return CHECK_SHORT[t.check] ?? t.check;
  const e = Math.max(speedErr(t), 0.05);
  const z = Math.abs(t.speed - t.gaia.pm) / e;
  return z <= 2 ? `Gaia ✓ ${t.gaia.pm.toFixed(1)}` : `Gaia ${t.gaia.pm.toFixed(1)}″/yr: ${z.toFixed(1)}σ apart`;
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
function MoversRows({
  mv,
  names,
  onPick,
  bare,
}: {
  mv: MoversView | null;
  names: string[];
  onPick: (t: Track, name: string) => void;
  /** in the searches bar: the summary as a quiet line under the row that already names the search */
  bare?: boolean;
}) {
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
            : `${r.tracks.length} track${r.tracks.length === 1 ? '' : 's'} · ${r.sources_per_pass.join(' / ')} sources per pass${r.still ? ` · ${r.still} centroid shift${r.still === 1 ? '' : 's'} rejected` : ''}${r.weak ? ` · ${r.weak} weak` : ''}${r.chance?.trials ? ` · chance ${r.chance.mean.toFixed(2)} tracks per field (${r.chance.trials} trials)` : ''}${r.passes_left_out?.length ? ` · season ${r.passes_left_out.map((i) => i + 1).join(', ')} left out (it covers only part of the field)` : ''}`;
  return (
    <>
      {bare ? (
        <div class="ssum">{state}</div>
      ) : (
        <div
          class="srow"
          title="Search between passes: sources that move in a straight line over the seasons (3–120 ″/yr), checked against Gaia DR3 afterwards"
        >
          <b>Movers</b>
          <span class="st">{state}</span>
        </div>
      )}
      {r?.tracks.map((t, i) => (
        <button
          key={i}
          class="mvrow"
          onClick={() => onPick(t, names[i] ?? '')}
          title="Select this mover: its spectrum is measured along its track"
        >
          <b>{names[i] || t.name || 'unnamed'}</b>
          <span>
            {rateText(t)} toward {t.pa.toFixed(0)}° · {t.passes.length} passes{t.saturated ? ' · saturated core' : ''}
            {t.source === 'red' ? ' · found in the red bands, 3.8–5 µm' : ''}
          </span>
          <span class="faint">{t.check === 'same' ? gaiaWord(t) : (CHECK_WORD[t.check] ?? t.check)}</span>
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
  const bands = p['bands'] === 'none' ? [] : p['bands'] ? p['bands'].split(',').filter((k) => allBands.includes(k)) : allBands;
  const dets =
    p['dets'] === 'none'
      ? []
      : p['dets']
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
    red: p['red'] !== '0',
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
    motion: p['mot'] === '1',
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
  const noiseRef = useRef(new Map<number, { bg: number; sigma: number }>());
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
      /** the label of a tile too small for the full one */
      short?: string;
      hue: number;
      /** the tile's own box and what it says when its detector or band has no exposure in the season shown */
      empty?: { x: number; y: number; w: number; h: number; text: string };
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
  const motionCache = useRef<{ key: string; canvas: HTMLCanvasElement } | null>(null);
  const artRef = useRef<HTMLCanvasElement | null>(null);
  const [stage, setStage] = useState({ w: 400, h: 400 });
  const stageRef = useRef<HTMLDivElement>(null);
  const [copied, setCopied] = useState('');
  const ft = useFilmTracks(filmData?.key ?? null);
  const ftRef = useRef<FilmTrack[] | null>(null);
  ftRef.current = ft.state === 'ok' ? ft.tracks : null;
  const [trackSel, setTrackSel] = useState(-1);
  const [note, setNote] = useState('');
  const [wallWait, setWallWait] = useState(false);
  const [trayOpen, setTrayOpen] = useState(() => typeof innerWidth === "number" && innerWidth > 860 && innerHeight > 640);
  const [dwarfs, setDwarfs] = useState<DwarfView | null>(null);
  const dwarfsRef = useRef<DwarfView | null>(null);
  dwarfsRef.current = dwarfs;
  const [bandsWait, setBandsWait] = useState(false);
  const [pairSel, setPairSel] = useState<number | null>(null);
  // the asteroid search runs on its own for the middle pass of the field (in Days: the pass on show)
  const astMjd = film ? film : meta?.passes[Math.floor((meta?.passes.length ?? 1) / 2)]?.mjd_mid;
  const ast = useAsteroidSearch(ra, dec, fov, astMjd, valid && !!meta);
  // a link from the Planet X page (fpx=1) sets the search's distance range and detectors and starts it once the field is open
  const fpx = p['fpx'] === '1';
  const fpxRange = fpx
    ? {
        dmin: Number(p['dmin']) > 0 ? Number(p['dmin']) : undefined,
        dmax: Number(p['dmax']) > 0 ? Number(p['dmax']) : undefined,
        det: /^[1-6]{1,6}$/.test((p['det'] ?? '').replace(/\D/g, '')) ? (p['det'] ?? '').replace(/\D/g, '') : undefined,
      }
    : {};
  const distRef = useDistant({
    ra,
    dec,
    fov,
    pass: null,
    mjd: (meta?.passes.find((q) => q.index === (shown?.pass ?? 0)) ?? meta?.passes[0])?.mjd_mid,
    ...fpxRange,
  });
  // Find Planet X on its own (FPX_AUTO): the other distance range and the other light choice run after the first, one after another
  const autoMjd = (meta?.passes.find((q) => q.index === (shown?.pass ?? 0)) ?? meta?.passes[0])?.mjd_mid;
  const distB = useDistant({ ra, dec, fov, pass: null, mjd: autoMjd, dmin: 30, dmax: 300, det: '4' });
  const distC = useDistant({ ra, dec, fov, pass: null, mjd: autoMjd, dmin: 250, dmax: 2000, det: '123' });
  const distD = useDistant({ ra, dec, fov, pass: null, mjd: autoMjd, dmin: 250, dmax: 2000, det: '4' });
  const [deepSt, setDeepSt] = useState<[string, string]>(['idle', 'idle']);
  const [deepFr, setDeepFr] = useState<[number | null, number | null]>([null, null]);
  const fpxStarted = useRef('');
  useEffect(() => {
    const key = `${ra},${dec},${fov}`;
    if (!fpx || !meta || distRef.available !== true || distRef.ds.state !== 'idle' || fpxStarted.current === key) return;
    fpxStarted.current = key;
    setTab('cand');
    void distRef.start(null);
  }, [fpx, meta, distRef.available, distRef.ds.state, ra, dec, fov]);
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
    prov: { ra0: ra, dec0: dec, step_arcsec: 1.5375, n: 4 },
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
    // the grid the service builds for this field (engine/grid.py), known before the field is finished: marks during a cold load
    prov: (() => {
      const step = fov <= 20 ? 1.5375 : 3.075;
      return { ra0: ra, dec0: dec, step_arcsec: step, n: Math.ceil((fov * 60) / step / 4) * 4 };
    })(),
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
    noiseRef.current.clear();
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
          noiseRef.current.set(k, { bg: lv.bg, sigma: lv.sigma });
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
        // the test target in the field is selected from the start (Object and Spectrum tabs show it); a click selects another source
        if (!selRef.current) {
          const half = (m.grid.n * m.grid.step_arcsec) / 2 / 3600;
          const o = targets.current.find((t) => {
            const [tra, tdec] = atEpoch(t.ra, t.dec, t.pmra ?? 0, t.pmdec ?? 0, 2025.9);
            return (
              Math.abs(tdec - m.grid.dec0) < half &&
              Math.abs(((tra - m.grid.ra0 + 540) % 360) - 180) * Math.cos((tdec * Math.PI) / 180) < half
            );
          });
          if (o) setSel({ ra: o.ra, dec: o.dec, name: o.n, pmra: o.pmra ?? 0, pmdec: o.pmdec ?? 0 });
        }
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
            inset = { x: 10, y: 52, w: S, h: S, zoom: z };
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
        // chosen bands whose pictures are still on their way: the picture stays as it was (no black frame), the status says so
        const bandsPending = st.view === 'picture' && stack === 'bands' && !r.loaded('bands').length;
        if (bandsPending) stack = r.loaded('picture').length ? 'picture' : 'quick';
        setBandsWait((w) => (w === bandsPending ? w : bandsPending));
        const all = r.loaded(stack);
        // Difference: the reference pass shows no change, so the loop plays the passes compared with it
        const loaded = st.time === 'diff' && all.length > 1 && st.frame === null ? all.filter((k) => k > 0) : all;
        const waiting = st.view !== 'picture' && !all.length;
        setWallWait((w) => (w === waiting ? w : waiting));
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
          let panels: (Panel & { label?: string; short?: string; sub?: string; hue?: number; empty?: string })[] = [];
          const passMeta0 = st.meta?.passes.find((q) => q.index === a);
          const lacks = (dets: number[]) => (passMeta0 && dets.length > 0 && dets.every((d) => !((passMeta0.detectors?.[`D${d}`] ?? 0) > 0)) ? dets : null);
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
            if (!st.bands.length) panels = []; // no band chosen: nothing to draw (black)
            const z = st.bands.length ? zoomOn() : null;
            if (z) {
              const S = Math.round(Math.min(220, w * 0.26));
              inset = { x: 10, y: 52, w: S, h: S, zoom: z };
              panels.push({ ...panels[0]!, ...inset });
            }
          } else if (st.view === 'bands') {
            // on a phone the status line sits over the bottom of the picture: the tiles leave room for it
            const wallRes = w < 560 ? 156 : 96;
            const shownBands = BANDS.map((bd, i) => ({ bd, i })).filter((x) => st.bands.includes(x.bd.key));
            panels = wallLayout(shownBands.length, w, h - wallRes).map((c0, j) => {
              const c = { ...c0, y: c0.y + 48 }; // clear of the title row above and the band buttons below
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
                short: BAND_SHORT[bd.key],
                sub: bd.band,
                hue: bd.hue,
                empty: lacks(BAND_DETS[bd.key] ?? []) ? `no exposure in ${(BAND_DETS[bd.key] ?? []).map((d) => `D${d}`).join(', ')} in this season` : undefined,
              };
            });
          } else {
            const wallRes = w < 560 ? 156 : 96;
            const shownDets = st.dets.slice().sort();
            panels = wallLayout(shownDets.length, w, h - wallRes).map((c0, j) => {
              const c = { ...c0, y: c0.y + 48 }; // clear of the title row above and the detector buttons below
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
                short: `D${d + 1}`,
                sub: DETECTORS[d]!.range,
                hue: DETECTORS[d]!.hue,
                empty: lacks([d + 1]) ? `no exposure in D${d + 1} in this season` : undefined,
              };
            });
          }
          shownPass = t < 0.5 ? a : b;
          for (const q of panels) {
            r.panel(q, a, b, t);
            if (!q.zoom) rects.push({ x: q.x, y: q.y, w: q.w, h: q.h });
            if (q.label)
              out.push({ x: q.x, y: q.y + q.h - 20, w: q.w, text: q.label, short: q.short, sub: q.sub ?? '', hue: q.hue ?? 0, empty: q.empty ? { x: q.x, y: q.y, w: q.w, h: q.h, text: q.empty } : undefined });
          }
          setShown((s) => (s && s.pass === a && s.stack === stack ? s : { pass: a, stack }));
        }
      }
      if (!rects.length && !days && st.view === 'picture') rects.push({ x: 0, y: 0, w, h }); // a cold field: the marks already
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
      ghost?: boolean;
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
      const grid = days && F ? F.meta.grid : (m?.grid ?? (!days && st.view === 'picture' ? st.prov : undefined));
      if (!grid) return;
      const { rects, inset } = layoutRef.current;
      if (days && F && st.opts.motion) {
        // Motion only: the visit's motion map (masked = black) instead of its sky, with amber only on real candidates: the change around a
        // track the search found on this visit, or around a known body JPL predicts there and the visit shows; elsewhere the sky stays
        // clean (the raw map's specks and a bright star's halo are not motion)
        const n = F.meta.grid.n;
        const cands: [number, number][] = [];
        for (const t of ftRef.current ?? []) for (const q of t.pts) if (q.f === shownVisit) cands.push([q.x, q.y]);
        for (const bd of bodiesRef.current?.bodies ?? []) {
          const fr = bd.frames[shownVisit];
          if (fr?.inside && bd.seen.includes(shownVisit)) cands.push([fr.x, fr.y]);
        }
        let mc = motionCache.current;
        const mkey = `${F.key}:${shownVisit}:${cands.length}`;
        if (!mc || mc.key !== mkey) {
          const c = document.createElement('canvas');
          c.width = c.height = n;
          const g = c.getContext('2d')!;
          const img = g.createImageData(n, n);
          const off = shownVisit * n * n;
          const zs = new Float32Array(n * n);
          for (let i = 0; i < n * n; i++) {
            const m = F.motion[off + i]!;
            zs[i] = m ? ((m - 1) / 254) * 20 - 5 : 0;
          }
          // the change connected to a candidate's place (within 6 grid pixels), above 3σ
          const keep = new Uint8Array(n * n);
          const stack: number[] = [];
          for (const [cx, cy] of cands) {
            for (let dy = -6; dy <= 6; dy++)
              for (let dx = -6; dx <= 6; dx++) {
                const x = Math.round(cx) + dx;
                const y = Math.round(cy) + dy;
                if (x < 0 || y < 0 || x >= n || y >= n || dx * dx + dy * dy > 36) continue;
                const i = y * n + x;
                if (!keep[i] && zs[i]! >= 3) {
                  keep[i] = 1;
                  stack.push(i);
                }
              }
          }
          while (stack.length) {
            const i = stack.pop()!;
            const x = i % n;
            for (const j of [x > 0 ? i - 1 : -1, x < n - 1 ? i + 1 : -1, i - n, i + n]) {
              if (j >= 0 && j < n * n && !keep[j] && zs[j]! >= 3) {
                keep[j] = 1;
                stack.push(j);
              }
            }
          }
          for (let i = 0; i < n * n; i++) {
            const m = F.motion[off + i]!;
            const z = zs[i]!;
            const a = m && keep[i] ? Math.min(1, Math.max(0.45, (z - 3) / 5)) : 0;
            img.data[4 * i] = m ? 16 + 239 * a : 0;
            img.data[4 * i + 1] = m ? 20 + 157 * a : 0;
            img.data[4 * i + 2] = m ? 26 + 33 * a : 0;
            img.data[4 * i + 3] = 255;
          }
          g.putImageData(img, 0, 0);
          mc = motionCache.current = { key: mkey, canvas: c };
        }
        ctx.imageSmoothingEnabled = false;
        for (const box of rects) ctx.drawImage(mc.canvas, box.x, box.y, box.w, box.h);
      }
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
      const shownMjd =
        days && F ? (F.meta.frames[shownVisit]?.mjd ?? F.meta.pass.mjd0) : ((m?.passes[shownPass] ?? m?.passes[0])?.mjd_mid ?? 60994.5);
      const year = 2000 + (shownMjd - 51544.5) / 365.25;
      const marks: Mark[] = [];
      if (st.opts.marker || st.opts.trajectory) {
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
            path: m?.passes.map((pp) => atEpoch(o.ra, o.dec, o.pmra ?? 0, o.pmdec ?? 0, 2000 + (pp.mjd_mid - 51544.5) / 365.25)),
            motion: (o.pmra ?? 0) || (o.pmdec ?? 0) ? [o.pmra ?? 0, o.pmdec ?? 0] : undefined,
            ghost: !st.opts.marker, // Trajectory alone: the path without the ring and the name
          });
        }
      }
      const mvv = mvRef.current;
      if (st.opts.movers && mvv?.result && !days) {
        mvv.result.tracks.forEach((t, ti) => {
          const yrs = year - 2025.9;
          const decN = t.dec_2025_9 + (t.v_north * yrs) / 3600;
          const raN = t.ra_2025_9 + (t.v_east * yrs) / 3600 / Math.cos((decN * Math.PI) / 180);
          const line = `${rateText(t)} · ${gaiaWord(t)}${t.saturated ? ' · saturated core' : ''}${t.source === 'red' ? ' · red bands' : ''}`;
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
              name: `Mover ${ti + 1}`,
              lines: [line],
              colour: '#ffb454',
              fill: '#ffd08a',
              rr: 18,
              path: t.ra.map((x, i) => [x, t.dec[i]!]),
              motion: [t.v_east * 1000, t.v_north * 1000],
              dashed: t.check === 'pending',
            });
        });
      }
      const near = (ra: number, dec: number, arcsec: number) =>
        marks.find((k) => Math.hypot((k.ra - ra) * Math.cos((dec * Math.PI) / 180), k.dec - dec) * 3600 < arcsec);
      const anv = anRef.current;
      // a big field keeps its picture readable: the strongest few anomalies and red sources are ringed, the full lists are in Candidates
      const ringMax = fovArc >= 3000 ? 5 : fovArc >= 1100 ? 8 : Infinity;
      // anomalies and red sources belong to the passes (Months); the Days scale shows the visit's own movers
      if (st.opts.anomalies && anv?.result && !days) {
        for (const a of anv.result.anomalies.filter((q) => q.n <= ringMax)) {
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
      if (rv?.result && !days && st.opts.red) {
        rv.result.red.forEach((x, i) => {
          if (i >= ringMax) return;
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
      const dw = dwarfsRef.current?.result;
      if (dw && !days && st.opts.red) {
        dw.candidates.forEach((c, i) => {
          const [cra, cdec] =
            c.ra_j2000 != null && c.dec_j2000 != null
              ? atEpoch(c.ra_j2000, c.dec_j2000, c.pmra ?? 0, c.pmdec ?? 0, year)
              : [c.ra ?? 0, c.dec ?? 0];
          const k = near(cra, cdec, 10);
          const what = `brown dwarf candidate ${i + 1}${c.status ? ` · ${c.status}` : ''}`;
          if (k) k.lines.push(what);
          else
            marks.push({
              ra: cra,
              dec: cdec,
              kind: 'red',
              name: `BD candidate ${i + 1}`,
              lines: c.simbad?.name ? [c.simbad.name] : [],
              colour: '#b98cff',
              fill: '#dcc8ff',
              rr: 16,
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
            // its path through the visits of the pass, where JPL predicts it on each (Trajectory)
            path: b.frames
              .filter((q) => q.inside)
              .map((q) => [
                grid.ra0 - ((q.x + 0.5 - n / 2) * grid.step_arcsec) / 3600 / cosd,
                grid.dec0 - ((q.y + 0.5 - n / 2) * grid.step_arcsec) / 3600,
              ]),
          });
        }
      }
      const fts = ftRef.current;
      if (days && F && fts && st.opts.movers) {
        drawTracks(
          ctx,
          fts,
          {
            scale: wcss / F.meta.grid.n,
            k: shownVisit,
            rings: true,
            trail: false,
            selected: trackSelRef.current,
            mjd: F.meta.frames.map((f) => f.mjd),
          },
          1,
        );
      }
      const sl = st.opts.marker ? selRef.current : null;
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
      let labelled = 0;
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
        // only the name or number at the ring; the rate, the checks and the rest are on the selected mark and in the Candidates list (the test target keeps its line)
        let lines = [mk.name, ...(mk.kind === 'target' || mk.selected ? mk.lines : [])].filter(Boolean);
        if (!lines.length) return;
        // a narrow picture: at most two lines, each cut to fit
        if (box.w < 520) {
          lines = lines.slice(0, 2);
          const maxW = box.w * 0.55;
          lines = lines.map((l, i) => {
            ctx.font = i
              ? '500 10.5px ui-monospace, Menlo, monospace'
              : '600 11px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
            let t = l;
            while (t.length > 4 && ctx.measureText(t).width > maxW) t = t.slice(0, -2);
            return t === l ? l : `${t.trimEnd()}…`;
          });
        }
        const tw = Math.max(
          ...lines.map((l, i) => {
            ctx.font = i
              ? '500 10.5px ui-monospace, Menlo, monospace'
              : '600 11px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
            return ctx.measureText(l).width;
          }),
        );
        const th = 13 * lines.length;
        // right of the ring, else left; moved down past labels already placed
        let lx = x + rr + 8;
        if (lx + tw > box.x + box.w - 4) lx = Math.max(box.x + 4, x - rr - 8 - tw);
        // a label never leaves the picture, on either side (a phone's picture is narrower than a long name)
        lx = Math.max(box.x + 4, Math.min(lx, box.x + box.w - 4 - tw));
        let ly = y - rr * 0.35 - 9;
        const ly0 = ly;
        for (let tries = 0; tries < 8; tries++) {
          const hit = placed.find((p) => lx < p.x1 && lx + tw > p.x0 && ly < p.y1 && ly + th > p.y0);
          if (!hit) break;
          ly = hit.y1 + 2;
        }
        // a narrow picture: a name pushed away from its own ring would point at the wrong thing, so it is left out (the ring and the list keep it)
        if (box.w < 520 && !mk.selected && mk.kind !== 'target' && Math.abs(ly - ly0) > 6) return;
        ly = Math.max(box.y + 2, Math.min(box.y + box.h - th - 2, ly));
        // pushed back inside the picture (at its bottom edge) the label can land on another one again: it then goes above that one, and
        // when no room is left only the selected mark and the test target keep their name (the ring and the list carry the rest)
        const clash = () => placed.find((p) => lx < p.x1 && lx + tw > p.x0 && ly < p.y1 && ly + th > p.y0);
        for (let tries = 0; tries < 8; tries++) {
          const hit = clash();
          if (!hit) break;
          ly = Math.max(box.y + 2, Math.min(box.y + box.h - th - 2, hit.y0 - th - 2));
        }
        if (clash() && !mk.selected && mk.kind !== 'target') return;
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
        }
        if (mk.motion && !days) {
          // the motion over the decades (2000 to 2050) as a dashed line with year ticks, never inside the ring: the trajectory
          // can be seen even when the passes of 2025–26 lie inside the star's own disc
          const [ve, vn] = mk.motion;
          const at = (yr: number): [number, number] => {
            const dt = yr - year;
            const d = mk.dec + (vn * dt) / 3.6e6;
            const r0 = mk.ra + (ve * dt) / 3.6e6 / Math.cos((d * Math.PI) / 180);
            const [u, v] = uvOf(r0, d);
            return [box.x + u * box.w, box.y + v * box.h];
          };
          const years = [2000, 2010, 2020, 2030, 2040, 2050];
          const pts = years.map((yr) => [yr, ...at(yr)] as const);
          ctx.save();
          ctx.beginPath();
          ctx.rect(box.x, box.y, box.w, box.h);
          ctx.arc(x, y, rr + 3, 0, 2 * Math.PI, true); // outside the ring only
          ctx.clip('evenodd');
          ctx.setLineDash([5, 4]);
          ctx.lineWidth = 3;
          ctx.strokeStyle = 'rgba(0,0,0,0.5)';
          ctx.beginPath();
          pts.forEach(([, px, py], i) => (i ? ctx.lineTo(px, py) : ctx.moveTo(px, py)));
          ctx.stroke();
          ctx.lineWidth = 1.3;
          ctx.strokeStyle = mk.colour;
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.font = '600 10px ui-monospace, Menlo, monospace';
          for (const [yr, px, py] of pts) {
            if (Math.hypot(px - x, py - y) < rr + 8) continue;
            if (px > x && Math.abs(py - y) < 30) continue; // where the object's own label stands
            ctx.fillStyle = mk.colour;
            ctx.beginPath();
            ctx.arc(px, py, 2.4, 0, 2 * Math.PI);
            ctx.fill();
            ctx.lineWidth = 3;
            ctx.strokeStyle = '#000';
            ctx.strokeText(String(yr), px + 5, py - 4);
            ctx.fillStyle = '#e8f6f4';
            ctx.fillText(String(yr), px + 5, py - 4);
          }
          ctx.restore();
        }
      };
      const wall = !days && st.view !== 'picture';
      const underInset = (x: number, y: number) =>
        (!!inset && x > inset.x - 4 && y > inset.y - 4 && x < inset.x + inset.w + 4 && y < inset.y + inset.h + 4) || (!wall && y < 46); // nor under the title row floating over the top of the picture
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
            if ((mk.kind !== 'target' && !mk.selected) || mk.ghost) continue;
            ring(x, y, mk, Math.max(8, Math.min(14, box.w / 22)), mk.kind === 'target');
            continue;
          }
          if (underInset(x, y)) continue;
          if (!mk.ghost) ring(x, y, mk, mk.rr, mk.kind === 'target');
          pathOf(mk, box, mk.rr, x, y);
        }
        if (!wall)
          for (const mk of all) {
            const [u, v] = uvOf(mk.ra, mk.dec);
            const x = box.x + u * box.w;
            const y = box.y + v * box.h;
            if (x < box.x || y < box.y || x > box.x + box.w || y > box.y + box.h || underInset(x, y) || mk.ghost) continue;
            // a crowded field: names for the first dozen marks (targets, movers, known bodies first), rings only for the rest
            if (++labelled > 14 && (mk.kind === 'anomaly' || mk.kind === 'red') && !mk.selected) continue;
            // a phone: names for the first six marks only (the selected one and the test target always)
            if (box.w < 520 && labelled > 6 && !mk.selected && mk.kind !== 'target') continue;
            label(x, y, mk.rr, mk, box);
          }
        ctx.restore();
      }
      if (inset) {
        // the 2′ zoom: the selected object ringed, scaled with the zoom
        const mk = all.find((k) => k.selected) ?? all.find((k) => k.kind === 'target' && !k.ghost);
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
      if (!st.opts.rings) return;
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
      // centred on the selected source, else the test target, else the middle of the field
      const centre = all.find((k) => k.selected) ?? all.find((k) => k.kind === 'target');
      for (const box of rects) {
        const [cu, cv] = centre ? uvOf(centre.ra, centre.dec) : [0.5, 0.5];
        const cx = box.x + cu * box.w;
        const cy = box.y + cv * box.h;
        ctx.save();
        ctx.beginPath();
        ctx.rect(box.x, box.y, box.w, box.h);
        ctx.clip();
        for (const v of speeds) {
          const rpx = ((v * dt) / fovArc) * box.w;
          if (rpx < 14 || rpx > box.w * 0.72) continue;
          ctx.beginPath();
          ctx.setLineDash([4, 4]);
          ctx.arc(cx, cy, rpx, 0, 2 * Math.PI);
          ctx.stroke();
          ctx.setLineDash([]);
          if (!wall || box === rects[0]) ctx.fillText(`${v}${unit}`, cx + rpx * 0.7071 + 3, cy - rpx * 0.7071 - 3);
        }
        ctx.restore();
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
    // the band pictures are fetched soon after the field opens, so choosing bands shows them at once
    const t = meta ? setTimeout(() => c.want('bands_'), 2500) : 0;
    if (opts.artifacts) c.want('flags_');
    if (tab === 'obj') c.want('dets_');
    return () => clearTimeout(t);
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

  // brown dwarf candidates: red sources that move between passes and have no Gaia counterpart (polled until done)
  useEffect(() => {
    setDwarfs(null);
    if (!valid || !meta) return;
    let stop = false;
    void (async () => {
      for (let i = 0; i < 200 && !stop; i++) {
        try {
          const r = await fetch(`/api/v1/browndwarfs?ra=${ra}&dec=${dec}&fov=${fov}`, { cache: 'no-store' });
          if (r.status === 404) return;
          if (r.ok) {
            const v = (await r.json()) as DwarfView;
            if (stop) return;
            setDwarfs(v);
            if (v.state === 'done' || v.state === 'failed') return;
          }
        } catch {
          /* asked again */
        }
        await new Promise((res) => setTimeout(res, 3000));
      }
    })();
    return () => {
      stop = true;
    };
  }, [ra, dec, fov, valid, meta]);

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
    setTab('obj');
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
    // the searches of a field start by themselves once it is built: anomalies and red sources (movers and asteroids have their own)
    void followAnomalies(true);
    void findRed();
    return () => {
      anPoll.current++;
    };
  }, [ra, dec, fov, valid, meta]);
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
    setTab('obj');
  };
  const pickAnomaly = (n: number) => {
    const a = an?.result?.anomalies.find((x) => x.n === n);
    if (!a) return;
    setSel({ ra: a.ra, dec: a.dec, name: `Anomaly ${n}`, pmra: a.pmra || undefined, pmdec: a.pmdec || undefined });
    setSelAn(n);
    setTab('obj');
  };

  /** a track found inside a pass: selected as an object (at its middle place in the film's grid) and its passport opened */
  const selectTrack = (i: number) => {
    setTrackSel(i);
    const t = ft.state === 'ok' ? ft.tracks[i] : undefined;
    const g = filmData?.meta.grid;
    if (t && g && t.pts.length) {
      const p = t.pts[Math.floor(t.pts.length / 2)]!;
      const cd = Math.cos((g.dec0 * Math.PI) / 180);
      setSelAn(null);
      setSel({
        ra: g.ra0 - ((p.x + 0.5 - g.n / 2) * g.step_arcsec) / 3600 / cd,
        dec: g.dec0 - ((p.y + 0.5 - g.n / 2) * g.step_arcsec) / 3600,
        name: t.known?.name ?? `Track ${i + 1}`,
      });
    }
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
  const drawTargets = (ctx: CanvasRenderingContext2D, size: number, epoch: number, ring: number, withPath = false) => {
    if (!meta) return;
    const scale = size / meta.grid.n;
    const xy = (ra0: number, dec0: number): [number, number] => [
      (meta.grid.n / 2 -
        ((((ra0 - meta.grid.ra0 + 540) % 360) - 180) * Math.cos((meta.grid.dec0 * Math.PI) / 180) * 3600) / meta.grid.step_arcsec) *
        scale,
      (meta.grid.n / 2 - ((dec0 - meta.grid.dec0) * 3600) / meta.grid.step_arcsec) * scale,
    ];
    for (const o of targets.current) {
      const [x, y] = xy(...atEpoch(o.ra, o.dec, o.pmra ?? 0, o.pmdec ?? 0, epoch));
      if (x < -ring || y < -ring || x > size + ring || y > size + ring) continue;
      if (withPath && ((o.pmra ?? 0) || (o.pmdec ?? 0))) {
        // the path from this image's date to the SPHEREx passes, a dot and a year at every archive image and pass
        const years = [...ARCHIVE.map((a) => a.epoch), ...(meta.passes.map((q) => 2000 + (q.mjd_mid - 51544.5) / 365.25) ?? [])].sort(
          (a, b) => a - b,
        );
        const pts = years.map((yr) => [yr, ...xy(...atEpoch(o.ra, o.dec, o.pmra ?? 0, o.pmdec ?? 0, yr))] as const);
        ctx.save();
        ctx.setLineDash([3, 3]);
        ctx.lineWidth = 2.6;
        ctx.strokeStyle = 'rgba(0,0,0,0.6)';
        ctx.beginPath();
        pts.forEach(([, px, py], i) => (i ? ctx.lineTo(px, py) : ctx.moveTo(px, py)));
        ctx.stroke();
        ctx.lineWidth = 1.3;
        ctx.strokeStyle = '#7ff0e4';
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.font = `600 ${Math.round(10 * (size / Math.max(1, side)) + 0.5)}px ui-monospace, Menlo, monospace`;
        let lastLabel = -1e9;
        for (const [yr, px, py] of pts) {
          const now = Math.abs(yr - epoch) < 0.6;
          ctx.fillStyle = now ? '#ffb454' : '#7ff0e4';
          ctx.beginPath();
          ctx.arc(px, py, now ? 3.6 * (size / side) : 2.4 * (size / side), 0, 2 * Math.PI);
          ctx.fill();
          if (yr - lastLabel > 4) {
            ctx.lineWidth = 3;
            ctx.strokeStyle = '#000';
            ctx.strokeText(String(Math.round(yr)), px + 6 * (size / side), py + 4 * (size / side));
            ctx.fillText(String(Math.round(yr)), px + 6 * (size / side), py + 4 * (size / side));
            lastLabel = yr;
          }
        }
        ctx.restore();
      }
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
      drawTargets(ctx, px, s.epoch, 18 * (px / side), opts.trajectory);
    }
  }, [archTick, archStep, opts.reference, opts.trajectory, side, meta]);
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
    if (!m || film !== null) return;
    const box = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const lx = e.clientX - box.left;
    const ly = e.clientY - box.top;
    if (view === 'picture' && insetBox && !opts.reference) {
      if (lx >= insetBox.x && ly >= insetBox.y && lx <= insetBox.x + insetBox.w && ly <= insetBox.y + insetBox.h) return; // the zoom itself
    }
    // the panel under the click (the whole picture, or one panel of a wall)
    const panel =
      view === 'picture'
        ? { x: 0, y: 0, w: box.width, h: box.height }
        : layoutRef.current.rects.find((q) => lx >= q.x && ly >= q.y && lx <= q.x + q.w && ly <= q.y + q.h);
    if (!panel) return;
    const rect = { width: panel.w, height: panel.h };
    let gx = ((lx - panel.x) / panel.w) * m.grid.n;
    let gy = ((ly - panel.y) / panel.h) * m.grid.n;
    setPairSel(null);
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
        setTab('obj');
        return;
      }
    }
    const pk = pictures.current.has(shown?.pass ?? 0) ? (shown?.pass ?? 0) : (pictures.current.keys().next().value ?? 0);
    const pic = pictures.current.get(pk);
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
      // nothing there: empty sky is said, not measured
      const lv = noiseRef.current.get(pk);
      if (lv && best / 3 < lv.bg + 5 * lv.sigma) {
        setNote('Empty sky here: no source above 5σ within 3 grid pixels of the click, nothing to measure.');
        setTimeout(() => setNote(''), 3500);
        return;
      }
    }
    const [ra2, dec2] = toSky(gx, gy);
    setSelAn(null);
    setSel({ ra: ra2, dec: dec2 });
    setTab('obj');
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
      bands: next.length === BANDS.length ? null : next.length ? next.join(',') : 'none',
    });
  };
  const toggleDet = (d: number) => {
    const next = dets.includes(d) ? dets.filter((x) => x !== d) : [...dets, d];
    set({
      dets: next.length === 6 ? null : next.length ? next.sort().join(',') : 'none',
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
        ? `Archive · ${ARCHIVE[archStep]!.name} · ${ARCHIVE[archStep]!.years}${archOffset(ARCHIVE[archStep]!.epoch)} · then the SPHEREx passes`
        : shown && meta
          ? `${time === 'sum' ? 'All seasons co-added' : `Season ${shown.pass + 1} of ${passes.length} · ${passMeta ? season(passMeta.mjd_mid) : ''}`} · ${colourWord}${time === 'diff' ? (shown.pass === 0 ? ' · Difference: reference season' : ' · Difference against season 1') : ''} · ${meta.exposures} exposures${meta.overlapping > meta.exposures ? ` (an even sample of the ${meta.overlapping} that cover the field)` : ''}${time === 'loop' && seq ? ` · passes ${seq.slice(0, -2)}` : ''}${phase ? ` · ${phase}` : ''}${Math.abs(eclipticLat(ra, dec)) < 10 ? ' · near the ecliptic: asteroids likely here, switch to Days' : ''}`
          : phase;
  const frameIndex = frame ?? shown?.pass ?? 0;
  const wallView = view !== 'picture' && film === null;
  /** How far the test target in the field was, on an archive image's date, from its place in 2025.9 (and which way). */
  function archOffset(epoch: number): string {
    const half = fov / 120;
    const o = targets.current.find((t) => {
      const [tra, tdec] = atEpoch(t.ra, t.dec, t.pmra ?? 0, t.pmdec ?? 0, 2025.9);
      return Math.abs(tdec - dec) < half && Math.abs(((tra - ra + 540) % 360) - 180) * Math.cos((dec * Math.PI) / 180) < half;
    });
    if (!o || !((o.pmra ?? 0) || (o.pmdec ?? 0))) return '';
    const [a0, d0] = atEpoch(o.ra, o.dec, o.pmra ?? 0, o.pmdec ?? 0, epoch);
    const [a1, d1] = atEpoch(o.ra, o.dec, o.pmra ?? 0, o.pmdec ?? 0, 2025.9);
    const dx = (((a0 - a1 + 540) % 360) - 180) * Math.cos((d1 * Math.PI) / 180) * 3600;
    const dy = (d0 - d1) * 3600;
    const r = Math.hypot(dx, dy);
    const dir = Math.abs(dy) > Math.abs(dx) ? (dy > 0 ? 'north' : 'south') : dx > 0 ? 'east' : 'west';
    return ` · ${o.n} was ${r >= 60 ? `${(r / 60).toFixed(1)}′` : `${r.toFixed(0)}″`} ${dir} of its 2025 place`;
  }
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
    const pic = pictures.current.get(k);
    const wbq = meta.passes.find((x) => x.index === k)?.white_balance;
    const data: PortraitData = {
      n: d.n,
      dets: d.dets,
      sat: satData.current.get(k),
      stepArcsec: g.step_arcsec,
      rgb: pic && pic.n === d.n ? pic.rgb : undefined,
      wb: wbq ? { kr: wbq.kr, kb: wbq.kb } : undefined,
    };
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
        : `Days · pass of ${season((filmData.meta.pass.mjd0 + filmData.meta.pass.mjd1) / 2)} · ${nVisits} visits · visit ${visit + 1} · ${utc(filmData.meta.frames[visit]?.mjd ?? 0)} · ` +
          (filmBodies?.state === 'running'
            ? 'asking JPL which known asteroids cross the field…'
            : filmBodies
              ? `${filmBodies.bodies.length} known asteroids predicted, ${bodiesSeen} seen in the visits`
              : '') +
          (Math.abs(beta) > 25 ? ' · far from the ecliptic: few asteroids expected here' : '') +
          (opts.motion ? ' · motion only' : '');
  const passMjd = (passes.find((q) => q.index === (shown?.pass ?? 0)) ?? passes[0])?.mjd_mid;
  const toDays = () => {
    setFrame(null);
    set({ film: passMjd ? passMjd.toFixed(1) : '0', view: null });
  };
  const toMonths = () => {
    setFrame(null);
    set({ film: null });
  };

  const sw = (
    id: string,
    text: string,
    title: string,
    checked: boolean,
    onChange: ((v: boolean) => void) | null,
    why = 'not available in this view',
    whyShort = '',
  ) => (
    <label class={`sw ${onChange ? '' : 'dis'}`} title={onChange ? title : `${title} (${why})`}>
      <input
        type="checkbox"
        id={id}
        aria-label={text}
        checked={checked}
        disabled={!onChange}
        onChange={(e) => onChange?.((e.target as HTMLInputElement).checked)}
      />
      <span>{text}</span>
      {!onChange && whyShort && <em class="why">{whyShort}</em>}
    </label>
  );
  const pct = (f: number | null | undefined) => (f === null || f === undefined ? null : Math.max(0, Math.min(100, Math.round(f * 100))));
  const srow = (name: string, frac: number | null, right: string, title: string, testid?: string) => (
    <div class="sline" title={title} data-testid={testid}>
      <b>{name}</b>
      <span class={`sprog ${frac === null ? 'busy' : ''}`}>
        <i style={{ width: `${frac === null ? 30 : Math.max(2, frac * 100)}%` }} />
      </span>
      <span class="sval">{right}</span>
    </div>
  );
  const mvr = mv?.result;
  const pairs = ((mv as unknown as { pairs?: Pair[] } | null)?.pairs ?? []) as Pair[];
  const pairMjds = (mv as unknown as { mjd?: number[] } | null)?.mjd ?? passes.map((q) => q.mjd_mid);
  const anFrac = an?.state === 'done' ? 1 : an?.state === 'running' ? (an.total ? an.done / an.total : null) : 0;
  const dist = distRef;
  const pxPlan = useDistantPlan(
    { ra, dec, fov, pass: null, mjd: (meta?.passes.find((q) => q.index === (shown?.pass ?? 0)) ?? meta?.passes[0])?.mjd_mid },
    dist.available === true && valid,
  );

  // Find Planet X on its own: after the field's other scans and behind a person's picture, one search at a time
  const fpxRuns = [distRef, distB, distC, distD];
  const scanOver = (v?: string) => v === 'done' || v === 'failed' || v === 'none';
  const othersOver =
    !!mv && scanOver(mv.state) && !!redv && scanOver(redv.state) && !!dwarfs && scanOver(dwarfs.state) && (!an || an.state === 'done' || an.state === 'failed' || an.state === 'idle') && scanOver(ast.state);
  const pictureBusy = !meta || wallWait || (film !== null && !filmData);
  const fpxHold = !FPX_AUTO || !valid || pictureBusy || !othersOver;
  const fpxOff = fpxRuns.some((r) => r.available !== true);
  useEffect(() => {
    if (fpxHold || fpxOff || fpx) return;
    if (fpxRuns.some((r) => r.ds.state === 'loading')) return;
    const next = fpxRuns.find((r) => r.ds.state === 'idle');
    if (next) void next.start(null, true);
  });
  const fpxAllOver = FPX_AUTO && fpxRuns.every((r) => r.ds.state !== 'idle' && r.ds.state !== 'loading');
  const deepOn = FPX_AUTO && passes.length >= 3;
  const pxState = (() => {
    if (FPX_AUTO && !fpx) {
      if (fpxOff) return { frac: 0, text: 'not offered' };
      const parts = [...fpxRuns.map((r) => (r.ds.state === 'ok' ? 1 : r.ds.state === 'loading' ? (r.ds.frac ?? 0) : r.ds.state === 'idle' ? 0 : 1)), ...(deepOn ? deepFr.map((f) => f ?? (0 as number)) : [])];
      const frac = parts.reduce((a, b) => a + b, 0) / parts.length;
      const active = fpxRuns.some((r) => r.ds.state === 'loading') || deepSt.some((x) => x === 'planning' || x === 'plan' || x === 'running');
      const finished = fpxAllOver && (!deepOn || deepSt.every((x) => x === 'done' || x === 'none' || x === 'error'));
      if (finished) {
        const n = fpxRuns.reduce((a, r) => a + (r.ds.state === 'ok' ? (r.ds.r.candidates?.length ?? 0) : 0), 0);
        return { frac: 1, text: `${n} found` };
      }
      if (!active && fpxHold) return { frac: 0, text: 'waiting for the other scans' };
      return { frac, text: `${Math.round(frac * 100)} %` };
    }
    const ds = dist.ds;
    if (dist.available !== true) return { frac: 0, text: 'not offered' };
    if (ds.state === 'idle') return { frac: 0, text: pxPlan ? (pxPlan.estimate_s < 90 ? '~1 min' : pxPlan.estimate_s < 5400 ? `~${Math.round(pxPlan.estimate_s / 60)} min` : `~${(pxPlan.estimate_s / 3600).toFixed(1)} h`) : 'ready' };
    if (ds.state === 'loading') return { frac: ds.frac, text: ds.frac != null ? `${Math.round(ds.frac * 100)} %` : 'running' };
    if (ds.state === 'ok') return { frac: 1, text: `${ds.r.candidates?.length ?? 0} found` };
    if (ds.state === 'none') return { frac: 0, text: 'no data' };
    return { frac: 0, text: 'failed' };
  })();
  const goPx = (e: Event, deep: boolean) => {
    e.stopPropagation();
    setTab('cand');
    if (!deep && dist.ds.state === 'idle') void dist.start(null);
    setTimeout(() => document.getElementById('px-section')?.scrollIntoView({ block: 'start', behavior: 'smooth' }), 80);
  };
  const scanItems: (readonly [string, string | null])[] = [
    ...(
  [
                  ['Movers', mv?.state === 'done' ? String(mvr?.tracks.length ?? 0) : null],
                  ['Pairs', mv?.state === 'done' ? String(pairs.length) : null],
                  ['Red', redv?.state === 'done' ? String(redv.result?.red.length ?? 0) : null],
                  [
                    'Brown dwarfs',
                    dwarfs?.state === 'done'
                      ? `${dwarfs.result?.counts?.candidates ?? dwarfs.result?.candidates.length ?? 0}${dwarfs.result?.counts?.known_brown_dwarfs ? ` (+${dwarfs.result.counts.known_brown_dwarfs} known)` : ''}`
                      : dwarfs
                        ? null
                        : '–',
                  ],
                  [
                    'Anomalies',
                    an?.state === 'done'
                      ? String(an.result?.anomalies.length ?? 0)
                      : an?.state === 'running'
                        ? `${pct(anFrac) ?? 0}%`
                        : null,
                  ],
                  [
                    'Asteroids',
                    ast.state === 'done'
                      ? String(ast.tracks?.length ?? 0)
                      : ast.state === 'none'
                        ? '–'
                        : ast.frac !== null && ast.state !== 'idle'
                          ? `${pct(ast.frac)}%`
                          : null,
                  ],
                ] as readonly (readonly [string, string | null])[]
    ),
    ['Planet X', FPX_AUTO && !fpx ? ((pxState.frac ?? 0) >= 1 ? pxState.text.replace(' found', '') : pxState.text.endsWith('%') ? pxState.text : null) : dist.ds.state === 'ok' ? String(dist.ds.r.candidates?.length ?? 0) : dist.ds.state === 'loading' ? `${Math.round((dist.ds.frac ?? 0) * 100)}%` : null],
  ];
  const foundCount =
    (mvr?.tracks.length ?? 0) +
    pairs.length +
    (redv?.result?.red.length ?? 0) +
    (an?.result?.anomalies.length ?? 0) +
    (ast.tracks?.length ?? 0);

  return (
    <div class="screen">
      <div class="lab">
        <div class="vwrap" ref={wrap}>
          <div class="vstage" ref={stageRef}>
            <div
              class={`vsq ${side < 760 ? 'compact' : ''}`}
              style={{
                width: `${side}px`, // one square in every view: the frame never moves or grows
                height: `${side}px`,
                // whole pixels, so that the 1 px frame is crisp and never one pixel thick in one state and two in another
                marginLeft: `${Math.max(0, Math.floor((stage.w - side) / 2))}px`,
                marginTop: `${Math.max(0, Math.floor((stage.h - side) / 2))}px`,
                cursor: 'crosshair',
              }}
              onClick={(e) => {
                if (dragged.current) {
                  dragged.current = false;
                  return;
                }
                if ((e.target as HTMLElement).closest('.float')) return;
                if (film !== null && filmData && ft.state === 'ok') {
                  // a click on a track's badge selects the track and opens the findings
                  const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
                  const hit = hitTrack(
                    ft.tracks,
                    visit,
                    r.width / filmData.meta.grid.n,
                    e.clientX - r.left,
                    e.clientY - r.top,
                    filmData.meta.frames.map((f) => f.mjd),
                  );
                  if (hit >= 0) {
                    selectTrack(hit);
                    return;
                  }
                }
                pick(e);
              }}
              onPointerDown={(e) => {
                if (film !== null || view !== 'picture' || !meta || (e.target as HTMLElement).closest('.float')) return;
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
              {labels.map((l) =>
                l.empty ? (
                  <div class={`panel-empty${l.w < 200 ? ' small' : ''}`} style={{ left: `${l.empty.x}px`, top: `${l.empty.y}px`, width: `${l.empty.w}px`, height: `${l.empty.h}px` }}>
                    <span>{l.empty.text}</span>
                  </div>
                ) : null,
              )}
              {labels.map((l) => {
                // a tile under 200 px has room for the short name only (D1, CO₂), not for the wavelength range
                const small = l.w < 200;
                return (
                  <div class="panel-label" style={{ left: `${l.x + 6}px`, top: `${l.y + 2}px`, maxWidth: `${l.w - 10}px` }}>
                    <span class="sw" style={{ background: `hsl(${l.hue},80%,60%)`, boxShadow: `0 0 8px hsl(${l.hue},80%,60%)` }} />
                    <b>{small && l.short ? l.short : l.text}</b>
                    {!small && <em>{l.sub}</em>}
                  </div>
                );
              })}
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
              {film === null && wallWait && (
                <div class="vwait">loading the pictures of every {view === 'det' ? 'detector' : 'band'} for each pass…</div>
              )}
              {film !== null && !filmData && <div class="vwait">{filmNote || 'asking for the visits of the pass…'}</div>}
              {dist.ds.state === 'ok' && film === null && view === 'picture' && <DistantCanvas d={dist} />}
              <div class="float ftitle" data-testid="field-title">
                <b>{fieldName}</b>
                <span>
                  {formatRA(ra)} {formatDec(dec)}
                </span>
              </div>
              <div class="float factions">
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
              <div class="float fstatus" data-testid="lab-status" title={film !== null ? daysStatus : status}>
                {note ||
                  (bandsWait
                    ? 'loading the pictures of the chosen bands…'
                    : film === null && view !== 'det' && !bands.length
                      ? 'No band chosen: switch a band on (or ALL) to see the sky'
                      : film === null && view === 'det' && !dets.length
                        ? 'No detector chosen: switch one on (or ALL)'
                        : film !== null
                          ? daysStatus
                          : status)}
                {time === 'diff' && film === null && (
                  <span class="dkey">
                    <i style={{ background: 'var(--magenta)' }} /> brighter <i style={{ background: 'var(--cyan)' }} /> fainter
                  </span>
                )}
              </div>
              <div class="float fbands" role="group" aria-label={view === 'det' ? 'Detectors' : 'Spectral bands'}>
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
                        <i class="dot" />
                        <b>{side < 760 ? (SHORT[bd.key] ?? bd.name) : bd.name}</b>
                      </button>
                    ))}
                <button class="mini" onClick={() => set(view === 'det' ? { dets: null } : { bands: null })} title="Switch every one on">
                  ALL
                </button>
                <button
                  class="mini"
                  onClick={() => set(view === 'det' ? { dets: 'none' } : { bands: 'none' })}
                  title={view === 'det' ? 'Switch every detector off' : 'Switch every band off'}
                >
                  NONE
                </button>
              </div>
            </div>
          </div>
          <div class="tbar">
            <button
              class="play"
              onClick={() => {
                setFrame(null);
                setPlaying(!playing);
              }}
              title="Play / pause (Space)"
              dangerouslySetInnerHTML={{ __html: icon(playing && frame === null ? 'pause' : 'play', 16) }}
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
            <div class="seg sm scale" role="group" aria-label="Time">
              {(
                [
                  ['loop', 'eye', 'Blink', 'Blink: seasons play one after another. Look for sources that move or vary'],
                  [
                    'diff',
                    'plus-minus',
                    'Difference',
                    'Difference against season 1: a real change is shown (magenta brighter, cyan fainter)',
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
                  <span dangerouslySetInnerHTML={{ __html: icon(ico, 14) }} style={{ display: 'contents' }} />
                  {n}
                </button>
              ))}
            </div>
            <div class="tl-mid">
              {film === null && archStep !== null ? (
                <>
                  <div class="tl-labels">
                    {[...ARCHIVE.map((a) => String(Math.round(a.epoch))), ...passes.map((q) => season(q.mjd_mid))].map((t, j, all) => (
                      <span
                        key={j}
                        class={archStep === j ? 'on' : ''}
                        style={{
                          left: all.length > 1 ? `${(j / (all.length - 1)) * 100}%` : '0%',
                          transform: `translateX(${j === 0 ? 0 : j === all.length - 1 ? -100 : -50}%)`,
                        }}
                      >
                        {archStep === j || j === 0 || j === all.length - 1 ? t : '·'}
                      </span>
                    ))}
                  </div>
                  <input
                    type="range"
                    min={0}
                    max={ARCHIVE.length + passes.length - 1}
                    step={1}
                    value={archStep}
                    aria-label="Archive images, then the passes"
                    disabled
                  />
                </>
              ) : film === null ? (
                <>
                  <div class="tl-labels">
                    {passes.map((q, i) => (
                      <span
                        key={q.index}
                        class={frameIndex === q.index && time !== 'sum' ? 'on' : ''}
                        style={{
                          left: passes.length > 1 ? `${(i / (passes.length - 1)) * 100}%` : '0%',
                          transform: `translateX(${i === 0 ? 0 : i === passes.length - 1 ? -100 : -50}%)`,
                        }}
                        onClick={() => {
                          setPlaying(false);
                          setFrame(q.index);
                        }}
                      >
                        {frameIndex === q.index || passes.length <= 2 ? season(q.mjd_mid) : '·'}
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
                          {label ? mjdToDate(fr.mjd).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' }) : '·'}
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
            <select
              class="inp spd"
              value={String(speed)}
              onChange={(e) => setSpeed(Number((e.target as HTMLSelectElement).value))}
              title={`Playback speed ([ and ] change it); 1× = ${HOLD_MS + FADE_MS} ms a season`}
            >
              {[0.25, 0.5, 1, 2, 4].map((v) => (
                <option value={String(v)}>{`${v}×`}</option>
              ))}
            </select>
            <span class="acts">
              <button
                class="mini"
                onClick={() => {
                  const done = (t: string) => {
                    setCopied(t);
                    setTimeout(() => setCopied(''), 2000);
                  };
                  try {
                    navigator.clipboard.writeText(location.href).then(
                      () => done('Copied'),
                      () => done('Copy blocked'),
                    );
                  } catch {
                    done('Copy blocked');
                  }
                }}
                title="Copy a link to this exact view"
                aria-label="Copy link"
              >
                <span dangerouslySetInnerHTML={{ __html: icon('link', 14) }} style={{ display: 'contents' }} />
                {copied && <span class="t">{copied}</span>}
              </button>
              <button
                class="mini"
                disabled={recording || film !== null || !passes.length}
                onClick={() => void recordVideo()}
                title="Download one full cycle of the seasons as a video (WebM)"
                aria-label="Download video"
              >
                <span dangerouslySetInnerHTML={{ __html: icon('download-simple', 14) }} style={{ display: 'contents' }} />
                {recording && <span class="t">Recording…</span>}
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
                  ['cand', 'Candidates'],
                  ['obj', 'Object'],
                  ['spec', 'Spectrum'],
                ] as const
              ).map(([k, n]) => (
                <button key={k} class={tab === k ? 'on' : ''} aria-pressed={tab === k} onClick={() => setTab(k)} data-testid={`tab-${k}`}>
                  {n}
                  {k === 'cand' && foundCount > 0 && <span class="tbadge">{foundCount}</span>}
                </button>
              ))}
            </div>
          </div>
          {tab === 'ctl' && (
            <div class="pbody">
              <section class="psec">
                <div class="psh">Place</div>
                <div class="coords">
                  <div class="field">
                    <label for="raIn">RA</label>
                    <input
                      id="raIn"
                      class={`inp coord ${raIn !== '' && parseRA(raIn) === null ? 'bad' : ''}`}
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
                      class={`inp coord ${decIn !== '' && parseDec(decIn) === null ? 'bad' : ''}`}
                      spellcheck={false}
                      value={decIn}
                      onInput={(e) => setDecIn((e.target as HTMLInputElement).value)}
                      onKeyDown={(e) => e.key === 'Enter' && goAim()}
                      onBlur={goAim}
                    />
                  </div>
                </div>
              </section>
              <section class="psec">
                <div class="psh">View</div>
                <div class="vsquares" role="group" aria-label="View">
                  {VIEWS.map((v) => (
                    <button
                      key={v.id}
                      class={`vsquare ${v.id === view && film === null ? 'on' : ''}`}
                      title={v.tip}
                      onClick={() => set({ view: v.id === 'picture' ? null : v.id, film: null })}
                      data-testid={`view-${v.id}`}
                    >
                      <span class="vi" dangerouslySetInnerHTML={{ __html: v.icon }} />
                      <span>{VIEW_WORD[v.id] ?? v.name}</span>
                    </button>
                  ))}
                </div>
              </section>
              <section class="psec">
                <div class="psh">Display</div>
                <div class="psub">Objects</div>
                <div class="swgrid">
                  {sw(
                    'ringChk',
                    'Target marker',
                    'Marker: a ring and the name of the test target, of the selected source and of the known asteroids, at their place on the date shown',
                    opts.marker,
                    (v) => set({ marker: v ? null : '0' }),
                  )}
                  {sw(
                    'movChk',
                    'Movers',
                    'Mover rings: a ring and a path for every track a search found (between passes in Months, inside the pass in Days)',
                    opts.movers && (film === null || (ft.state === 'ok' && ft.tracks.length > 0)),
                    film === null || (ft.state === 'ok' && ft.tracks.length > 0) ? (v) => set({ movers: v ? null : '0' }) : null,
                    ft.state === 'ok'
                      ? 'the asteroid search found no track in this pass'
                      : 'the asteroid search of this pass has not finished yet',
                  )}
                  {sw(
                    'redChk',
                    'Red sources',
                    'Red sources: a ring and the name on every source that is bright at 3.8–5 µm and faint at 1.1–1.6 µm against the field’s own stars (the brown dwarf candidates are among them)',
                    opts.red && !!redv?.result?.red.length,
                    redv?.result?.red.length ? (v) => set({ red: v ? null : '0' }) : null,
                    redv?.result ? 'the red-source search found none in this field' : 'the red-source search of this field has not finished yet',
                  )}
                  {sw(
                    'anoChk',
                    'Anomalies',
                    'Anomaly rings: sources whose spectrum differs from an ordinary star of the field',
                    opts.anomalies && !!an?.result?.anomalies.length,
                    an?.result?.anomalies.length ? (v) => set({ ano: v ? null : '0' }) : null,
                    an?.result
                      ? 'the anomaly search found no anomaly in this field'
                      : 'the anomaly search of this field has not finished yet',
                  )}
                  {sw(
                    'maskChk',
                    'Artifacts',
                    'Artifacts: red where the SPHEREx pipeline flagged the pixel in many exposures of the season shown',
                    opts.artifacts,
                    wallView ? null : (v) => set({ art: v ? '1' : null }),
                    'shown on the Picture and in Days, not on the walls',
                  )}
                </div>
                <div class="psub">Paths</div>
                <div class="swgrid">
                  {sw(
                    'trailChk',
                    'Trajectory',
                    'Trajectory: the path of the test target (or of a found mover) over the passes, a dot at each pass date',
                    opts.trajectory,
                    (v) => set({ traj: v ? null : '0' }),
                  )}
                  {sw(
                    'ringsChk',
                    'Speed rings',
                    'Speed rings: how far a body of 1, 3, 10, 30, 100 or 300 ″/yr moves between the first and the last pass (″/h between the first and last visit in Days), centred on the selected source',
                    opts.rings,
                    (v) => set({ rings: v ? '1' : null }),
                  )}
                </div>
                <div class="psub">Picture</div>
                <div class="swgrid">
                  {sw(
                    'boostChk',
                    'Brighten',
                    'Brighten: a lower white point, so faint sources stand out (bright stars saturate)',
                    opts.boost,
                    (v) => set({ boost: v ? '1' : null }),
                  )}
                  {sw(
                    'sharpChk',
                    'Sharpen',
                    'Sharpen: faint stars crisper on screen; boost only, the same gain in every channel (colours never change), never near saturated cores',
                    opts.sharp,
                    (v) => set({ sharp: v ? null : '0' }),
                  )}
                  {sw(
                    'satChk',
                    'White cores',
                    'Saturated cores white: pixels saturated in most exposures are drawn white, never as missing data',
                    opts.sat,
                    film !== null ? null : (v) => set({ sat: v ? null : '0' }),
                    'the visits of the Days scale carry no saturation layer: their cores are always drawn white',
                  )}
                  {sw(
                    'refChk',
                    'Reference',
                    'Reference: the DSS2 red picture of the same place (1985–1999) in the corner, the test target ringed where it was then',
                    opts.reference,
                    (v) => set({ ref: v ? '1' : null }),
                  )}
                </div>
                <div class="psub">Playback</div>
                <div class="swgrid">
                  {sw(
                    'morphChk',
                    'Interpolate',
                    'Interpolate: cross-fade between passes (or visits) instead of stepping (seen while playing)',
                    opts.morph,
                    (v) => set({ morph: v ? '1' : null }),
                  )}
                  {sw(
                    'motionChk',
                    'Motion only',
                    'Motion only: the visit’s motion map instead of its sky (static sources masked, changes above 4σ in amber)',
                    opts.motion && film !== null,
                    film !== null && filmData ? (v) => set({ mot: v ? '1' : null }) : null,
                    'Days scale only',
                  )}
                </div>
                <div class="seg sm" role="group" aria-label="Blend">
                  <button
                    class={opts.mono ? '' : 'on'}
                    onClick={() => set({ blend: null })}
                    title="Each band keeps its colour; bands are screen-blended"
                  >
                    Colour blend
                  </button>
                  <button
                    class={opts.mono ? 'on' : ''}
                    onClick={() => set({ blend: 'mono' })}
                    title="Sum the bands into one grayscale image"
                  >
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
                      set({ trim: (e.target as HTMLSelectElement).value === 'fixed' ? null : (e.target as HTMLSelectElement).value })
                    }
                    title="Trim of the bright end: a fixed white point, or the field's own 99.9th / 99.5th percentile"
                  >
                    <option value="fixed">Trim fixed</option>
                    <option value="p999">Trim 99.9 %</option>
                    <option value="p995">Trim 99.5 %</option>
                    <option value="adaptive">Trim adaptive</option>
                  </select>
                </div>
                {sw(
                  'archChk',
                  `Archive images · ${ARCHIVE.length} · 1985–2010`,
                  'Archive images: the same field in DSS2 red (1985–1999), 2MASS Ks (1997–2001) and AllWISE W1 (2010), blinked before the SPHEREx passes',
                  opts.archive,
                  film !== null ? null : (v) => set({ arch: v ? '1' : null }),
                  'Months scale only',
                )}
              </section>
            </div>
          )}
          {tab === 'obj' && (
            <div class="pbody">
              {selAn !== null && an?.result && an.result.anomalies.find((a) => a.n === selAn) ? (
                <>
                  {(() => {
                    // what it is and why it is an anomaly, first
                    const a = an.result.anomalies.find((x) => x.n === selAn)!;
                    const name = a.identified?.simbad?.name
                      ? `${a.identified.simbad.name}${a.identified.simbad.type ? ` · ${a.identified.simbad.type}` : ''}`
                      : a.identified?.gaia?.state === 'ok'
                        ? `Gaia DR3 ${a.identified.gaia.source_id}`
                        : 'not in SIMBAD or Gaia';
                    return (
                      <div class="why-card" data-testid="anomaly-why">
                        <b>
                          Anomaly {a.n} · {name}
                        </b>
                        <span>
                          score {a.score.toFixed(1)} against the field's bar {an.result.threshold?.toFixed(1) ?? '—'} (median{' '}
                          {an.result.median?.toFixed(2) ?? '—'})
                        </span>
                      </div>
                    );
                  })()}
                  {portraitOf(
                    an.result.anomalies.find((a) => a.n === selAn)!,
                    `Anomaly ${selAn}`,
                  )}
                  <Passport a={an.result.anomalies.find((a) => a.n === selAn)!} f={an.result} />
                  <ContextRows
                    ra={an.result.anomalies.find((a) => a.n === selAn)!.ra}
                    dec={an.result.anomalies.find((a) => a.n === selAn)!.dec}
                    pmra={an.result.anomalies.find((a) => a.n === selAn)!.pmra}
                    pmdec={an.result.anomalies.find((a) => a.n === selAn)!.pmdec}
                  />
                </>
              ) : (
                <>
                  {sel && portraitOf(sel, sel.name ?? 'selected source')}
                  <ObjectTab src={sel} onSpectrum={() => setTab('spec')} />
                  {sel && <ContextRows ra={sel.ra} dec={sel.dec} epoch={2000} pmra={sel.pmra ?? 0} pmdec={sel.pmdec ?? 0} />}
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
          {(tab === 'cand' || (FPX_AUTO && !fpx)) && (
            // with Find Planet X on its own the body stays mounted (hidden) so its deep sums keep running when another tab is shown
            <div class="pbody" style={tab === 'cand' ? undefined : { display: 'none' }}>
              {pairSel !== null && pairs[pairSel] && (
                <PairCard
                  pair={pairs[pairSel]!}
                  mjds={pairMjds}
                  chance={(mv as unknown as { chance_pairs?: ChancePairs } | null)?.chance_pairs ?? null}
                  onSpectrum={(pra, pdec) => {
                    setSelAn(null);
                    setSel({ ra: pra, dec: pdec });
                    setTab('spec');
                  }}
                  onBack={() => setPairSel(null)}
                />
              )}
              <div class="psh">Candidates in this field</div>
              <section class="psec fsec">
                <div class="fsh">Movers between passes</div>
                <MoversRows bare mv={mv} names={trackNames} onPick={pickTrack} />
              </section>
              <section class="psec fsec">
                <div class="fsh">Brown dwarf candidates</div>
                <div class="ssum">
                  {!dwarfs
                    ? 'waiting for the field'
                    : dwarfs.state !== 'done'
                      ? 'checking the red sources that move against Gaia and SIMBAD…'
                      : (() => {
                          // the service's own counts, so every view says the same: candidates are what the catalogues do not explain
                          const c = dwarfs.result?.counts;
                          const cand = c?.candidates ?? dwarfs.result?.candidates.length ?? 0;
                          const knownBd = c?.known_brown_dwarfs ?? 0;
                          const moving = c?.moving_red;
                          return `${cand} candidate${cand === 1 ? '' : 's'} the catalogues do not explain · ${knownBd} known brown dwarf${knownBd === 1 ? '' : 's'} recovered${moving != null ? ` · ${moving} red source${moving === 1 ? '' : 's'} moving` : ''}`;
                        })()}
                </div>
                {[
                  ...(dwarfs?.result?.candidates ?? []).map((c) => ({ c, known: false })),
                  ...(dwarfs?.result?.known ?? []).map((c) => ({ c, known: true })),
                ].map(({ c, known }, i) => (
                  <button
                    key={i}
                    class="anrow"
                    onClick={() => {
                      setSelAn(null);
                      setSel({
                        ra: c.ra_j2000 ?? c.ra ?? ra,
                        dec: c.dec_j2000 ?? c.dec ?? dec,
                        pmra: c.ra_j2000 != null ? c.pmra : undefined,
                        pmdec: c.ra_j2000 != null ? c.pmdec : undefined,
                        name: c.simbad?.name ?? (known ? 'Known red mover' : `Brown dwarf candidate ${i + 1}`),
                      });
                      setTab('obj');
                    }}
                    title="Select it: its card, portrait and spectrum"
                  >
                    <b>{known ? 'Known' : `Candidate ${i + 1}`}</b>
                    <span class="t">
                      {c.simbad?.name ?? 'not in SIMBAD'}
                      {c.pm != null ? ` · ${(c.pm / 1000).toFixed(1)}″/yr` : ''}
                      {c.colour != null ? ` · colour ${c.colour.toFixed(2)}` : ''}
                    </span>
                    <span class="faint">{c.status ?? (known ? 'known' : 'candidate')}</span>
                  </button>
                ))}
              </section>
              <section class="psec fsec">
                <div class="fsh">Pairs that change in place</div>
                {!pairs.length && (
                  <div class="ssum">{mv?.state === 'done' ? 'none in this field' : 'waiting for the search between passes'}</div>
                )}
                {pairs.length > 0 &&
                  pairs.map((pr, i) => (
                    <PairButton
                      key={i}
                      pair={pr}
                      on={pairSel === i}
                      onClick={() => {
                        setPairSel(i);
                        setTab('cand');
                      }}
                    />
                  ))}
              </section>
              <section class="psec fsec">
                <div class="fsh">Red sources</div>
                {redv?.state === 'done' && !redv.result?.red.length && <div class="ssum">none far redder than the field's stars</div>}
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
              </section>
              <section class="psec fsec">
                <div class="fsh">Spectral anomalies</div>
                {an?.result && (
                  <div class="ssum">
                    {an.result.reason ||
                      `${an.result.anomalies.length} of ${an.result.with_spectrum} spectra above ${an.result.threshold?.toFixed(2)}`}
                  </div>
                )}
                {an?.result?.anomalies.map((a) => (
                  <button key={a.n} class="anrow" onClick={() => pickAnomaly(a.n)} title={driverWords(a).join('; ')}>
                    <b>Anomaly {a.n}</b>
                    <span class="t">
                      {a.identified?.simbad?.name ??
                        (a.identified?.gaia?.state === 'ok' ? `Gaia DR3 ${a.identified.gaia.source_id}` : 'not in SIMBAD or Gaia')}
                    </span>
                    <span class="faint">score {a.score.toFixed(1)}</span>
                  </button>
                ))}
              </section>
              <section class="psec fsec">
                <div class="fsh">Asteroids{astMjd ? ` · pass of ${season(astMjd)}` : ''}</div>
                <div class="ssum">
                  {ast.state === 'done'
                    ? `${ast.tracks?.length ?? 0} track${ast.tracks?.length === 1 ? '' : 's'} · drawn on the picture in Days`
                    : ast.text || 'waiting for the field'}
                </div>
                {ast.state === 'done' &&
                  (ast.tracks ?? []).slice(0, 8).map((t, i) => (
                    <button
                      key={i}
                      class="mvrow"
                      onClick={() => {
                        selectTrack(i);
                        if (film === null) toDays();
                        setFrame(t.pts[0]?.f ?? 0);
                        setPlaying(false);
                      }}
                      title="Show this track in the Days scale (its first visit)"
                    >
                      <b>{t.known ? t.known.name : `Track ${i + 1}`}</b>
                      <span>
                        {t.rate.toFixed(1)}″/h · {t.n} visits
                      </span>
                      <span class="faint">{t.known ? 'known' : t.verified ? 'verified' : 'lead'}</span>
                    </button>
                  ))}
                {film !== null && filmBodies && (
                  <>
                    <div
                      class="sline"
                      title="Known asteroids and comets JPL predicts in this field during the pass; seen = the visit shows something at the predicted place"
                    >
                      <b>Known bodies</b>
                      <span class="sval wide">
                        {filmBodies.state === 'running'
                          ? 'asking JPL…'
                          : filmBodies.state === 'unavailable'
                            ? 'JPL not reachable'
                            : `${filmBodies.bodies.length} predicted · ${filmBodies.bodies.filter((b) => b.seen.length).length} seen`}
                      </span>
                    </div>
                    {filmBodies.bodies.some((b) => b.name.includes('3I/ATLAS')) && <NeossatCard />}
                    {[...filmBodies.bodies]
                      .sort((a, b) => (b.stackZ ?? 0) - (a.stackZ ?? 0) || (a.v ?? 99) - (b.v ?? 99))
                      .slice(0, 6)
                      .map((b) => (
                        <div key={b.name} class="mvrow body" style={{ cursor: 'default' }}>
                          <b>
                            {b.name} · V {b.v?.toFixed(1) ?? '—'}
                          </b>
                          <span class="faint">
                            seen {b.seen.length} of {b.expected.length} visits
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
                            >
                              JPL
                            </a>
                            <a target="_blank" rel="noopener" href={ssoisUrl(b.name, filmMjd)}>
                              SSOIS
                            </a>
                          </span>
                        </div>
                      ))}
                  </>
                )}
              </section>
              <section class="psec fsec" id="px-section">
                <div class="fsh">Planet X · bodies beyond Neptune</div>
                {FPX_AUTO && !fpx ? (
                  [
                    [distRef, '30–300 au · D1–D3'],
                    [distB, '30–300 au · D4'],
                    [distC, '250–2000 au · D1–D3'],
                    [distD, '250–2000 au · D4'],
                  ].map(([d, label]) =>
                    (d as typeof distRef).ds.state === 'idle' ? null : (
                      <div key={label as string}>
                        <div class="mono-s">{label as string}</div>
                        <DistantPanel d={d as typeof distRef} currentPass={shown?.pass ?? 0} passLabel={passMeta ? season(passMeta.mjd_mid) : undefined} />
                      </div>
                    ),
                  )
                ) : (
                  <DistantPanel d={dist} currentPass={shown?.pass ?? 0} passLabel={passMeta ? season(passMeta.mjd_mid) : undefined} />
                )}
                {passes.length >= 3 &&
                  (FPX_AUTO && !fpx ? (
                    <>
                      <DeepSumPanel
                        ra={ra}
                        dec={dec}
                        fov={fov}
                        passes={passes.map((q) => q.mjd_mid)}
                        autoRun={[60, 110]}
                        autoHold={fpxHold || !fpxAllOver}
                        onState={(x) => {
                          setDeepSt((o) => (o[0] === x.state ? o : [x.state, o[1]]));
                          setDeepFr((o) => (o[0] === x.frac ? o : [x.frac, o[1]]));
                        }}
                      />
                      <DeepSumPanel
                        ra={ra}
                        dec={dec}
                        fov={fov}
                        passes={passes.map((q) => q.mjd_mid)}
                        autoRun={[250, 800]}
                        autoHold={fpxHold || !fpxAllOver || !['done', 'none', 'error'].includes(deepSt[0])}
                        onState={(x) => {
                          setDeepSt((o) => (o[1] === x.state ? o : [o[0], x.state]));
                          setDeepFr((o) => (o[1] === x.frac ? o : [o[0], x.frac]));
                        }}
                      />
                    </>
                  ) : (
                    <DeepSumPanel
                      ra={ra}
                      dec={dec}
                      fov={fov}
                      passes={passes.map((q) => q.mjd_mid)}
                      autoPlan={fpx && (fpxRange.dmin ?? 0) >= 200 ? [fpxRange.dmin!, fpxRange.dmax ?? 2000] : undefined}
                    />
                  ))}
                {Object.keys(dist.keys).length >= 2 && <LinkPanel keys={Object.values(dist.keys)} />}
              </section>
            </div>
          )}
          <div class={`sbar ${trayOpen ? '' : 'tray'}`} data-testid="scans">
            <button class="tray-head" onClick={() => setTrayOpen((o) => !o)} aria-expanded={trayOpen} title={trayOpen ? 'Fold the scans' : 'Show every scan'}>
              <b>Scans</b>
              <span class="chev">{trayOpen ? '▾' : '▴'}</span>
            </button>
            {trayOpen ? (
              <div onClick={() => setTab('cand')} title="Open the candidates of this field">
                {srow(
                  'Movers',
                  !mv ? 0 : mv.state === 'done' ? 1 : mv.state === 'failed' ? 0 : null,
                  !mv
                    ? 'waiting'
                    : mv.state === 'done'
                      ? `${mvr?.tracks.length ?? 0} found`
                      : mv.state === 'failed'
                        ? 'failed'
                        : 'searching',
                  'Between passes: sources that move in a straight line over the passes (3–120 ″/yr), checked against Gaia DR3',
                  'search-movers',
                )}
                {srow(
                  'Pairs',
                  !mv ? 0 : mv.state === 'done' ? 1 : null,
                  !mv || mv.state !== 'done' ? 'waiting' : pairs.length ? `${pairs.length} found` : 'none',
                  'Two sources close together whose brightness changes in place between passes, against chance pairs',
                  'search-pairs',
                )}
                {srow(
                  'Red sources',
                  !redv ? 0 : redv.state === 'done' ? 1 : redv.state === 'failed' ? 0 : null,
                  !redv
                    ? 'waiting'
                    : redv.state === 'done'
                      ? `${redv.result!.red.length} found`
                      : redv.state === 'failed'
                        ? 'failed'
                        : 'measuring',
                  'Red sources: bright at 3.8–5 µm and faint at 1.1–1.6 µm against the field’s own stars (cold brown dwarfs and the like)',
                  'search-red',
                )}
                {srow(
                  'Spectral anomalies',
                  anFrac,
                  !an
                    ? 'waiting'
                    : an.state === 'done'
                      ? an.result?.reason
                        ? 'too few spectra'
                        : `${an.result?.anomalies.length ?? 0} found`
                      : an.state === 'running'
                        ? `${pct(anFrac) ?? 0} %`
                        : an.state === 'failed'
                          ? 'failed'
                          : 'waiting',
                  an?.result?.reason ||
                    (an?.result
                      ? `${an.result.anomalies.length} of ${an.result.with_spectrum} spectra above ${an.result.threshold?.toFixed(2)} (median ${an.result.median?.toFixed(2)})`
                      : 'Every source of the field measured in every exposure; spectra unlike the field’s ordinary star'),
                  'search-anomalies',
                )}
                {srow(
                  `Asteroids${astMjd ? ` · pass of ${season(astMjd)}` : ''}`,
                  ast.state === 'done' ? 1 : ast.state === 'none' || ast.state === 'failed' || ast.state === 'idle' ? 0 : ast.frac,
                  ast.state === 'done'
                    ? `${ast.tracks?.length ?? 0} found`
                    : ast.state === 'none'
                      ? 'too few visits'
                      : ast.state === 'failed'
                        ? 'failed'
                        : ast.state === 'idle'
                          ? 'waiting'
                          : pct(ast.frac) !== null
                            ? `${pct(ast.frac)} %`
                            : 'running',
                  ast.text ||
                    'Inside the pass shown: unknown movers hopping from visit to visit (straight tracks through the candidates of every visit, against chance)',
                  'search-asteroids',
                )}
                {srow(
                  'Brown dwarfs',
                  !dwarfs ? 0 : dwarfs.state === 'done' ? 1 : dwarfs.state === 'failed' ? 0 : null,
                  !dwarfs
                    ? 'waiting'
                    : dwarfs.state === 'done'
                      ? `${dwarfs.result?.counts?.candidates ?? dwarfs.result?.candidates.length ?? 0} found${dwarfs.result?.counts?.known_brown_dwarfs ? ` · +${dwarfs.result.counts.known_brown_dwarfs} known` : ''}`
                      : dwarfs.state === 'failed'
                        ? 'failed'
                        : 'checking',
                  'Red sources that move between the passes and that the catalogues do not explain',
                  'search-browndwarfs',
                )}
                <div class="sline sline-px" data-testid="search-px" title="Find Planet X: the visits of a pass added along trial distances and own motions (30–300 au)">
                  <b>Find Planet X</b>
                  <span class={`sprog ${pxState.frac === null ? 'busy' : ''}`}>
                    <i style={{ width: `${pxState.frac === null ? 30 : Math.max(2, pxState.frac * 100)}%` }} />
                  </span>
                  <span class="sval">{pxState.text}</span>
                  <span class="sacts">
                    {!FPX_AUTO && dist.ds.state === 'idle' && dist.available === true && (
                      <button class="btn sm mini" onClick={(e) => goPx(e, false)}>
                        Start
                      </button>
                    )}
                    {!FPX_AUTO && passes.length >= 3 && pxPlan && (
                      <button class="btn sm mini" onClick={(e) => goPx(e, true)} title="The deep sum of all passes: its plan and time are shown before it starts">
                        Deep sum
                      </button>
                    )}
                  </span>
                </div>
              </div>
            ) : (
              <div class="tray-sum" onClick={() => setTrayOpen(true)}>
                {scanItems.map(([n, v]) => (
                  <span class="it" key={n}>
                    <i class={v === null || v.endsWith('%') ? 'busy' : ''} />
                    {n} <span class="v">{v ?? '…'}</span>
                  </span>
                ))}
              </div>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
