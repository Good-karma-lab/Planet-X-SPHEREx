import { isSquareTask } from './lab/rules';
import { LoadOverlay, loadStep } from './lab/loading';
import { HereSquare, useSquareLook } from './lab/square';
import { FindStatus, rateWord, useFindRecord } from './lab/passport';
import { SearchMark, SearchRow, searchingLine, useRetry, type SearchInfo } from './lab/progress';
import type { ComponentChildren } from 'preact';
import { useEffect, useLayoutEffect, useRef, useState } from 'preact/hooks';
import { aim } from '../lib/aim';
import { FieldClient, mjdToDate, type FieldMeta, type PassMeta } from '../lib/field';
import { idleLoop } from '../lib/idle';
import { formatHash, parseHash, type Route } from '../lib/route';
import { formatDec, formatRA, parseDec, parseRA } from '../lib/sky';
import type { SxpPart } from '../lib/sxp';
import { icon } from '../ui/icons';
import { passLevels, planeLevels, starBalance } from '../viewer/balance';
import { ARCHIVE, ArchiveSet } from '../viewer/archive';
import { FADE_MS, HOLD_MS, order } from '../viewer/pendulum';
import { PER_PASS, Renderer, wallLayout, type Panel, type StackName } from '../viewer/renderer';
import { atEpoch } from '../lib/sky';
import { FRAME_MS, loadBodies, loadFilm, utc, type Film, type KnownBody } from './film';
import { NeossatCard } from './neossat';
import { LightCurves } from './lightcurve';
import { LeadPassport, type DistantState } from './search/distant';
import { ObjectGuess, ObjectTab, Passport, SpectrumTab, driverWords, type AnomalyField, type Source } from './spectrum';
import { halfToFloat } from '../lib/sxp';
import { Portrait, type PortraitData } from './portrait';
import {
  ContextRows,
  DeepSumPanel,
  DistantCanvas,
  peakRows,
  type DeepCandidate,
  type DistantCandidate,
  type DistantResult,
  peaksAbove,
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
import { useLabFlow, type LabFlow } from './lab/flow';
import { CandList, unexplainedCount, useJplState, type FindRow } from './lab/candidates';
import { SheetGrip } from '../ui/Sheet';

const SIZES = [2, 5, 10, 20, 60];
/** Where the first look lies on the picture's box: by its own grid against the field's (the same unless one snapped to a nearby
 *  centre: then it is shifted and scaled so that both skies agree). Fractions of the box, as CSS. */
export function quickPlace(
  q: { ra0: number; dec0: number; step_arcsec: number; n: number } | null,
  f: { ra0: number; dec0: number; step_arcsec: number; n: number } | null,
): Record<string, string> | undefined {
  if (!q || !f) return undefined;
  const fa = f.n * f.step_arcsec;
  const qa = q.n * q.step_arcsec;
  if (!(fa > 0 && qa > 0)) return undefined;
  const cd = Math.cos((f.dec0 * Math.PI) / 180);
  // east is left: a centre further east (larger RA) lies left of the field's
  const du = (-(((q.ra0 - f.ra0 + 540) % 360) - 180) * cd * 3600) / fa;
  const dv = (-(q.dec0 - f.dec0) * 3600) / fa;
  const s = qa / fa;
  if (Math.abs(du) < 1e-4 && Math.abs(dv) < 1e-4 && Math.abs(s - 1) < 1e-4) return undefined;
  const pc = (x: number) => `${Math.round(x * 1e5) / 1e3}%`;
  return { left: pc(0.5 + du - s / 2), top: pc(0.5 + dv - s / 2), width: pc(s), height: pc(s), inset: 'auto' };
}

/** a find opened in a wide field: the zoom shows this much around it (arcminutes across), large */
const AROUND_ARCMIN = 5;
/** the zoom's side in CSS px: 2′ small; around a find, large */
const insetSide = (w: number, around: boolean) => Math.round(around ? Math.min(420, w * 0.44) : Math.min(220, w * 0.26));
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
/** what a picture shows where its pixels have not arrived yet */
const DARK: [number, number, number] = [0, 0, 0];
/** room a wall leaves for the title chip above and the band or detector chips below, so neither covers a tile (CSS px) */
const WALL_TOP = 48;
const WALL_BOTTOM = 44;
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
/** the scale bar: the round length (arcminutes) nearest to a quarter of the picture, and its width in CSS px */
export function scaleBar(fovArcmin: number, sidePx: number): { arcmin: number; px: number; word: string } {
  const steps = [0.25, 0.5, 1, 2, 5, 10, 20];
  const want = fovArcmin / 5;
  const arcmin = steps.reduce((a, b) => (Math.abs(Math.log(b / want)) < Math.abs(Math.log(a / want)) ? b : a));
  return { arcmin, px: Math.round((sidePx * arcmin) / fovArcmin), word: arcmin < 1 ? `${Math.round(arcmin * 60)}″` : `${arcmin}′` };
}
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
  /** Known objects: a ring on every Gaia DR3 star, SIMBAD object and JPL body in the frame */
  known: boolean;
  /** People's marks (spec §5g): what people said on this field, where you have said something too */
  people: boolean;
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

const identCache = new Map<string, string | null>();

/** The catalogue name of each mover track (SIMBAD's display name, as the passport's heading has it), asked once per place: undefined while asked or when there is none. */
function useIdentNames(tracks: Track[]): (string | undefined)[] {
  const key = tracks.map((t) => `${t.ra_2025_9.toFixed(5)},${t.dec_2025_9.toFixed(5)}`).join(';');
  const [, tick] = useState(0);
  useEffect(() => {
    let stop = false;
    void (async () => {
      for (const t of tracks) {
        const k = `${t.ra_2025_9.toFixed(5)},${t.dec_2025_9.toFixed(5)}`;
        if (identCache.has(k)) continue;
        for (let n = 0; n < 6 && !stop; n++) {
          try {
            const r = await fetch(`/api/v1/context?ra=${t.ra_2025_9.toFixed(6)}&dec=${t.dec_2025_9.toFixed(6)}&epoch=2025.9&radius=6`);
            if (r.ok) {
              const c = (await r.json()) as { simbad?: { state: string; name?: string; display_name?: string } };
              if (c.simbad?.state === 'pending') {
                await new Promise((res) => setTimeout(res, 2000));
                continue;
              }
              identCache.set(k, c.simbad?.state === 'ok' ? (c.simbad.display_name ?? c.simbad.name ?? null) : null);
            }
          } catch {
            /* asked again when the tracks change */
          }
          break;
        }
        if (stop) return;
        tick((x) => x + 1);
      }
    })();
    return () => {
      stop = true;
    };
  }, [key]);
  return tracks.map((t) => identCache.get(`${t.ra_2025_9.toFixed(5)},${t.dec_2025_9.toFixed(5)}`) ?? undefined);
}

export function Lab({ route }: { route: Route }) {
  const p = route.params;
  const squareTaskRef = useRef(false);
  const ra = Number(p['ra']);
  const dec = Number(p['dec']);
  const valid = Number.isFinite(ra) && Number.isFinite(dec);
  // the nearest of the five sizes (a link with fov=18, the size a distance search reads, opens the 20′ field)
  const fovAsked = Number(p['fov']);
  const fov =
    Number.isFinite(fovAsked) && fovAsked > 0 ? SIZES.reduce((a, b) => (Math.abs(b - fovAsked) < Math.abs(a - fovAsked) ? b : a)) : 10;
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
  // a task ("Mark everything that moves and has no ring"): Known objects on, red sources and anomalies off, unless the address says
  const inTask = !!p['task'];
  const squareTask = isSquareTask(p);
  const opts: Opts = {
    reference: p['ref'] === '1',
    archive: p['arch'] === '1',
    anomalies: p['ano'] === '1',
    movers: p['movers'] !== '0',
    red: p['red'] === '1',
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
    known: p['known'] !== '0', // the rings on known objects are on unless turned off (the lookup starts by itself)
    people: inTask ? p['people'] !== '0' : p['people'] === '1',
  };
  squareTaskRef.current = squareTask;

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
          recentre();
        },
      )
      .catch(() => {});
  }, []);
  // a small field opened at a catalogue object's J2000 place (from the catalogue, the front page, a finding, another field of the lab)
  // is centred where the object is during the SPHEREx survey: a fast star has moved by arcminutes since 2000 and would be outside the
  // field (WISE 0855 moved 3.5'). On the first load (when the catalogue arrives) and on every move to another place.
  useEffect(() => {
    if (targets.current.length) recentre();
  }, [p['ra'], p['dec'], p['fov']]);
  function recentre(): void {
          const cur = parseHash(location.hash);
          const cra = Number(cur.params['ra']);
          const cdec = Number(cur.params['dec']);
          const cfov = Number(cur.params['fov'] ?? 5);
          if (cur.screen === 'lab' && Number.isFinite(cra) && Number.isFinite(cdec) && cfov <= 10) {
            for (const o of targets.current) {
              if (!(o.pmra || o.pmdec)) continue;
              const near = (a: number, b: number, c: number, e: number) =>
                Math.hypot((a - c) * Math.cos((b * Math.PI) / 180), b - e) * 3600;
              if (near(o.ra, o.dec, cra, cdec) > 6) continue;
              const [era, edec] = atEpoch(o.ra, o.dec, o.pmra ?? 0, o.pmdec ?? 0, 2025.9);
              if (near(era, edec, o.ra, o.dec) < 20) break;
              location.replace(formatHash({ screen: 'lab', params: { ...cur.params, ra: era.toFixed(5), dec: edec.toFixed(5) } }));
              break;
            }
          }
  }
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const overlay = useRef<HTMLCanvasElement>(null);
  const clientRef = useRef<FieldClient | null>(null);
  const [side, setSide] = useState(400);
  // one column instead of tabs (spec §5f): a call that used to open a tab scrolls to its section; the spectrum opens as a wide sheet
  const [specOpen, setSpecOpen] = useState(false);
  useEffect(() => setSpecOpen(false), [ra, dec, fov]);
  const colRef = useRef<HTMLDivElement>(null);
  // the panel's three tabs: Task (what to do: the task, the marks, what the searches found), Object (the selected object's passport
  // and spectrum), View (how the picture looks). A selection during a task does not take the person away from the task: the Object
  // tab shows a dot until it is opened.
  const [ptab, setPtabRaw] = useState<'task' | 'obj' | 'view'>('task');
  const [objNew, setObjNew] = useState(false);
  const setPtab = (t: 'task' | 'obj' | 'view') => {
    setPtabRaw(t);
    if (t === 'obj') setObjNew(false);
    colRef.current?.scrollTo({ top: 0 });
  };
  // a task that opens (the next one included) is shown in its tab, whatever tab was open before
  useEffect(() => {
    if (p['task']) {
      setPtabRaw('task');
      setObjNew(false);
    }
  }, [p['task']]);
  const setTab = (t: Tab) => {
    if (t === 'ctl') return;
    if (t === 'cand') return setPtab('task');
    if (t === 'obj' || t === 'spec') {
      if (p['task'] && ptab !== 'obj') return setObjNew(true);
      return setPtab('obj');
    }
  };
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
  const trackNamesRef = useRef<string[]>([]);
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
  const flowRef = useRef<LabFlow | null>(null);
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
  // the finds of the field as a file (CSV or JSON): the service's export of what the searches have found so far
  const [exportOpen, setExportOpen] = useState(false);
  const [exportNote, setExportNote] = useState('');
  const downloadExport = async (format: 'csv' | 'json') => {
    setExportOpen(false);
    try {
      const r = await fetch(`/api/v1/export/field?ra=${ra.toFixed(5)}&dec=${dec.toFixed(5)}&fov=${fov}&format=${format}`);
      if (!r.ok) {
        setExportNote(r.status === 409 ? 'Field not finished' : 'Export failed');
      } else {
        const blob = await r.blob();
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `spherex_field_${ra.toFixed(3)}_${dec.toFixed(3)}_${fov}.${format}`;
        a.click();
        setTimeout(() => URL.revokeObjectURL(a.href), 4000);
        setExportNote(r.headers.get('x-export-pending') ? 'Saved (searches still running)' : 'Saved');
      }
    } catch {
      setExportNote('Export failed');
    }
    setTimeout(() => setExportNote(''), 3000);
  };
  const ft = useFilmTracks(filmData?.key ?? null);
  const ftRef = useRef<FilmTrack[] | null>(null);
  ftRef.current = ft.state === 'ok' ? ft.tracks : null;
  const [trackSel, setTrackSel] = useState(-1);
  const [note, setNote] = useState('');
  const [wallWait, setWallWait] = useState(false);
  const [trayOpen, setTrayOpen] = useState(true);
  // the find picked in the Candidates list: it alone is bright on the picture until the picture is clicked
  const [candSel, setCandSel] = useState<string | null>(null);
  const candSelRef = useRef<string | null>(null);

  candSelRef.current = candSel;
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
  // Find Planet X reads one pass, always the same (the middle one): not the pass on show, which changes with every step of the
  // picture and would ask the service again each time
  const fpxMjd = meta?.passes[Math.floor((meta?.passes.length ?? 1) / 2)]?.mjd_mid;
  const distRef = useDistant({
    ra,
    dec,
    fov,
    pass: null,
    mjd: fpxMjd,
    ...fpxRange,
  });
  // Find Planet X on its own (FPX_AUTO): the other distance range and the other light choice run after the first, one after another
  const autoMjd = fpxMjd;
  const distB = useDistant({ ra, dec, fov, pass: null, mjd: autoMjd, dmin: 30, dmax: 300, det: '4' });
  const distC = useDistant({ ra, dec, fov, pass: null, mjd: autoMjd, dmin: 250, dmax: 2000, det: '123' });
  const distD = useDistant({ ra, dec, fov, pass: null, mjd: autoMjd, dmin: 250, dmax: 2000, det: '4' });
  const [deepSt, setDeepSt] = useState<[string, string]>(['idle', 'idle']);
  const [deepFr, setDeepFr] = useState<[number | null, number | null]>([null, null]);
  const [deepCands, setDeepCands] = useState<[DeepCandidate[] | undefined, DeepCandidate[] | undefined]>([undefined, undefined]);
  // the distance search's own map replaces the sky only when asked: a link from the Planet X page, or the switch in its section
  const [distShow, setDistShow] = useState(fpx);
  const fpxStarted = useRef('');
  useEffect(() => {
    const key = `${ra},${dec},${fov}`;
    if (!fpx || !meta || distRef.available !== true || distRef.ds.state !== 'idle' || fpxStarted.current === key) return;
    fpxStarted.current = key;
    setTab('cand');
    void distRef.start(null);
  }, [fpx, meta, distRef.available, distRef.ds.state, ra, dec, fov]);
  // a link from Findings (find=<id>): the find sits at the field's centre; once the field is open it is selected and its ring pulses on the picture
  // in a task the find is the task's object (the cabinet's and the lab's own task links name it `finding`)
  const findId = p['find'] ?? (p['task'] ? (p['finding'] ?? '') : '');
  const findShown = useRef('');
  const selectFind = () => {
    const kind = findId.split(':')[0] ?? '';
    const name =
      {
        anomaly: 'Anomaly',
        mover: 'Mover',
        track: 'Mover',
        red: 'Red source',
        pair: 'Pair',
        brown_dwarf: 'Brown dwarf',
        browndwarf: 'Brown dwarf',
        film: 'Moving object',
        peak: 'Find Planet X lead',
      }[kind] ?? 'Find';
    // the find's own place when the link gives it (the link then opens the field's own centre); else the link's place is the find's
    const fra = Number(p['find_ra']);
    const fdec = Number(p['find_dec']);
    const own = p['find_ra'] != null && p['find_dec'] != null && Number.isFinite(fra) && Number.isFinite(fdec);
    // a film find is a moving body: no variable-star verdict for it
    setSel({
      ra: own ? fra : ra,
      dec: own ? fdec : dec,
      name,
      finding: findId,
      ...(kind === 'film' ? { moving: { V: null, visits: [] } } : {}),
    });
  };
  useEffect(() => {
    const key = `${findId}|${ra},${dec},${fov}`;
    // the meta must be this field's (right after a move the old field's is still in the state)
    if (!findId || !meta || findShown.current === key || state.current.meta !== meta) return;
    findShown.current = key;
    selectFind();
  }, [findId, meta, ra, dec, fov, p['find_ra'], p['find_dec']]);
  // the find opened from Findings, as Findings has it: the first row of "To look at" (a time-lapse track is on no other list)
  // the selected find's record (its own catalogue answers): the Object tab says what its badge stands on
  const selRecord = useFindRecord(sel?.finding ?? '');
  const openFind = useFindRecord(p['find'] && !inTask && p['find'].startsWith('film:') ? p['find'] : '');
  // a find opened from Findings in a wide field: the picture says what is around the find (10′), the rest of the field dimmed, with
  // no rings or names there; "Whole field" shows everything again
  const aroundAsk = !!p['find'] && !inTask && fov >= 20;
  const [aroundOff, setAroundOff] = useState('');
  const aroundOn = aroundAsk && aroundOff !== `${findId}|${ra},${dec},${fov}`;
  const aroundRef = useRef<string | null>(null);
  aroundRef.current = aroundOn ? findId : null;
  // when the selected source changed: its ring pulses for a few seconds, so that a click in a list shows where on the picture the thing is
  const selAtRef = useRef(0);
  useEffect(() => {
    selAtRef.current = performance.now();
  }, [sel?.ra, sel?.dec, sel?.name]);
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
    loading: false,
    ready: false,
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
    // the field is still being read: the part of the picture that has not arrived is dark, not a flat grey slab (grey is for sky the survey does not cover)
    loading: phase !== '',
    // the field is finished and its pictures are in hand: until then one finished pass is held still (or nothing) and nothing plays
    ready: !!meta && phase === '',
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

  // on a phone the status line and the band buttons stand under the picture, each as tall as it is: the room under the picture follows them (no empty gap)
  useEffect(() => {
    const v = document.querySelector('.vsq') as HTMLElement | null;
    if (!v) return;
    const set = () => {
      const st = v.querySelector('.fstatus') as HTMLElement | null;
      const ch = v.querySelector('.fbands') as HTMLElement | null;
      v.style.setProperty('--below-status', `${st ? st.offsetHeight : 0}px`);
      v.style.setProperty('--below-chips', `${ch ? ch.offsetHeight : 0}px`);
    };
    set();
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(set);
    v.querySelectorAll('.fstatus, .fbands').forEach((e) => ro?.observe(e));
    return () => ro?.disconnect();
  }, [valid, view, film, time, bandsWait]);
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
    // a new field: the task's (or the link's) object is selected again once this field's meta is here, and the panel starts at its top
    findShown.current = '';
    colRef.current?.scrollTo({ top: 0 });
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
      if (squareTaskRef.current) return null; // a square has no target: the 2′ inset waits for a clicked source
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
      if (stack === 'picture') {
        const pl = planeLevels(arr.data as Uint16Array, n, PER_PASS.picture);
        r.setPictureNoise(k, pl.bg, pl.sigma);
      }
      if (stack === 'dets') {
        // every detector against the scatter measured in its own picture, so that a tile of the wall shows a source that stands 5 sigma above it
        const dl = planeLevels(arr.data as Uint16Array, n, PER_PASS.dets);
        r.setBandNoise(k, dl.bg, dl.sigma, 'dets');
      }
      if (stack === 'bands') {
        // every band against its own background and noise, for the colour blend of the picture and the tiles of the bands wall
        const bl = planeLevels(arr.data as Uint16Array, n, PER_PASS.bands);
        r.setBandNoise(k, bl.bg, bl.sigma);
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
        // the test target in the field is selected from the start (Object and Spectrum tabs show it); a click selects another source.
        // Not in a square task: a square has no object of its own (lab/rules.ts)
        if (!selRef.current && !squareTaskRef.current) {
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
        return { cx, cy, s: (aroundRef.current ? AROUND_ARCMIN * 60 : 120) / fovArc };
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
            const S = insetSide(w, !!aroundRef.current);
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
        // a field still being made: never a quick (half-built) stack; one finished pass, held still
        if (!st.ready && st.view === 'picture') stack = 'picture';
        setBandsWait((w) => (w === bandsPending ? w : bandsPending));
        const all = r.loaded(stack);
        // Difference: the reference pass shows no change, so the loop plays the passes compared with it
        const loaded0 = st.time === 'diff' && all.length > 1 && st.frame === null ? all.filter((k) => k > 0) : all;
        // Blink of two chosen passes (Tools): only those two play
        const bl = flowRef.current?.blink;
        const two = bl && st.time === 'loop' ? loaded0.filter((k) => k === bl[0] || k === bl[1]) : [];
        const loaded = two.length === 2 ? two : loaded0;
        const waiting = st.view !== 'picture' && !all.length;
        setWallWait((w) => (w === waiting ? w : waiting));
        if (loaded.length) {
          let a = loaded[0]!;
          let b = a;
          let t = 0;
          if (st.time === 'sum' || !st.ready) a = b = loaded[0]!;
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
          const lacks = (dets: number[]) =>
            passMeta0 && dets.length > 0 && dets.every((d) => !((passMeta0.detectors?.[`D${d}`] ?? 0) > 0)) ? dets : null;
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
                    noData: st.loading ? DARK : undefined,
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
                    noData: st.loading ? DARK : undefined,
                    time: timeMode,
                    diffLayer: 0,
                  },
            ];
            if (stack === 'picture' && st.opts.mono) panels[0]!.stack = r.loaded('quick').length ? 'quick' : 'picture';
            if (!st.bands.length) panels = []; // no band chosen: nothing to draw (black)
            const z = st.bands.length ? zoomOn() : null;
            if (z) {
              const S = insetSide(w, !!aroundRef.current);
              inset = { x: 10, y: 52, w: S, h: S, zoom: z };
              panels.push({ ...panels[0]!, ...inset });
            }
          } else if (st.view === 'bands') {
            // the tiles fill the whole picture area (the chips and the title float over it)
            const shownBands = BANDS.map((bd, i) => ({ bd, i })).filter((x) => st.bands.includes(x.bd.key));
            panels = wallLayout(shownBands.length, w, Math.max(80, h - WALL_TOP - WALL_BOTTOM)).map((c0, j) => {
              const c = { ...c0, y: c0.y + WALL_TOP };
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
                empty: lacks(BAND_DETS[bd.key] ?? [])
                  ? `no exposure in ${(BAND_DETS[bd.key] ?? []).map((d) => `D${d}`).join(', ')} in this season`
                  : undefined,
              };
            });
          } else {
            const shownDets = st.dets.slice().sort();
            panels = wallLayout(shownDets.length, w, Math.max(80, h - WALL_TOP - WALL_BOTTOM)).map((c0, j) => {
              const c = { ...c0, y: c0.y + WALL_TOP };
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
            // a tile with no exposure in this season is plain black under its note (nothing is drawn: no white core where there is no data at all)
            if (!q.empty) r.panel(q, a, b, t);
            if (!q.zoom) rects.push({ x: q.x, y: q.y, w: q.w, h: q.h });
            if (q.label)
              out.push({
                x: q.x,
                y: q.y + 4,
                w: q.w,
                text: q.label,
                short: q.short,
                sub: q.sub ?? '',
                hue: q.hue ?? 0,
                empty: q.empty ? { x: q.x, y: q.y, w: q.w, h: q.h, text: q.empty } : undefined,
              });
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
    };

    // ---- marks on the picture: one marker per object ----------------------------------------------------------------------
    type Mark = {
      ra: number;
      dec: number;
      kind: 'target' | 'track' | 'anomaly' | 'red' | 'body' | 'pair';
      name: string;
      lines: string[];
      colour: string;
      fill: string;
      rr: number;
      path?: [number, number][];
      /** its place at the reference epoch (the first season, in Days the first visit): where the speed rings are centred */
      ref?: [number, number];
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
      // pairs of sources that change in place: a ring (a click on it opens the pair's passport) around the middle of the two, wide enough for both
      if (st.opts.movers && !days) {
        const prs = ((mvv as unknown as { pairs?: Pair[] } | null)?.pairs ?? []) as Pair[];
        prs.forEach((pr, pi) => {
          const [c0, c1] = pr.components;
          const [u0, v0] = uvOf(c0.ra, c0.dec);
          const [u1, v1] = uvOf(c1.ra, c1.dec);
          const half = Math.hypot(u0 - u1, v0 - v1) * 0.5 * wcss || 0;
          marks.push({
            ra: (c0.ra + c1.ra) / 2,
            dec: (c0.dec + c1.dec) / 2,
            kind: 'pair',
            name: `Pair ${pi + 1}`,
            lines: [],
            colour: '#ff7ac8',
            fill: '#ffb0dc',
            rr: Math.max(13, half + 7),
            dashed: true,
          });
        });
      }
      if (st.opts.movers && mvv?.result && !days) {
        mvv.result.tracks.forEach((t, ti) => {
          const yrs = year - 2025.9;
          const decN = t.dec_2025_9 + (t.v_north * yrs) / 3600;
          const raN = t.ra_2025_9 + (t.v_east * yrs) / 3600 / Math.cos((decN * Math.PI) / 180);
          // on the picture only the rate: the catalogue match and the core's state are in the object's card
          const line = rateText(t);
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
              // the name of the identification (the same as in the lists and the passport), else its number
              name: ((n) => (n && !n.startsWith('Gaia DR3') ? n : `Mover ${ti + 1}`))(trackNamesRef.current[ti]),
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
        // only the anomalies the catalogues do not explain: a known one is Known objects' to ring
        for (const a of anv.result.anomalies.filter((q) => q.n <= ringMax && !q.identified?.simbad?.name)) {
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
          // only the red sources no catalogue names (SIMBAD, as the Candidates rows ask it)
          if (identCache.get(`${x.ra.toFixed(5)},${x.dec.toFixed(5)}`)) return;
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
        // never over the 2′ zoom (a track under it was drawn on the zoom's picture)
        ctx.save();
        if (inset) {
          const keep = new Path2D();
          keep.rect(0, 0, wcss, hcss);
          keep.rect(inset.x, inset.y, inset.w, inset.h);
          ctx.clip(keep, 'evenodd');
        }
        drawTracks(
          ctx,
          fts,
          {
            scale: wcss / F.meta.grid.n,
            k: shownVisit,
            rings: true,
            trail: true,
            selected: trackSelRef.current,
            mjd: F.meta.frames.map((f) => f.mjd),
          },
          1,
        );
        ctx.restore();
      }
      const sl = st.opts.marker ? selRef.current : null;
      // where a source with a proper motion stands at the reference epoch: the speed rings are centred there and nowhere else, so they never move
      const refMjd = days && F ? F.meta.frames[0]?.mjd : m?.passes[0]?.mjd_mid;
      const refYear = refMjd != null ? 2000 + (refMjd - 51544.5) / 365.25 : year;
      const refPlace = (q: { ra: number; dec: number; pmra?: number; pmdec?: number }): [number, number] =>
        q.pmra || q.pmdec ? (atEpoch(q.ra, q.dec, q.pmra ?? 0, q.pmdec ?? 0, refYear) as [number, number]) : [q.ra, q.dec];
      let selMark: Mark | null = null;
      const slAt: [number, number] | null = sl
        ? sl.pmra || sl.pmdec
          ? (atEpoch(sl.ra, sl.dec, sl.pmra ?? 0, sl.pmdec ?? 0, year) as [number, number])
          : [sl.ra, sl.dec]
        : null;
      if (sl && slAt) {
        const [sra, sdec] = slAt;
        // a task about this object: its own bright ring, never folded into a known star's thin one nearby
        const k = flowRef.current?.focus ? null : near(sra, sdec, 8);
        if (k) k.selected = true;
        else
          selMark = {
            ra: sra,
            dec: sdec,
            kind: 'track',
            name: '',
            lines: [],
            colour: '#ffb454',
            fill: '#ffd08a',
            rr: 13,
            selected: true,
            ref: refPlace(sl),
          };
      }
      const all = selMark ? [...marks, selMark] : marks;
      const focus = !!flowRef.current?.focus || candSelRef.current !== null;
      const placed: { x0: number; y0: number; x1: number; y1: number }[] = [];
      // no name is put on the 2′ inset (a label pushed to the picture's left edge used to land on it)
      if (inset) placed.push({ x0: inset.x - 6, y0: inset.y - 6, x1: inset.x + inset.w + 6, y1: inset.y + inset.h + 6 });
      // nor under the controls floating over the picture (the title, the field size, Mark and Ruler, the zoom's switch)
      const ovEl = overlay.current;
      if (ovEl && !days && st.view === 'picture') {
        const o = ovEl.getBoundingClientRect();
        ovEl.closest('.vsq')?.querySelectorAll<HTMLElement>('.ftitle, .factions, .toolstrip, .faround').forEach((el) => {
          const r = el.getBoundingClientRect();
          if (r.width) placed.push({ x0: r.left - o.left - 4, y0: r.top - o.top - 4, x1: r.right - o.left + 4, y1: r.bottom - o.top + 4 });
        });
      }
      let labelled = 0;
      const ring = (x: number, y: number, mk: Mark, rr: number, ticks: boolean) => {
        // a target: a thin crosshair, four 1 px arms with a gap at the centre (no ring over the star); anything else: a thin 1 px ring
        const shape = () => {
          ctx.beginPath();
          if (ticks) {
            const gap = Math.max(3, rr * 0.45),
              arm = rr + 5;
            for (const [ax, ay] of [
              [1, 0],
              [-1, 0],
              [0, 1],
              [0, -1],
            ] as const) {
              ctx.moveTo(x + ax * gap, y + ay * gap);
              ctx.lineTo(x + ax * arm, y + ay * arm);
            }
          } else ctx.arc(x, y, rr, 0, 2 * Math.PI);
          ctx.stroke();
        };
        ctx.setLineDash(mk.dashed ? [4, 3] : []);
        ctx.lineWidth = mk.selected ? 2.6 : 2.2;
        ctx.strokeStyle = 'rgba(0,0,0,0.5)';
        shape();
        ctx.lineWidth = mk.selected ? 1.4 : 1;
        ctx.strokeStyle = mk.colour;
        shape();
        ctx.setLineDash([]);
        // a just-selected mark: a ring that opens out from it and fades, repeated for about four seconds
        const age = performance.now() - selAtRef.current;
        if (mk.selected && age < 4200) {
          const ph = (age % 900) / 900;
          ctx.save();
          ctx.globalAlpha = (1 - ph) * Math.min(1, (4200 - age) / 600);
          ctx.strokeStyle = '#ffffff';
          ctx.lineWidth = 2.5;
          ctx.beginPath();
          ctx.arc(x, y, rr + 3 + ph * Math.max(8, rr * 1.6), 0, 2 * Math.PI);
          ctx.stroke();
          ctx.restore();
        }
      };
      // the status line floats over the bottom of the picture: a name is kept above it (not cut by it)
      const statusRoom = !days && st.view !== 'picture' ? 0 : 76;
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
        ly = Math.max(box.y + 2, Math.min(box.y + box.h - th - 2 - statusRoom, ly));
        // pushed back inside the picture (at its bottom edge) the label can land on another one again: it then goes above that one, and
        // when no room is left only the selected mark and the test target keep their name (the ring and the list carry the rest)
        const clash = () => placed.find((p) => lx < p.x1 && lx + tw > p.x0 && ly < p.y1 && ly + th > p.y0);
        for (let tries = 0; tries < 8; tries++) {
          const hit = clash();
          if (!hit) break;
          ly = Math.max(box.y + 2, Math.min(box.y + box.h - th - 2 - statusRoom, hit.y0 - th - 2));
        }
        if (clash() && !mk.selected && mk.kind !== 'target') return;
        placed.push({ x0: lx, y0: ly, x1: lx + tw, y1: ly + th });
        // a label pushed away from its ring (past other labels) is tied to it by a thin leader line
        {
          const nx = Math.max(lx, Math.min(x, lx + tw));
          const ny = Math.max(ly, Math.min(y, ly + th));
          const d = Math.hypot(nx - x, ny - y);
          if (d > rr + 20) {
            const ux = (nx - x) / d;
            const uy = (ny - y) / d;
            ctx.save();
            ctx.beginPath();
            ctx.moveTo(x + ux * (rr + 2), y + uy * (rr + 2));
            ctx.lineTo(nx - ux * 3, ny - uy * 3);
            ctx.lineWidth = 2.6;
            ctx.strokeStyle = 'rgba(0,0,0,0.55)';
            ctx.stroke();
            ctx.lineWidth = 1;
            ctx.strokeStyle = mk.colour;
            ctx.globalAlpha = 0.85;
            ctx.stroke();
            ctx.restore();
          }
        }
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
        // the white core of a bright star is wider than its ring: no path is drawn through it (the path starts outside, and an arrow-like line is
        // never seen crossing the core)
        const clipR = Math.max(rr + 3, mk.kind === 'target' || mk.kind === 'track' ? 24 : rr + 3);
        if (span > 2.5 * rr) {
          // a path long enough to leave the ring: the places on the seasons' dates, joined
          ctx.save();
          ctx.beginPath();
          ctx.rect(box.x, box.y, box.w, box.h);
          ctx.arc(x, y, clipR, 0, 2 * Math.PI, true);
          ctx.clip('evenodd');
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
          ctx.restore();
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
          ctx.arc(x, y, clipR, 0, 2 * Math.PI, true); // outside the ring and the white core only
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
          ctx.font = '600 11px ui-monospace, Menlo, monospace';
          for (const [yr, px, py] of pts) {
            if (Math.hypot(px - x, py - y) < clipR + 5) continue;
            if (px > x && Math.abs(py - y) < 30) continue; // where the object's own label stands
            ctx.fillStyle = mk.colour;
            ctx.beginPath();
            ctx.arc(px, py, 2.4, 0, 2 * Math.PI);
            ctx.fill();
            // the year beside its dot: to the right, else to the left, else none (never over another label: a pass's "’26" or a name)
            const tw = ctx.measureText(String(yr)).width;
            const hit = (bx: { x0: number; y0: number; x1: number; y1: number }) =>
              placed.some((q) => bx.x0 < q.x1 && bx.x1 > q.x0 && bx.y0 < q.y1 && bx.y1 > q.y0);
            const right = { x0: px + 4, y0: py - 15, x1: px + 6 + tw, y1: py - 2 };
            const left = { x0: px - 6 - tw, y0: py - 15, x1: px - 4, y1: py - 2 };
            const spot = !hit(right) ? right : !hit(left) ? left : null;
            if (!spot) continue;
            ctx.lineWidth = 3;
            ctx.strokeStyle = '#000';
            placed.push(spot);
            ctx.strokeText(String(yr), spot.x0 + 1, py - 4);
            ctx.fillStyle = '#e8f6f4';
            ctx.fillText(String(yr), spot.x0 + 1, py - 4);
          }
          ctx.restore();
        }
      };
      const wall = !days && st.view !== 'picture';
      const underInset = (x: number, y: number) =>
        (!!inset && x > inset.x - 4 && y > inset.y - 4 && x < inset.x + inset.w + 4 && y < inset.y + inset.h + 4) ||
        (!wall && y < 46) ||
        (!wall && y > hcss - 46); // nor under the title row floating over the top of the picture, nor in the band of the chips and the date at its bottom
      // around the find: the zoom's 5′ square on the picture; the rest of the field dimmed, without rings or names
      const box0 = rects[0];
      let around: { x0: number; y0: number; x1: number; y1: number } | null = null;
      if (!wall && box0 && inset && aroundRef.current && sl?.finding === aroundRef.current) {
        const cx = box0.x + inset.zoom.cx * box0.w;
        const cy = box0.y + inset.zoom.cy * box0.h;
        const hw = (inset.zoom.s * box0.w) / 2;
        around = { x0: cx - hw, y0: cy - hw, x1: cx + hw, y1: cy + hw };
      }
      const outside = (x: number, y: number) => !!around && (x < around.x0 || y < around.y0 || x > around.x1 || y > around.y1);
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
        if (around) {
          // the field outside the zoom's square dimmed (the zoom itself left as it is), a thin dashed square where the zoom looks
          ctx.save();
          const a = around;
          const dim = new Path2D();
          dim.rect(box.x, box.y, box.w, box.h);
          dim.rect(a.x0, a.y0, a.x1 - a.x0, a.y1 - a.y0);
          if (inset) dim.rect(inset.x, inset.y, inset.w, inset.h);
          ctx.fillStyle = 'rgba(2, 7, 10, 0.72)';
          ctx.fill(dim, 'evenodd');
          ctx.beginPath();
          ctx.rect(a.x0 + 0.5, a.y0 + 0.5, a.x1 - a.x0 - 1, a.y1 - a.y0 - 1);
          ctx.setLineDash([4, 5]);
          ctx.lineWidth = 1;
          ctx.strokeStyle = 'rgba(255, 208, 138, 0.7)';
          ctx.stroke();
          ctx.restore();
        }
        for (const mk of all) {
          const [u, v] = uvOf(mk.ra, mk.dec);
          const x = box.x + u * box.w;
          const y = box.y + v * box.h;
          if (x < box.x - 30 || y < box.y - 30 || x > box.x + box.w + 30 || y > box.y + box.h + 30) continue;
          // a task about one object: that object alone is bright; every other mark is a quiet outline without a name or a path
          const quiet = (focus || outside(x, y)) && !mk.selected;
          if (wall) {
            // every tile of a wall carries the same rings as the picture (movers, red sources, anomalies, known bodies, the selected one), without names
            if (mk.ghost) continue;
            if (quiet) ctx.globalAlpha = 0.35;
            ring(x, y, mk, Math.max(5, Math.min(10, box.w / 26)), mk.kind === 'target' && !quiet);
            ctx.globalAlpha = 1;
            continue;
          }
          if (underInset(x, y)) continue;
          if (quiet) {
            if (!mk.ghost) {
              ctx.globalAlpha = 0.35;
              ring(x, y, mk, Math.min(mk.rr, 9), false);
              ctx.globalAlpha = 1;
            }
            continue;
          }
          if (!mk.ghost) {
            ring(x, y, mk, mk.rr, mk.kind === 'target');
            // a ring is a thing no name is put on
            placed.push({ x0: x - mk.rr - 2, y0: y - mk.rr - 2, x1: x + mk.rr + 2, y1: y + mk.rr + 2 });
          }
          pathOf(mk, box, mk.rr, x, y);
        }
        if (!wall)
          for (const mk of all) {
            const [u, v] = uvOf(mk.ra, mk.dec);
            const x = box.x + u * box.w;
            const y = box.y + v * box.h;
            if (x < box.x || y < box.y || x > box.x + box.w || y > box.y + box.h || underInset(x, y) || mk.ghost) continue;
            if ((focus || outside(x, y)) && !mk.selected) continue;
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
          ring(x, y, { ...mk, selected: false }, Math.min(inset.w * (around ? 0.07 : 0.2), mk.rr / inset.zoom.s), false);
          ctx.restore();
        }
      }
      ctx.restore();
      // the scale bar: a round length near a fifth of the picture, at its bottom right (top right on a phone, where Mark and Ruler
      // sit at the bottom), clear of the status line and the chips
      if (!wall && box0) {
        const sb = scaleBar(fovArc / 60, box0.w);
        const phone = box0.w < 520 && hcss > box0.w * 0.8 && typeof window !== 'undefined' && window.innerWidth < 600;
        let x1 = box0.x + box0.w - 14;
        const y = phone ? 76 : hcss - 96;
        // never under Mark and Ruler: where their strip reaches down to the bar, the bar stands left of it
        const strip = overlay.current?.closest('.vsq')?.querySelector<HTMLElement>('.toolstrip');
        const ovr = overlay.current?.getBoundingClientRect();
        if (strip && ovr) {
          const r = strip.getBoundingClientRect();
          if (r.width && r.bottom - ovr.top > y - 22 && r.top - ovr.top < y + 6) x1 = Math.min(x1, r.left - ovr.left - 10);
        }
        const x0 = x1 - sb.px;
        ctx.save();
        ctx.lineCap = 'square';
        for (const [w, c] of [
          [4, 'rgba(0,0,0,0.6)'],
          [1.6, '#e6f2f6'],
        ] as const) {
          ctx.lineWidth = w;
          ctx.strokeStyle = c;
          ctx.beginPath();
          ctx.moveTo(x0, y - 4);
          ctx.lineTo(x0, y);
          ctx.lineTo(x1, y);
          ctx.lineTo(x1, y - 4);
          ctx.stroke();
        }
        ctx.font = '600 11px ui-monospace, Menlo, monospace';
        ctx.textAlign = 'center';
        ctx.lineWidth = 3;
        ctx.strokeStyle = '#000';
        ctx.strokeText(sb.word, (x0 + x1) / 2, y - 7);
        ctx.fillStyle = '#e6f2f6';
        ctx.fillText(sb.word, (x0 + x1) / 2, y - 7);
        ctx.restore();
        placed.push({ x0: x0 - 4, y0: y - 20, x1: x1 + 4, y1: y + 4 });
      }
      // the product flow's layer (Known objects, marks, the ruler): on top of the lab's own rings, at the date shown
      const fpic = !days && st.view === 'picture' ? pictures.current.get(shownPass) : undefined;
      const fnoise = noiseRef.current.get(shownPass);
      flowRef.current?.draw({
        ctx,
        rects,
        wall,
        uvOf,
        year,
        fovArc,
        placed,
        hidden: (x, y) => underInset(x, y) || outside(x, y),
        focus,
        zoom: around && inset ? { rect: inset, cx: inset.zoom.cx, cy: inset.zoom.cy, s: inset.zoom.s } : null,
        picture:
          fpic && fnoise && fpic.n === grid.n
            ? { pass: shownPass, n: fpic.n, rgb: fpic.rgb, bg: fnoise.bg, sigma: fnoise.sigma, step: grid.step_arcsec }
            : null,
      });
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
      ctx.font = '600 11px ui-monospace, Menlo, monospace';
      ctx.lineWidth = 1;
      // centred on the selected source, else the test target, else the middle of the field
      const centre = all.find((k) => k.selected) ?? all.find((k) => k.kind === 'target');
      // the rings stay where they are on the sky: centred on the place of the object at the first epoch (the first season, or the first visit in
      // Days), never on where the mark is now, so the object is seen travelling across them
      const ref: [number, number] | null = centre
        ? (centre.ref ??
          (days && centre.kind === 'target'
            ? [centre.ra, centre.dec]
            : ((centre.path?.[0] as [number, number] | undefined) ?? [centre.ra, centre.dec])))
        : null;
      for (const box of rects) {
        const [cu, cv] = ref ? uvOf(ref[0], ref[1]) : [0.5, 0.5];
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

    // every frame while the passes play; paused, the picture is drawn a few times a second until the next input (lib/idle.ts)
    const idle = idleLoop(tick, () => state.current.playing && state.current.frame === null);
    return () => {
      idle.stop();
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
    if (sel) c.want('dets_');
    return () => clearTimeout(t);
  }, [view, time, fov, meta, bands.length, opts.artifacts, !!sel]);

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

  // a search that failed is asked again by itself (lab/progress.tsx): these counters start its polling again
  const [moversTry, setMoversTry] = useState(0);
  const [dwarfTry, setDwarfTry] = useState(0);
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
  }, [ra, dec, fov, valid, meta, dwarfTry]);

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
  }, [ra, dec, fov, valid, meta, moversTry]);
  // the names of the movers in the lists come from the same identification as the passport's heading (/context at the track's place in 2025.9)
  const identNames = useIdentNames(mv?.result?.tracks ?? []);
  const trackNames = (mv?.result?.tracks ?? []).map((t, ti) => {
    for (const o of targets.current) {
      const [tra, tdec] = atEpoch(o.ra, o.dec, o.pmra ?? 0, o.pmdec ?? 0, 2025.9);
      if (Math.hypot((tra - t.ra_2025_9) * Math.cos((tdec * Math.PI) / 180), tdec - t.dec_2025_9) * 3600 < 12) return o.n;
    }
    return identNames[ti] ?? (t.gaia ? `Gaia DR3 ${t.gaia.source_id}` : '');
  });
  trackNamesRef.current = trackNames;
  // a mover's positions in every pass as an MPC ADES report: built by the service, saved as a file; what the MPC would still need is said (nothing is sent anywhere)
  const [adesNote, setAdesNote] = useState('');
  const downloadAdes = async (t: Track, i: number) => {
    setAdesNote('Building the ADES report…');
    try {
      const observations = t.mjd.map((m, k) => ({
        mjd_utc: m,
        ra: t.ra[k]!,
        dec: t.dec[k]!,
        rms_ra_arcsec: Math.max(0.05, t.rms_arcsec),
        rms_dec_arcsec: Math.max(0.05, t.rms_arcsec),
      }));
      const r = await fetch('/api/v1/export/ades', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          track_id: `SPX${i + 1}`,
          observations,
          fill_positions: true,
          // the header of our team; the file is only for download, nothing is ever sent to the MPC from here
          submitter: 'A. Ostapenko',
          measurers: ['A. Ostapenko', 'K. Karmatskikh'],
          observers: ['SPHEREx Science Team'],
          ast_cat: 'Gaia3',
          comment: 'a lead, not a discovery',
        }),
      });
      if (!r.ok) return setAdesNote(`The ADES report could not be built (${r.status}).`);
      const d = (await r.json()) as { ready: boolean; problems: string[]; notes: string[]; text: string };
      if (d.text) {
        const a = document.createElement('a');
        a.href = URL.createObjectURL(new Blob([d.text], { type: 'text/plain' }));
        a.download = `spherex_ades_SPX${i + 1}.psv`;
        a.click();
        setTimeout(() => URL.revokeObjectURL(a.href), 4000);
      }
      setAdesNote(
        d.ready
          ? 'ADES report saved: ready for the MPC.'
          : `ADES report saved, not ready to send: ${[...d.problems, ...d.notes].join('; ')}`,
      );
    } catch {
      setAdesNote('The ADES report could not be built.');
    }
  };
  const pickTrack = (t: Track, name: string) => {
    // the spectrum of a mover: measured along its own track (motion from the search, place at 2025.9)
    const years = 2025.9 - 2000;
    const dec0 = t.dec_2025_9 - (t.v_north * years) / 3600;
    const ra0 = t.ra_2025_9 - (t.v_east * years) / 3600 / Math.cos((t.dec_2025_9 * Math.PI) / 180);
    // a mover the catalogues know (the same /api/known answer as the picture's rings) is selected as that catalogue object: its
    // J2000 place and its catalogue motion, so the card never says "no catalogue gave it" about a star the picture calls known
    const known = flowRef.current?.catalogueAt(t.ra_2025_9, t.dec_2025_9, 2025.9) ?? null;
    // the track of the task's find stays that find (its record in Findings, its name), whatever the track adds
    const of = selRef.current?.finding ? { finding: selRef.current.finding } : {};
    const findName = selRef.current?.finding ? selRef.current.name : undefined;
    if (known && known.pmra != null && known.pmdec != null) {
      const cd = Math.cos((t.dec_2025_9 * Math.PI) / 180);
      const p0 = known.positions[0]!;
      const back = 2000 - p0.epoch;
      setSel({
        ra: (p0.ra + (known.pmra * back) / 3.6e6 / cd + 360) % 360,
        dec: p0.dec + (known.pmdec * back) / 3.6e6,
        name: name || known.simbad || known.name || findName || undefined,
        pmra: known.pmra,
        pmdec: known.pmdec,
        ...of,
      });
    } else
      setSel({
        ra: (ra0 + 360) % 360,
        dec: dec0,
        name: name || findName || undefined,
        pmra: t.v_east * 1000,
        pmdec: t.v_north * 1000,
        pmKind: 'measured',
        pmErr: speedErr(t) * 1000,
        ...of,
      });
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
  // the red sources of the field: a failed answer (a 5xx, a restart) is asked again a few times before the tray says failed
  const findRed = async () => {
    setRedv({ state: 'waiting', error: 'busy', result: null });
    const my = anPoll.current;
    for (let attempt = 0; attempt < 4; attempt++) {
      try {
        const r = await fetch(`/api/v1/red?ra=${ra}&dec=${dec}&fov=${fov}`, { cache: 'no-store' });
        if (r.ok) return setRedv((await r.json()) as RedView);
        if (r.status < 500 && r.status !== 429)
          return setRedv({ state: 'failed', error: `the sky service answered ${r.status}`, result: null });
      } catch {
        /* the service restarts: ask again */
      }
      await new Promise((res) => setTimeout(res, 3000 * (attempt + 1)));
      if (my !== anPoll.current) return; // another field was opened meanwhile
    }
    setRedv({ state: 'failed', error: 'the sky service did not answer', result: null });
  };
  /** The mover track that stands where a red source stands (within 4″ at pass 1): one star has one motion on the page, the track's with its uncertainty. */
  const trackOfRed = (x: RedRow): Track | undefined =>
    (mv?.result?.tracks ?? []).find((t) => {
      if (!t.ra?.length) return false;
      const cd = Math.cos((t.dec[0]! * Math.PI) / 180);
      return (
        Math.hypot((t.ra[0]! - x.ra) * cd, t.dec[0]! - x.dec) * 3600 < 4 ||
        Math.hypot((t.ra_2025_9 - x.ra) * cd, t.dec_2025_9 - x.dec) * 3600 < 4
      );
    });
  const pickRed = (k: number) => {
    const x0 = redv?.result?.red[k];
    const tr = x0 ? trackOfRed(x0) : undefined;
    if (tr) return pickTrack(tr, `Red source ${k + 1}`);
    const x = redv?.result?.red[k];
    if (!x) return;
    setSelAn(null);
    setSel(
      x.moves && x.ra_j2000 != null && x.dec_j2000 != null
        ? { ra: x.ra_j2000, dec: x.dec_j2000, pmra: x.pmra, pmdec: x.pmdec, name: `Red source ${k + 1}`, pmKind: 'measured' }
        : { ra: x.ra, dec: x.dec, name: `Red source ${k + 1}` },
    );
    setTab('obj');
  };
  const pickAnomaly = (n: number) => {
    const a = an?.result?.anomalies.find((x) => x.n === n);
    if (!a) return;
    setSel({ ra: a.ra, dec: a.dec, name: `Anomaly ${n}`, pmra: a.pmra || undefined, pmdec: a.pmdec || undefined, pmKind: 'measured' });
    setSelAn(n);
    setTab('obj');
  };

  // a track chosen in the list before the Days view of its pass is here: chosen again, by its place, once that pass's tracks are
  const pendingTrack = useRef<{ x: number; y: number } | null>(null);
  useEffect(() => {
    const want = pendingTrack.current;
    if (!want || film === null || ft.state !== 'ok' || !ft.tracks.length) return;
    pendingTrack.current = null;
    let best = -1;
    let bd = 12; // grid pixels: the same track, found again in the Days search
    ft.tracks.forEach((t, k) => {
      const m = t.pts[Math.floor(t.pts.length / 2)];
      if (!m) return;
      const d = Math.hypot(m.x - want.x, m.y - want.y);
      if (d < bd) {
        bd = d;
        best = k;
      }
    });
    if (best >= 0) selectTrack(best);
  }, [ft.state, film, filmData?.key]);
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
        // a body moving ~20′ through a pass: its place is the one of its middle visit, and the card says which
        at: ((fr) => (fr ? `at visit ${p.f + 1} of ${filmData!.meta.frames.length} · ${utc(fr.mjd)}` : undefined))(filmData?.meta.frames[p.f]),
        moving: {
          V: t.known?.V ?? null,
          visits: t.pts.map((q, n) => ({ n: n + 1, z: q.z ?? 0, how: q.how ?? 'found' })),
        },
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
  // the test targets on an archive picture, and the selected object (a task's candidate): where they were at the survey's epoch
  const drawTargets = (ctx: CanvasRenderingContext2D, size: number, epoch: number, ring: number, withPath = false) => {
    if (!meta) return;
    const scale = size / meta.grid.n;
    const xy = (ra0: number, dec0: number): [number, number] => [
      (meta.grid.n / 2 -
        ((((ra0 - meta.grid.ra0 + 540) % 360) - 180) * Math.cos((meta.grid.dec0 * Math.PI) / 180) * 3600) / meta.grid.step_arcsec) *
        scale,
      (meta.grid.n / 2 - ((dec0 - meta.grid.dec0) * 3600) / meta.grid.step_arcsec) * scale,
    ];
    const s0 = selRef.current;
    const selToo =
      s0 && !targets.current.some((o) => Math.hypot((o.ra - s0.ra) * Math.cos((o.dec * Math.PI) / 180), o.dec - s0.dec) * 3600 < 5);
    for (const o of selToo ? [...targets.current, s0] : targets.current) {
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
      // in CSS px: the canvas of the big archive frame has the screen's pixels
      const lw = Math.max(1, size / Math.max(1, side));
      ctx.lineWidth = 3.4 * lw;
      ctx.strokeStyle = 'rgba(0,0,0,0.55)';
      ctx.beginPath();
      ctx.arc(x, y, ring, 0, 2 * Math.PI);
      ctx.stroke();
      ctx.lineWidth = 1.6 * lw;
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
  }, [archTick, archStep, opts.reference, opts.trajectory, side, meta, sel]);
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
      if (state.current.film === null && !state.current.ready) return; // playback and stepping wait for the finished field
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

  // the product flow (Known objects, marks, the ruler): the dates on show are the passes, or in Days the visits of the pass
  const yearOf = (mjd: number) => 2000 + (mjd - 51544.5) / 365.25;
  const flowDays = film !== null && !!filmData;
  // a grid square or one of its quarters: its outline, its status words, the look that counts only on purpose
  const square = useSquareLook({
    params: p,
    ra,
    dec,
    fov,
    pass: film === null ? (shown?.pass ?? null) : null,
    passesTotal: meta?.passes.length ?? 0,
    ready: !!meta && phase === '' && film === null,
    open: (q) => {
      location.hash = formatHash({ screen: 'lab', params: q });
    },
  });
  const flow = useLabFlow({
    square,
    ra,
    dec,
    fov,
    grid: flowDays ? filmData.meta.grid : film === null ? (meta?.grid ?? null) : null,
    epochs: flowDays ? filmData.meta.frames.map((f) => f.mjd) : film === null ? (meta?.passes ?? []).map((q) => q.mjd_mid) : [],
    epochNow: flowDays
      ? yearOf(filmData.meta.frames[visit]?.mjd ?? filmData.meta.pass.mjd0)
      : ((q) => (q ? yearOf(q.mjd_mid) : null))(meta?.passes.find((x) => x.index === (shown?.pass ?? 0)) ?? meta?.passes[0]),
    pass: film === null ? (shown?.pass ?? 0) : null,
    single: film !== null || view === 'picture',
    side,
    known: opts.known,
    people: opts.people,
    blinkable: film === null && time === 'loop',
    sel,
    tracks: mv?.result?.tracks ?? [],
    anomalies: an?.result?.anomalies ?? [],
    set,
    setTab,
    pickAnomaly: (n) => pickAnomaly(n),
    // a task step holds one frame: a visit in Days, a pass in Months
    showFrame: (i) => {
      setPlaying(false);
      setFrame(i);
    },
    showPass: (mjd) => {
      const q = (meta?.passes ?? []).reduce<PassMeta | null>((b, x) => (!b || Math.abs(x.mjd_mid - mjd) < Math.abs(b.mjd_mid - mjd) ? x : b), null);
      if (!q) return;
      setPlaying(false);
      setFrame(q.index);
    },
    visit: film !== null ? visit : null,
    daysPass:
      film !== null && meta?.passes.length
        ? meta.passes.reduce((b, q) => (Math.abs(q.mjd_mid - film) < Math.abs(b.mjd_mid - film) ? q : b)).index
        : null,
    findIdents: selRecord && selRecord !== 'failed' && selRecord.id === sel?.finding ? (selRecord.idents ?? null) : null,
    findStatus: selRecord && selRecord !== 'failed' && selRecord.id === sel?.finding ? (selRecord.status ?? null) : null,
    visitOf: (mjd) => {
      // the visit of the pass on show nearest a date, numbered as the time line numbers it (within two hours, else none)
      const fr = filmData?.meta.frames ?? [];
      if (film === null || !fr.length) return null;
      let k = 0;
      fr.forEach((f, i) => {
        if (Math.abs(f.mjd - mjd) < Math.abs(fr[k]!.mjd - mjd)) k = i;
      });
      return Math.abs(fr[k]!.mjd - mjd) < 2 / 24 ? k + 1 : null;
    },
    nVisits: film !== null ? (filmData?.meta.frames.length ?? 0) : 0,
    // the same test as the loading card's (made later in this render: inlined here)
    ready: valid && !!meta && phase === '' && (film === null ? !!shown : !!filmData),
    pause: () => {
      // stop on the frame on show (the clock's frame, held)
      setPlaying(false);
    },
    showVisit: (mjd) => {
      const fr = filmData?.meta.frames ?? [];
      if (!fr.length) return;
      let k = 0;
      fr.forEach((f, i) => {
        if (Math.abs(f.mjd - mjd) < Math.abs(fr[k]!.mjd - mjd)) k = i;
      });
      setPlaying(false);
      setFrame(k);
    },
    pickTrack: (i) => {
      const t = mv?.result?.tracks[i];
      if (t) pickTrack(t, trackNamesRef.current[i] ?? '');
    },
    fieldKey: clientRef.current?.key ?? null,
    params: p,
    open: (q) => {
      location.hash = formatHash({ screen: 'lab', params: q });
    },
    passes: (meta?.passes ?? []).map((q) => ({ index: q.index, label: season(q.mjd_mid) })),
    thumb: () => {
      // the picture with its rings, 240 px wide
      const c = canvas.current;
      if (!c || !c.width) return null;
      try {
        const t = document.createElement('canvas');
        t.width = 240;
        t.height = Math.round((240 * c.height) / c.width);
        const g = t.getContext('2d')!;
        g.drawImage(c, 0, 0, t.width, t.height);
        if (overlay.current?.width) g.drawImage(overlay.current, 0, 0, t.width, t.height);
        return t.toDataURL('image/jpeg', 0.8);
      } catch {
        return null;
      }
    },
  });
  flowRef.current = flow;
  // for the Candidates list: whether JPL answered for the asteroid tracks of the pass, and the SIMBAD names of the red sources
  // (a named one is known)
  const jplState = useJplState(ast.filmKey);
  useIdentNames((redv?.result?.red ?? []).map((x) => ({ ra_2025_9: x.ra, dec_2025_9: x.dec }) as unknown as Track));

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
  // what a click on a pair, a known body or a peak shows in the Object tab: its own numbers (the card belongs to the selection at `key`)
  const [objCard, setObjCard] = useState<{
    key: string;
    title: string;
    rows: [string, string][];
    links?: { label: string; href: string }[];
  } | null>(null);
  const cardKey = (a: number, d: number) => `${a.toFixed(6)},${d.toFixed(6)}`;
  const openCard = (
    ra0: number,
    dec0: number,
    name: string,
    card: { title: string; rows: [string, string][]; links?: { label: string; href: string }[] },
  ) => {
    setSelAn(null);
    setSel({ ra: ra0, dec: dec0, name });
    setObjCard({ key: cardKey(ra0, dec0), ...card });
    setTab('obj');
  };
  /** In Days: a click on a known body's ring opens its card (JPL's numbers, the links to its orbit and to the images that caught it). */
  const pickBody = (e: MouseEvent) => {
    const F = filmRef.current;
    const fb = bodiesRef.current;
    if (!F || !fb) return;
    const box = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const n = F.meta.grid.n;
    const lx = e.clientX - box.left;
    const ly = e.clientY - box.top;
    let best: { b: KnownBody; d: number; ra: number; dec: number } | null = null;
    const g = F.meta.grid;
    const cd = Math.cos((g.dec0 * Math.PI) / 180);
    for (const b of fb.bodies) {
      const fr = b.frames[visit];
      if (!fr || !fr.inside) continue;
      const d = Math.hypot(((fr.x + 0.5) / n) * box.width - lx, ((fr.y + 0.5) / n) * box.height - ly);
      if (d < 18 && (!best || d < best.d))
        best = {
          b,
          d,
          ra: g.ra0 - ((fr.x + 0.5 - n / 2) * g.step_arcsec) / 3600 / cd,
          dec: g.dec0 - ((fr.y + 0.5 - n / 2) * g.step_arcsec) / 3600,
        };
    }
    if (!best) return;
    const b = best.b;
    const mjd = F.meta.frames[visit]?.mjd ?? null;
    openCard(best.ra, best.dec, b.name.replace(/\s*\(.*\)\s*$/, ''), {
      title: b.name,
      rows: [
        ['Predicted by', 'JPL Horizons, at the time of this visit'],
        ['Magnitude', b.v != null ? `V ${b.v.toFixed(1)}` : 'not given'],
        [
          'In the visits',
          b.seen.includes(visit)
            ? 'seen on this visit (the motion map shows it there)'
            : `predicted here; seen in ${b.seen.length} of the pass's visits`,
        ],
      ],
      links: [
        { label: 'JPL', href: `https://ssd.jpl.nasa.gov/tools/sbdb_lookup.html#/?sstr=${encodeURIComponent(sbdbName(b.name))}` },
        { label: 'SSOIS', href: ssoisUrl(b.name, mjd) },
      ],
    });
  };
  /** A peak of the distance search: its card with its numbers, at its place on the picture. */
  const onPeak = (c: DistantCandidate, i: number, r: DistantResult) =>
    openCard(c.ra, c.dec, `Peak ${i + 1} · ${c.d} au`, {
      title: `Find Planet X · peak ${i + 1}: a lead, not a discovery`,
      rows: peakRows(c, r).map(([k, v]) => [k, v] as [string, string]),
    });
  /** The pair's passport (from its ring on the picture or from the Candidates list): its numbers, and the passport of the component nearer `dist` (the first one without it); its own card (pictures per pass) stays in Candidates. */
  const openPair = (k: number, dist?: (c: Pair['components'][0]) => number) => {
    const pr = pairs[k];
    if (!pr) return;
    const [c0, c1] = pr.components;
    const near0 = dist && dist(c1) < dist(c0) ? c1 : c0;
    const nm = (c: typeof c0) => c.simbad?.name ?? (c.gaia?.source_id ? `Gaia DR3 ${c.gaia.source_id}` : 'unnamed');
    setPairSel(k);
    openCard(near0.ra, near0.dec, nm(near0), {
      title: `Pair: ${nm(c0)} and ${nm(c1)}`,
      rows: [
        ['Separation', `${pr.separation_arcsec.toFixed(1)}″`],
        ['Verdict', pr.verdict ?? 'a pair whose relative brightness changed between the passes: the peak seems to move, nothing does'],
        ...(pr.change_sigma != null ? ([['Largest change between passes', `${pr.change_sigma.toFixed(1)}σ`]] as [string, string][]) : []),
      ],
    });
  };
  const pick = (e: MouseEvent) => {
    const m = meta;
    if (m && film !== null) return pickBody(e);
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
    // a test target within the click's reach (12″ or 14 screen pixels) comes first, in a wall tile as on the picture: it opens the identified passport (SIMBAD, Gaia) of the star itself, not the
    // anomaly record of the same star (a wall tile is small, so its click tolerance below is wide and would otherwise catch it)
    for (const o of targets.current) {
      const [tra, tdec] = atEpoch(o.ra, o.dec, o.pmra ?? 0, o.pmdec ?? 0, year);
      if (Math.hypot((tra - cra) * Math.cos((cdec * Math.PI) / 180), tdec - cdec) * 3600 < tol) {
        setSelAn(null);
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
    // a click on a pair of sources that change in place opens the pair's card
    for (let k = 0; k < pairs.length; k++) {
      const [c0, c1] = pairs[k]!.components;
      if (sep((c0.ra + c1.ra) / 2, (c0.dec + c1.dec) / 2) < tol + (pairs[k]!.separation_arcsec ?? 0) / 2) {
        openPair(k, (c) => sep(c.ra, c.dec));
        return;
      }
    }
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
  /** Where a source is seen: its signal over the scatter of each detector's picture at its place on each season, as words ("Seen at 3.8–5 µm (D5/D6) in Nov ’25 and May ’26; faint in May ’25; invisible below 3.8 µm"). */
  const levelsOf = useRef(new WeakMap<Uint16Array, { bg: number[]; sigma: number[] }>());
  const seenText = (src: { ra: number; dec: number; pmra?: number; pmdec?: number }): string | null => {
    void portraitTick;
    if (!meta) return null;
    const LO = [0.75, 1.1, 1.6, 2.4, 3.8, 4.4];
    const HI = [1.1, 1.6, 2.4, 3.8, 4.4, 5];
    const g = meta.grid;
    const strong = new Map<string, string[]>();
    const faint: string[] = [];
    const none: string[] = [];
    let any = 0;
    let lowest = 7;
    for (const q of meta.passes) {
      const d = detsData.current.get(q.index);
      if (!d) continue;
      any++;
      const year = 2000 + (q.mjd_mid - 51544.5) / 365.25;
      const [pra, pdec] = src.pmra || src.pmdec ? atEpoch(src.ra, src.dec, src.pmra ?? 0, src.pmdec ?? 0, year) : [src.ra, src.dec];
      const gx = Math.round(g.n / 2 - ((((pra - g.ra0 + 540) % 360) - 180) * Math.cos((g.dec0 * Math.PI) / 180) * 3600) / g.step_arcsec);
      const gy = Math.round(g.n / 2 - ((pdec - g.dec0) * 3600) / g.step_arcsec);
      if (gx < 2 || gy < 2 || gx >= g.n - 2 || gy >= g.n - 2) continue;
      let lv = levelsOf.current.get(d.dets);
      if (!lv) {
        lv = planeLevels(d.dets, d.n, 6);
        levelsOf.current.set(d.dets, lv);
      }
      const N = d.n * d.n;
      const seen: number[] = [];
      let best = 0;
      for (let det = 0; det < 6; det++) {
        const sg = lv.sigma[det]!;
        if (!(sg > 0)) continue;
        // the source's own peak: the brightest pixel within 1 pixel of its place, and it counts only when it is a peak (not lower than the pixels 3 away): the wing of a
        // neighbouring star slopes up towards that star and is not the source
        let m = -1e9;
        let ring = -1e9;
        for (let dy = -3; dy <= 3; dy++)
          for (let dx = -3; dx <= 3; dx++) {
            const inner = Math.abs(dx) <= 1 && Math.abs(dy) <= 1;
            const outer = Math.abs(dx) === 3 || Math.abs(dy) === 3;
            if (!inner && !outer) continue;
            const x = gx + dx;
            const y = gy + dy;
            if (x < 0 || y < 0 || x >= d.n || y >= d.n) continue;
            const v = halfToFloat(d.dets[det * N + y * d.n + x]!);
            if (!Number.isFinite(v)) continue;
            if (inner) m = Math.max(m, (v - lv.bg[det]!) / sg);
            else ring = Math.max(ring, (v - lv.bg[det]!) / sg);
          }
        if (m < ring) m = Math.min(m, 2.9);
        best = Math.max(best, m);
        if (m >= 5) seen.push(det + 1);
      }
      const lab = season(q.mjd_mid);
      if (seen.length) {
        const key = seen.join('/');
        strong.set(key, [...(strong.get(key) ?? []), lab]);
        lowest = Math.min(lowest, seen[0]!);
      } else if (best >= 3) faint.push(lab);
      else none.push(lab);
    }
    if (!any) return null;
    const join = (a: string[]) =>
      a.length === 2 ? a.join(' and ') : a.length > 2 ? `${a.slice(0, -1).join(', ')} and ${a[a.length - 1]}` : a[0]!;
    const parts: string[] = [];
    let first = true;
    for (const [key, labs] of strong) {
      const set = key.split('/').map(Number);
      const range =
        set.length === 6
          ? 'all six detectors (0.75–5 µm)'
          : `${LO[set[0]! - 1]}–${HI[set[set.length - 1]! - 1]} µm (${set.map((x) => `D${x}`).join('/')})`;
      parts.push(`${first ? 'Seen at ' : 'at '}${range} in ${join(labs)}`);
      first = false;
    }
    if (faint.length) parts.push(`${parts.length ? 'faint' : 'Faint'} in ${join(faint)}`);
    if (!parts.length) return `No signal over 3σ at this place in ${join(none)}.`;
    if (lowest > 1 && lowest <= 6) parts.push(`invisible below ${LO[lowest - 1]} µm`);
    return `${parts.join('; ')}.`;
  };
  /** The portrait of a source at (ra, dec) [J2000 with motion] from the detector stacks of the season shown. */
  const portraitOf = (src: { ra: number; dec: number; pmra?: number; pmdec?: number }, label: string, aside?: ComponentChildren) => {
    void portraitTick;
    if (!meta) return null;
    const k = shown?.pass ?? 0;
    const d = detsData.current.get(k) ?? detsData.current.values().next().value;
    // the portrait's own space is kept while the detector pictures arrive, so that nothing below it (the scans tray) jumps when it comes
    if (!d)
      return (
        <div class="portrait">
          <div class="ph" />
          <div class="pt-key">
            <b>Portrait · {label}</b>
            <div>loading the detector pictures…</div>
          </div>
        </div>
      );
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
    return <Portrait data={data} x={gx} y={gy} label={label} aside={aside} />;
  };
  const half = fov / 120;
  const inField = targets.current.find((o) => {
    const [tra, tdec] = atEpoch(o.ra, o.dec, o.pmra ?? 0, o.pmdec ?? 0, 2025.9);
    return Math.abs(tdec - dec) < half && Math.abs(((tra - ra + 540) % 360) - 180) * Math.cos((dec * Math.PI) / 180) < half;
  });
  // a task's find before its answers: the title names no famous object of the field (a control looks like any other)
  const fieldName = inField && !flow.blind ? inField.n : 'Sky field';
  // the picture in words, for a screen reader: said again only when it stops (never every frame of the time-lapse)
  const passNo = passMeta ? passes.indexOf(passMeta) + 1 : 0;
  const pictureWords = !meta
    ? `${fieldName}: loading`
    : [
        fieldName,
        `${fov}′ field`,
        film !== null ? 'Days: the visits of one pass' : time === 'diff' ? 'Difference' : time === 'sum' ? 'Co-add of every pass' : 'Months',
        playing && time !== 'sum'
          ? `playing ${film !== null ? 'the visits' : `${passes.length} passes`}`
          : passMeta && time !== 'sum'
            ? `pass ${passNo} of ${passes.length}, ${season(passMeta.mjd_mid)}`
            : '',
        sel?.name ? `selected: ${sel.name}` : '',
      ]
        .filter(Boolean)
        .join(', ');
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
  // the line over the picture is only the date of what is shown (the time bar has the rest; the full line is its tooltip)
  // a field still being made or loaded (Months scale): one finished pass held still, dimmed, the progress in the middle; nothing plays
  // the field is made but its first pass is not on the canvas yet (a cold field: 2–8 s): the card stays, never a black picture
  const drawingFirst = valid && film === null && !!meta && phase === '' && !shown;
  const fieldLoading = (valid && film === null && !(meta && phase === '')) || drawingFirst;
  const loadNow = drawingFirst
    ? { title: 'Drawing the first pass', detail: '', frac: null }
    : fieldLoading
      ? loadStep(phase || 'asking the sky service…', pictures.current.size, meta?.passes.length ?? 0)
      : null;
  // a cold field's first look (GET /api/v1/fields/quick: the all-sky cube cut to the field's own grid), asked once per field while it
  // loads; a 502 (the bucket did not answer) keeps the usual card
  const [quick, setQuick] = useState<string | null>(null);
  // the first look's own grid (its X-Quick header): placed by it, so that a field that snapped to a nearby centre still agrees
  const [quickGrid, setQuickGrid] = useState<{ ra0: number; dec0: number; step_arcsec: number; n: number } | null>(null);
  useEffect(() => {
    setQuick(null);
    setQuickGrid(null);
    if (!valid || film !== null) return;
    let stop = false;
    let url = '';
    void fetch(`/api/v1/fields/quick?ra=${ra.toFixed(5)}&dec=${dec.toFixed(5)}&fov=${fov}`)
      .then((r) => {
        if (!r.ok || !/^image\//.test(r.headers.get('content-type') ?? '')) return null;
        try {
          const g = (JSON.parse(r.headers.get('x-quick') ?? '{}') as { grid?: { ra0: number; dec0: number; step_arcsec: number; n: number } }).grid;
          if (g && [g.ra0, g.dec0, g.step_arcsec, g.n].every(Number.isFinite)) setQuickGrid(g);
        } catch {
          // no header: drawn on the field's box as it is
        }
        return r.blob();
      })
      .then((b) => {
        if (!b || stop) return;
        url = URL.createObjectURL(b);
        setQuick(url);
      })
      .catch(() => {});
    return () => {
      stop = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [valid, ra, dec, fov, film === null]);
  const dateLabel = time === 'sum' ? 'all seasons' : passMeta ? season(passMeta.mjd_mid) : fieldLoading ? '' : phase || '';
  const daysShort = filmData ? `visit ${visit + 1} of ${nVisits} · ${utc(filmData.meta.frames[visit]?.mjd ?? 0)}` : daysStatus;
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
    busy = false,
    search: SearchInfo | null = null,
  ) => (
    <label class={`sw ${onChange ? '' : 'dis'}${busy ? ' busy' : ''}`} title={onChange ? title : `${title} (${why})`}>
      <input
        type="checkbox"
        id={id}
        aria-label={text}
        checked={checked}
        disabled={!onChange}
        onChange={(e) => onChange?.((e.target as HTMLInputElement).checked)}
      />
      <span class="swl">{text}</span>
      <span class="swr">
        {search && <SearchMark s={search} />}
        {!onChange && whyShort && <em class="why">{whyShort}</em>}
      </span>
    </label>
  );
  const pct = (f: number | null | undefined) => (f === null || f === undefined ? null : Math.max(0, Math.min(100, Math.round(f * 100))));
  // a scan that failed can be asked again from its own line
  const retryOf: Record<string, (() => void) | undefined> = {
    'Red sources': () => void findRed(),
    'Spectral anomalies': () => void followAnomalies(true),
  };
  const srow = (name: string, frac: number | null, right: string, title: string, testid?: string) => (
    <div class="sline" title={title} data-testid={testid}>
      <b>{name}</b>
      <span class={`sprog ${frac === null ? 'busy' : ''}`}>
        <i style={{ width: `${frac === null ? 30 : Math.max(2, frac * 100)}%` }} />
      </span>
      <span class="sval">
        {right}
        {right.startsWith('failed') && retryOf[name] && (
          <button
            class="btn sm mini"
            onClick={(e) => {
              e.stopPropagation();
              retryOf[name]!();
            }}
          >
            Retry
          </button>
        )}
      </span>
    </div>
  );
  const mvr = mv?.result;
  const pairs = ((mv as unknown as { pairs?: Pair[] } | null)?.pairs ?? []) as Pair[];
  const pairMjds = (mv as unknown as { mjd?: number[] } | null)?.mjd ?? passes.map((q) => q.mjd_mid);
  const anFrac = an?.state === 'done' ? 1 : an?.state === 'running' ? (an.total ? an.done / an.total : null) : 0;
  const dist = distRef;
  const pxPlan = useDistantPlan({ ra, dec, fov, pass: null, mjd: fpxMjd }, dist.available === true && valid);

  // Find Planet X on its own: after the field's other scans and behind a person's picture, one search at a time
  const fpxRuns = [distRef, distB, distC, distD];
  const scanOver = (v?: string) => v === 'done' || v === 'failed' || v === 'none';
  const othersOver =
    !!mv &&
    scanOver(mv.state) &&
    !!redv &&
    scanOver(redv.state) &&
    !!dwarfs &&
    scanOver(dwarfs.state) &&
    (!an || an.state === 'done' || an.state === 'failed' || an.state === 'idle') &&
    scanOver(ast.state);
  const pictureBusy = !meta || wallWait || (film !== null && !filmData);
  const fpxHold = !FPX_AUTO || !valid || pictureBusy || !othersOver;
  const fpxOff = fpxRuns.some((r) => r.available !== true);
  useEffect(() => {
    if (fpxHold || fpxOff || fpx) return;
    if (fpxRuns.some((r) => r.ds.state === 'loading')) return;
    // a search a person cancelled is not started again on this field
    // a person's cancel stops the whole automatic chain on this field: no later search starts after it
    if (fpxRuns.some((r) => r.closed())) return;
    const next = fpxRuns.find((r) => r.ds.state === 'idle' && !r.closed());
    if (next) void next.start(null, true);
  });
  // the leads of the field: the confirmed peaks (ok) of every search, the distance searches and the deep sums, one per object (peaks within 10″ of each other
  // are one object: the strongest is kept)
  type Lead = { ra: number; dec: number; sn: number; d: number; range: string; phot: number | null; label: string; open: () => void };
  const fpxLeads: Lead[] = (() => {
    const labels = ['30–300 au · D1–D3', '30–300 au · D4', '250–2000 au · D1–D3', '250–2000 au · D4'];
    const out: Lead[] = [];
    const add = (l: Lead) => {
      const near = out.findIndex((o) => Math.hypot((o.ra - l.ra) * Math.cos((l.dec * Math.PI) / 180), o.dec - l.dec) * 3600 < 10);
      if (near < 0) out.push(l);
      else if (l.sn > out[near]!.sn) out[near] = l;
    };
    fpxRuns.forEach((run, k) => {
      if (run.ds.state !== 'ok') return;
      const r = run.ds.r;
      r.candidates.forEach((c, i) => {
        if (c.ok)
          add({
            ra: c.ra,
            dec: c.dec,
            sn: c.sn,
            d: c.d,
            range: `${c.d_lo}–${c.d_hi}`,
            phot: c.phot_sn ?? null,
            label: labels[k]!,
            open: () => onPeak(c, i, r),
          });
      });
    });
    deepCands.forEach((cs, k) =>
      (cs ?? []).forEach((c, i) => {
        if (!c.ok) return;
        add({
          ra: c.ra,
          dec: c.dec,
          sn: c.sn,
          d: c.d,
          range: '',
          phot: c.phot_sn,
          label: `deep sum of the passes · ${k ? '250–800' : '60–110'} au`,
          open: () =>
            openCard(c.ra, c.dec, `Deep peak ${i + 1} · ${c.d} au`, {
              title: `Find Planet X · deep sum, peak ${i + 1}: a lead, not a discovery`,
              rows: [
                ['Signal', `S/N ${c.sn} in the sum of the passes · ${c.phot_sn != null ? `${c.phot_sn} σ` : 'n/a'} in photometry`],
                ['Distance', `${c.d} au`],
                ['Own motion', `${c.mu[0]}, ${c.mu[1]} ″/day (east, north)`],
                ['In each pass', c.by_pass.map((q) => (q.sn != null ? `${q.sn}σ` : '—')).join(' · ')],
                ['Control tracks', c.control_sn?.length ? `${c.control_sn.join(', ')} σ` : 'n/a'],
              ],
            }),
        });
      }),
    );
    return out;
  })();
  const fpxStopped = fpxRuns.some((r) => r.closed());
  const fpxAllOver = FPX_AUTO && (fpxStopped || fpxRuns.every((r) => (r.ds.state !== 'idle' && r.ds.state !== 'loading') || r.closed()));
  const deepOn = FPX_AUTO && passes.length >= 3;
  const pxState = (() => {
    if (FPX_AUTO && !fpx) {
      if (fpxOff) return { frac: 0, text: 'not offered' };
      const parts = [
        ...fpxRuns.map((r) => (r.ds.state === 'ok' ? 1 : r.ds.state === 'loading' ? (r.ds.frac ?? 0) : r.ds.state === 'idle' ? 0 : 1)),
        ...(deepOn ? deepFr.map((f) => f ?? (0 as number)) : []),
      ];
      const frac = parts.reduce((a, b) => a + b, 0) / parts.length;
      const active =
        fpxRuns.some((r) => r.ds.state === 'loading') || deepSt.some((x) => x === 'planning' || x === 'plan' || x === 'running');
      const finished = fpxAllOver && (!deepOn || deepSt.every((x) => x === 'done' || x === 'none' || x === 'error'));
      if (finished) {
        const n = fpxLeads.length;
        return { frac: 1, text: `${n} found` };
      }
      if (!active && fpxHold) return { frac: 0, text: 'waiting for the other scans' };
      return { frac, text: `${Math.round(frac * 100)} %` };
    }
    const ds = dist.ds;
    if (dist.available !== true) return { frac: 0, text: 'not offered' };
    if (ds.state === 'idle')
      return {
        frac: 0,
        text: pxPlan
          ? pxPlan.estimate_s < 90
            ? '~1 min'
            : pxPlan.estimate_s < 5400
              ? `~${Math.round(pxPlan.estimate_s / 60)} min`
              : `~${(pxPlan.estimate_s / 3600).toFixed(1)} h`
          : 'ready',
      };
    if (ds.state === 'loading') return { frac: ds.frac, text: ds.frac != null ? `${Math.round(ds.frac * 100)} %` : 'running' };
    if (ds.state === 'ok') return { frac: 1, text: `${peaksAbove(ds)} found` };
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
    ...([
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
        an?.state === 'done' ? String(an.result?.anomalies.length ?? 0) : an?.state === 'running' ? `${pct(anFrac) ?? 0}%` : null,
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
    ] as readonly (readonly [string, string | null])[]),
    [
      'Planet X',
      FPX_AUTO && !fpx
        ? (pxState.frac ?? 0) >= 1
          ? pxState.text.replace(' found', '')
          : pxState.text.endsWith('%')
            ? pxState.text
            : null
        : dist.ds.state === 'ok'
          ? String(peaksAbove(dist.ds))
          : dist.ds.state === 'loading'
            ? `${Math.round((dist.ds.frac ?? 0) * 100)}%`
            : null,
    ],
  ];
  // ---- the searches of the field, one source for the switches, the row at the top of Candidates and the status line
  const fieldId = `${ra},${dec},${fov}`;
  const moversRetry = useRetry(mv?.state === 'failed', () => setMoversTry((x) => x + 1), fieldId);
  const dwarfRetry = useRetry(dwarfs?.state === 'failed', () => setDwarfTry((x) => x + 1), fieldId);
  const redRetry = useRetry(redv?.state === 'failed', () => void findRed(), fieldId);
  const anRetry = useRetry(an?.state === 'failed', () => void followAnomalies(true), fieldId);
  const stOf = (state: string | undefined, started: boolean): SearchInfo['state'] =>
    !started ? 'idle' : state === 'done' ? 'done' : state === 'failed' ? 'failed' : 'running';
  // ---- the finds of the field as one list (Candidates tab): unexplained first; known ones never count
  const yearNow = passMeta ? 2000 + (passMeta.mjd_mid - 51544.5) / 365.25 : 2025.9;
  const uvNow = (pra: number, pdec: number): [number, number] | null => {
    const g = meta?.grid;
    if (!g) return null;
    const fa = g.n * g.step_arcsec;
    const u = 0.5 - ((((pra - g.ra0 + 540) % 360) - 180) * Math.cos((g.dec0 * Math.PI) / 180) * 3600) / fa;
    const v = 0.5 - ((pdec - g.dec0) * 3600) / fa;
    return u >= 0 && v >= 0 && u <= 1 && v <= 1 ? [u, v] : null;
  };
  const placeWord = (pra: number, pdec: number) => `${formatRA(pra)} ${formatDec(pdec)}`;
  /** a place's SIMBAD name as already asked: a name, null (none), or undefined (being asked) */
  const identOf = (pra: number, pdec: number): string | null | undefined => {
    const k = `${pra.toFixed(5)},${pdec.toFixed(5)}`;
    return identCache.has(k) ? identCache.get(k) : undefined;
  };
  const rowPick = (key: string, f: () => void) => () => {
    setCandSel(key);
    f();
  };
  const findRowsAll: FindRow[] = [
    ...(openFind && openFind !== 'failed'
      ? [
          ((f): FindRow => {
            const fra = Number(p['find_ra'] ?? ra);
            const fdec = Number(p['find_dec'] ?? dec);
            return {
              key: `find:${f.id}`,
              name: `Moving object · ${rateWord(f) || 'one pass'}`,
              line: ['from Findings', f.snr != null ? `S/N ${f.snr.toFixed(1)}` : '', 'moves within one pass'].filter(Boolean).join(' · '),
              status: f.status === 'unexplained' ? 'unexplained' : f.status === 'known' ? 'known' : 'checking',
              uv: uvNow(fra, fdec),
              pick: rowPick(`find:${f.id}`, () => {
                selectFind();
                setAroundOff('');
              }),
            };
          })(openFind),
        ]
      : []),
    ...(mvr?.tracks ?? []).map((t, i): FindRow => {
      const nm = trackNames[i] || t.name || '';
      const named = !!nm && !nm.startsWith('Gaia DR3');
      const yrs = yearNow - 2025.9;
      const dnow = t.dec_2025_9 + (t.v_north * yrs) / 3600;
      return {
        key: `mover:${i}`,
        ra: t.ra_2025_9,
        dec: t.dec_2025_9,
        name: nm || placeWord(t.ra_2025_9, t.dec_2025_9),
        line: `moves ${rateText(t)} toward ${t.pa.toFixed(0)}° · ${t.passes.length} passes`,
        status: t.check === 'same' || named ? 'known' : t.check === 'pending' ? 'checking' : 'unexplained',
        uv: uvNow(t.ra_2025_9 + (t.v_east * yrs) / 3600 / Math.cos((dnow * Math.PI) / 180), dnow),
        pick: rowPick(`mover:${i}`, () => pickTrack(t, trackNames[i] ?? '')),
      };
    }),
    ...[
      ...(dwarfs?.result?.candidates ?? []).map((c) => ({ c, known: false })),
      ...(dwarfs?.result?.known ?? []).map((c) => ({ c, known: true })),
    ].map(({ c, known }, i): FindRow => {
      const pra = c.ra_j2000 ?? c.ra ?? ra;
      const pdec = c.dec_j2000 ?? c.dec ?? dec;
      const now = c.ra_j2000 != null ? atEpoch(pra, pdec, c.pmra ?? 0, c.pmdec ?? 0, yearNow) : ([pra, pdec] as [number, number]);
      return {
        key: `bd:${i}`,
        name: c.simbad?.name ?? placeWord(now[0], now[1]),
        line: `red and moving${c.pm != null ? ` ${(c.pm / 1000).toFixed(1)}″/yr` : ''}: a brown dwarf ${known ? 'of the catalogues' : 'candidate'}`,
        status: known ? 'known' : 'unexplained',
        uv: uvNow(now[0], now[1]),
        pick: rowPick(`bd:${i}`, () => {
          setSelAn(null);
          setSel({
            ra: pra,
            dec: pdec,
            pmra: c.ra_j2000 != null ? c.pmra : undefined,
            pmdec: c.ra_j2000 != null ? c.pmdec : undefined,
            name: c.simbad?.name ?? (known ? 'Known red mover' : `Brown dwarf candidate ${i + 1}`),
          });
          setTab('obj');
        }),
      };
    }),
    ...pairs.map((pr, i): FindRow => {
      const [c0, c1] = pr.components;
      return {
        key: `pair:${i}`,
        name: placeWord((c0.ra + c1.ra) / 2, (c0.dec + c1.dec) / 2),
        line: `two sources ${pr.separation_arcsec.toFixed(1)}″ apart whose brightness changes between the passes`,
        status: 'unexplained',
        uv: uvNow((c0.ra + c1.ra) / 2, (c0.dec + c1.dec) / 2),
        pick: rowPick(`pair:${i}`, () => openPair(i)),
      };
    }),
    ...(redv?.result?.red ?? []).map((x, k): FindRow => {
      const tr = trackOfRed(x);
      const id = identOf(x.ra, x.dec);
      const times = redv?.result?.median_colour
        ? `${x.lower_limit ? 'over ' : ''}${(x.colour / redv.result.median_colour).toFixed(1)}× redder than the field's stars`
        : 'red';
      return {
        key: `red:${k}`,
        name: id || placeWord(x.ra, x.dec),
        line: `${times}${tr ? ` · moves ${rateText(tr)}` : ''}`,
        status: id ? 'known' : id === undefined ? 'checking' : 'unexplained',
        uv: uvNow(x.ra, x.dec),
        group: 'red',
        ra: x.ra,
        dec: x.dec,
        pick: rowPick(`red:${k}`, () => pickRed(k)),
      };
    }),
    ...(an?.result?.anomalies ?? []).map((a): FindRow => {
      const now = atEpoch(a.ra, a.dec, a.pmra, a.pmdec, yearNow);
      const nm = a.identified?.simbad?.name;
      return {
        key: `anomaly:${a.n}`,
        name: nm ?? (a.identified?.gaia?.state === 'ok' ? `Gaia DR3 ${a.identified.gaia.source_id}` : placeWord(now[0], now[1])),
        line: `spectrum unlike the field's stars: ${driverWords(a)[0] ?? `score ${a.score.toFixed(1)}`}`,
        status: nm ? 'known' : 'unexplained',
        uv: uvNow(now[0], now[1]),
        group: 'anomaly',
        ra: a.ra,
        dec: a.dec,
        pick: rowPick(`anomaly:${a.n}`, () => pickAnomaly(a.n)),
      };
    }),
    ...(ast.state === 'done' ? (ast.tracks ?? []) : []).slice(0, 8).map((t, i): FindRow => ({
      key: `ast:${i}`,
      name: t.known ? t.known.name : `Track of ${astMjd ? season(astMjd) : 'one pass'}`,
      line: `crosses the field at ${t.rate.toFixed(1)}″/h · ${t.n} visits of one pass`,
      // without JPL's answer a track is not a lead: it may be a known asteroid nobody could name
      status: t.known ? 'known' : jplState === 'unavailable' ? 'jpl' : jplState === 'asking' ? 'checking' : 'unexplained',
      // its stamp: the place of its middle visit, cut from the picture on show (the film's grid is the field's)
      uv: ((q) => {
        const n = filmData?.meta.grid.n ?? meta?.grid.n;
        return q && n ? ([(q.x + 0.5) / n, (q.y + 0.5) / n] as [number, number]) : null;
      })(t.pts[t.pts.length >> 1]),
      path: ((n) => (n ? t.pts.map((q) => [(q.x + 0.5) / n, (q.y + 0.5) / n] as [number, number]) : undefined))(filmData?.meta.grid.n ?? meta?.grid.n),
      pick: rowPick(`ast:${i}`, () => {
        // the list's track is the asteroid search's (Months): the Days view's own tracks are matched to it by place once they are here
        const mid = t.pts[t.pts.length >> 1];
        pendingTrack.current = mid ? { x: mid.x, y: mid.y } : null;
        if (film !== null && ft.state === 'ok') {
          const k = ft.tracks.findIndex((q) => {
            const m = q.pts[q.pts.length >> 1];
            return !!m && !!mid && Math.hypot(m.x - mid.x, m.y - mid.y) < 12;
          });
          if (k >= 0) {
            pendingTrack.current = null;
            selectTrack(k);
          }
        }
        // Days at the track's own pass (the one the search ran on), not at the pass on show
        if (film === null) {
          setFrame(null);
          set({ film: astMjd ? astMjd.toFixed(1) : passMjd ? passMjd.toFixed(1) : '0', view: null });
        }
        setFrame(t.pts[0]?.f ?? 0);
        setPlaying(false);
      }),
    })),
    ...(FPX_AUTO && !fpx ? fpxLeads : []).map((l, i): FindRow => ({
      key: `px:${i}`,
      name: placeWord(l.ra, l.dec),
      line: `Find Planet X peak · S/N ${l.sn} · ${l.d} au · photometry ${l.phot != null ? `${l.phot}σ` : 'n/a'}`,
      status: 'unexplained',
      uv: uvNow(l.ra, l.dec),
      pick: rowPick(`px:${i}`, l.open),
    })),
  ];
  // a find people set aside (two agreed it is an artefact) leaves the list, as it leaves Findings
  // a task's find before its answers: its own row in the list says nothing of what it is (a control object looks like any other)
  const blindUv = flow.blind && sel ? uvNow(...((sel.pmra || sel.pmdec ? atEpoch(sel.ra, sel.dec, sel.pmra ?? 0, sel.pmdec ?? 0, yearNow) : [sel.ra, sel.dec]) as [number, number])) : null;
  const blindTol = meta ? 12 / (meta.grid.n * meta.grid.step_arcsec) : 0;
  const findRows = findRowsAll
    .filter((r) => r.ra == null || r.dec == null || !flow.asideAt(r.ra, r.dec))
    .map((r) =>
      blindUv && r.uv && Math.abs(r.uv[0] - blindUv[0]) < blindTol && Math.abs(r.uv[1] - blindUv[1]) < blindTol
        ? { ...r, name: 'The object in question', line: 'what it is: after your answers', status: 'checking' as const }
        : r,
    );
  const foundCount = unexplainedCount(findRows);
  const unexplainedOf = (g: 'red' | 'anomaly') => findRows.filter((r) => r.group === g && r.status === 'unexplained').length;
  const fieldOn = valid && !!meta;
  const searches: SearchInfo[] = [
    {
      name: 'movers',
      state: stOf(mv?.state, fieldOn),
      count: String(mvr?.tracks.length ?? 0),
      ours: true,
      retry: mv?.state === 'failed' ? moversRetry : null,
    },
    {
      name: 'brown dwarfs',
      state: stOf(dwarfs?.state, fieldOn && !!dwarfs),
      count: String(dwarfs?.result?.counts?.candidates ?? dwarfs?.result?.candidates.length ?? 0),
      ours: true,
      retry: dwarfs?.state === 'failed' ? dwarfRetry : null,
    },
    { name: 'red sources', state: stOf(redv?.state, fieldOn && !!redv), count: String(unexplainedOf('red')), ours: true, retry: redv?.state === 'failed' ? redRetry : null },
    {
      name: 'anomalies',
      state: stOf(an?.state === 'idle' ? 'running' : an?.state, fieldOn && !!an),
      frac: an?.state === 'running' ? (anFrac ?? null) : null,
      count: String(unexplainedOf('anomaly')),
      ours: true,
      retry: an?.state === 'failed' ? anRetry : null,
    },
    {
      name: 'asteroids',
      state:
        ast.state === 'idle'
          ? 'idle'
          : ast.state === 'done' || ast.state === 'none'
            ? 'done'
            : ast.state === 'failed'
              ? 'failed'
              : 'running',
      frac: ast.frac,
      count: String(ast.tracks?.length ?? 0),
      ours: true,
    },
    ...(FPX_AUTO && !fpx
      ? [
          {
            name: 'Planet X',
            state: (fpxAllOver ? 'done' : fieldOn ? 'running' : 'idle') as SearchInfo['state'],
            frac: pxState.frac != null && pxState.frac < 1 ? pxState.frac : null,
            count: String(fpxLeads.length),
            ours: true,
          },
        ]
      : []),
    flow.knownSearch,
  ];
  const searchLine = searchingLine(searches.filter((x) => x.name !== 'known objects'));
  const searchOf = (name: string) => searches.find((x) => x.name === name) ?? null;
  // the Machine finds switch: movers and brown dwarfs together
  const machineSearch: SearchInfo | null = (() => {
    const m = searchOf('movers');
    const d = searchOf('brown dwarfs');
    if (!m) return null;
    const run = m.state === 'running' || d?.state === 'running';
    const fail = m.state === 'failed' ? m : d?.state === 'failed' ? d : null;
    return (
      fail ?? { name: 'machine finds', state: run ? 'running' : m.state, count: String((mvr?.tracks.length ?? 0) + Number(d?.count ?? 0)) }
    );
  })();

  // the picture's status line: a note, a wait, the date of what is shown (the flow's own words follow it)
  const mainStatus: string =
    note ||
    (bandsWait
      ? 'loading the pictures of the chosen bands…'
      : film === null && view !== 'det' && !bands.length
        ? 'No band chosen: switch a band on (or ALL) to see the sky'
        : film === null && view === 'det' && !dets.length
          ? 'No detector chosen: switch one on (or ALL)'
          : film !== null
            ? daysShort
            : dateLabel);

  return (
    <div class="screen">
      <div class={`lab${fieldLoading ? ' loading' : ''}`}>
        <div class="vwrap" ref={wrap}>
          <div class="vstage" ref={stageRef}>
            <div
              class={`vsq ${side < 760 ? 'compact' : ''}${view !== 'picture' && film === null ? ' wallv' : ''}`}
              data-tool={flow.tool ?? undefined}
              style={{
                // a picture is one square in every month and band (the frame never moves or grows); a wall of tiles takes the whole stage, which it fills with the largest squares
                width: `${wallView && stage.w >= 561 ? stage.w : side}px`,
                height: `${wallView && stage.w >= 561 ? stage.h : side}px`,
                // whole pixels, so that the 1 px frame is crisp and never one pixel thick in one state and two in another
                marginLeft: wallView && stage.w >= 561 ? '0px' : `${Math.max(0, Math.floor((stage.w - side) / 2))}px`,
                // a phone: the picture sits at the top of its stage (centred, it left a band of empty space above it)
                marginTop: stage.w < 560 || wallView ? '0px' : `${Math.max(0, Math.floor((stage.h - side) / 2))}px`,
                cursor: 'crosshair',
              }}
              onClick={(e) => {
                if (dragged.current) {
                  dragged.current = false;
                  return;
                }
                if ((e.target as HTMLElement).closest('.float')) return;
                setCandSel(null); // a click on the picture: every ring is on show again
                {
                  // a mark put or opened, a ruler point: the click is the flow's, not a pick
                  const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
                  if (flow.click(e.clientX - r.left, e.clientY - r.top)) return;
                }
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
              onPointerLeave={() => flow.pointer(null)}
              onPointerDown={(e) => {
                if (film !== null || view !== 'picture' || !meta || flow.tool || (e.target as HTMLElement).closest('.float')) return;
                const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
                dragFrom.current = { x: e.clientX - r.left, y: e.clientY - r.top };
              }}
              onPointerMove={(e) => {
                const f = dragFrom.current;
                const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
                flow.pointer((e.target as HTMLElement).closest('.float') ? null : { x: e.clientX - r.left, y: e.clientY - r.top });
                if (!f) return;
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
                aria-label={pictureWords}
                data-testid="picture"
              />
              <canvas ref={overlay} class="ov" />
              {quick && loadNow && view === 'picture' && (
                // the field's first look while it is built: the SPHEREx all-sky map cut to this very grid, dimmed, still
                <img
                  class="quickframe"
                  src={quick}
                  alt="SPHEREx all-sky map of this field: a first look while the field is built"
                  style={quickPlace(quickGrid, meta?.grid ?? null)}
                  data-testid="quick-frame"
                />
              )}
              <LoadOverlay step={loadNow} field={`${ra},${dec},${fov}`} firstLook={!!quick && !!loadNow && view === 'picture'} />
              {flow.floats}
              {labels.map((l) =>
                l.empty ? (
                  <div
                    class={`panel-empty${l.w < 200 ? ' small' : ''}`}
                    style={{ left: `${l.empty.x}px`, top: `${l.empty.y}px`, width: `${l.empty.w}px`, height: `${l.empty.h}px` }}
                  >
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
                  <span>{aroundOn ? `${AROUND_ARCMIN}′ around the find` : '2′ zoom'}</span>
                </div>
              )}
              {aroundAsk && view === 'picture' && film === null && insetBox && !(archStep !== null && archStep < ARCHIVE.length) && (
                <div
                  class="float faround"
                  role="group"
                  aria-label="What the picture shows"
                  style={{ left: `${insetBox.x}px`, top: `${insetBox.y + insetBox.h + 8}px` }}
                >
                  <button type="button" class={aroundOn ? 'on' : ''} aria-pressed={aroundOn} onClick={() => setAroundOff('')}>
                    Around the find
                  </button>
                  <button
                    type="button"
                    class={aroundOn ? '' : 'on'}
                    aria-pressed={!aroundOn}
                    onClick={() => setAroundOff(`${findId}|${ra},${dec},${fov}`)}
                  >
                    Whole field
                  </button>
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
              {distShow && dist.ds.state === 'ok' && film === null && view === 'picture' && <DistantCanvas d={dist} />}
              <div
                class="float ftitle"
                data-testid="field-title"
                role="button"
                tabIndex={0}
                title="Place: type another RA and Dec"
                onClick={() => {
                  // the place is typed in the View tab
                  setPtab('view');
                  setTimeout(() => {
                    colRef.current?.querySelector('#sec-place')?.scrollIntoView({ block: 'end', behavior: 'smooth' });
                    (document.getElementById('raIn') as HTMLInputElement | null)?.focus();
                  }, 120);
                }}
              >
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
              {time === 'diff' && film === null && view === 'picture' && (
                // what Difference draws, as a key on the picture (the status line is often too long to show it)
                <div
                  class="float fdkey"
                  data-testid="diff-key"
                  style={{ top: `${insetBox ? insetBox.y + insetBox.h + (aroundAsk ? 44 : 8) : 52}px` }}
                  title="A star that moved leaves a pair: bright where it is now, dark where it was"
                >
                  <i style={{ background: 'var(--magenta)' }} /> brighter <i style={{ background: 'var(--cyan)' }} /> fainter · side by side: it moved
                </div>
              )}
              <div class="sr-only" aria-live="polite" data-testid="picture-words">
                {pictureWords}
              </div>
              <div class="float fstatus" data-testid="lab-status" title={film !== null ? daysStatus : status}>
                {mainStatus}
                {!note && flow.status && (
                  <span data-testid="flow-status">
                    {mainStatus ? ' · ' : ''}
                    {flow.status}
                  </span>
                )}
                {!note && searchLine && (
                  <span data-testid="search-status">
                    {mainStatus || flow.status ? ' · ' : ''}
                    {searchLine}
                  </span>
                )}
                {flow.chip}
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
            {flow.blinkPick}
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
                        class={`${frameIndex === q.index && time !== 'sum' ? 'on' : ''}${i === 0 && passes.length > 1 ? ' restart' : ''}`}
                        title={i === 0 && passes.length > 1 ? 'The loop starts here and comes back here' : undefined}
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
            <details class="acts" data-testid="bar-more">
              <summary class="mini" title="Link, export, video" aria-label="More: link, export, video">
                <span aria-hidden="true">⋯</span>
                {(copied || exportNote || recording) && <span class="t">{copied || exportNote || 'Recording…'}</span>}
              </summary>
              <span class="acts-menu">
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
                <span class="al">Copy link</span>
              </button>
              <span class="expwrap">
                <button
                  class="mini"
                  disabled={!passes.length}
                  onClick={() => setExportOpen((o) => !o)}
                  title="Take away what the searches found in this field: movers, pairs, brown dwarfs, red sources, anomalies"
                  aria-label="Export the field's finds"
                  aria-expanded={exportOpen}
                  data-testid="export"
                >
                  <span dangerouslySetInnerHTML={{ __html: icon('archive', 14) }} style={{ display: 'contents' }} />
                  <span class="al">Export finds</span>
                </button>
                {exportOpen && (
                  <span class="expmenu" role="menu">
                    <button role="menuitem" onClick={() => void downloadExport('csv')}>
                      CSV
                    </button>
                    <button role="menuitem" onClick={() => void downloadExport('json')}>
                      JSON
                    </button>
                  </span>
                )}
              </span>
              <button
                class="mini"
                disabled={recording || film !== null || !passes.length}
                onClick={() => void recordVideo()}
                title="Download one full cycle of the seasons as a video (WebM)"
                aria-label="Download video"
              >
                <span dangerouslySetInnerHTML={{ __html: icon('download-simple', 14) }} style={{ display: 'contents' }} />
                <span class="al">Video (WebM)</span>
              </button>
              </span>
            </details>
          </div>
        </div>
        <aside class="panel">
          {/* phones and upright tablets: the panel is a bottom sheet pulled up by this grabber (not displayed on a desktop) */}
          {/* at rest the free lab shows its tab bar, the same on every tab (a tab's own first line made the picture jump) */}
          <SheetGrip peek=".taskpanel, .sqlook, .ptabs" under=".lab .tbar" label="Lab panel" />
          <div class="ptabs" role="tablist" aria-label="Panel" data-testid="panel-tabs">
            {(
              [
                ['task', 'Task'], // one name, task or not: it never changes under the hand
                ['obj', 'Object'],
                ['view', 'View'],
              ] as const
            ).map(([k, name]) => (
              <button key={k} role="tab" aria-selected={ptab === k} class={ptab === k ? 'on' : ''} onClick={() => setPtab(k)} data-testid={`ptab-${k}`}>
                {name}
                {k === 'task' && foundCount > 0 && <span class="ptab-n">{foundCount}</span>}
                {k === 'obj' && objNew && <i class="ptab-dot" title="Something new is selected" />}
              </button>
            ))}
          </div>
          {ptab === 'task' && flow.taskPanel}
          {ptab === 'task' && square.button}
          <div class="pbody pcol" ref={colRef}>
            {ptab === 'task' && !inTask && !square.cell && <HereSquare ra={ra} dec={dec} open={(q) => (location.hash = formatHash({ screen: 'lab', params: q }))} />}
            {ptab === 'task' && flow.markOpen && (
              <section class="psec" id="sec-mark" data-testid="sec-mark">
                <div class="psh">Mark</div>
                {flow.card}
              </section>
            )}
            <section class="psec" id="sec-look" data-testid="sec-look" hidden={ptab !== 'task'}>
              <div class="psh">
                To look at{foundCount > 0 && <span class="tbadge" data-testid="look-count">{foundCount}</span>}
              </div>
              {!inTask && (
                <button
                  class="mini look-missed"
                  onClick={() => flow.setTool('mark')}
                  title="Put a mark on something that moves or looks odd and has no ring"
                  data-testid="look-missed"
                >
                  Mark something the machine missed
                  <small class="tp-path">mark it · say what it is · the checks run</small>
                </button>
              )}
              <SearchRow list={searches} />
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
                <CandList
                  rows={findRows}
                  busy={searches.some((q) => q.state === 'running')}
                  also=""
                  selected={candSel}
                  picture={() => canvas.current}
                  field={{ ra, dec, fov }}
                  onTask={(kind, findId, r) =>
                    // the task of this very source: its find at its own place in the field on show (the lab selects it there)
                    set({
                      task: kind,
                      finding: findId,
                      find: findId,
                      find_ra: (r.ra ?? ra).toFixed(5),
                      find_dec: (r.dec ?? dec).toFixed(5),
                      kind: null,
                      cell: null,
                    })
                  }
                  details={
                    <>
                <section class="psec fsec">
                  <div class="fsh">Movers between passes</div>
                  <MoversRows bare mv={mv} names={trackNames} onPick={pickTrack} />
                  {(mv?.result?.tracks ?? [])
                    .filter((t) => t.mjd.length >= 2)
                    .map((t, i) => (
                      <div class="adesrow" key={i}>
                        <button
                          class="mini"
                          onClick={() => void downloadAdes(t, i)}
                          title="The positions of this mover in every pass as an MPC ADES report (nothing is sent; the file says what is missing before it could be)"
                        >
                          MPC ADES · {trackNames[i] || `track ${i + 1}`}
                        </button>
                      </div>
                    ))}
                  {adesNote && <div class="ssum">{adesNote}</div>}
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
                  {pairs.length > 0 && pairs.map((pr, i) => <PairButton key={i} pair={pr} on={pairSel === i} onClick={() => openPair(i)} />)}
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
                        {(() => {
                          const tr = trackOfRed(x);
                          if (tr) return ` · moves ${rateText(tr)} (the mover track)`;
                          return x.moves && x.pmra != null
                            ? ` · moves ${(Math.hypot(x.pmra, x.pmdec ?? 0) / 1000).toFixed(1)}″/yr (red-band measurement)`
                            : '';
                        })()}
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
                  {dist.ds.state === 'ok' && (
                    <label class="ds-chk">
                      <input type="checkbox" checked={distShow} onChange={(e) => setDistShow((e.target as HTMLInputElement).checked)} /> Show
                      the distance search on the picture (instead of the sky)
                    </label>
                  )}
                  {FPX_AUTO && !fpx && (
                    <div class="fpxleads" data-testid="fpx-leads">
                      <div class="mono-s">
                        {fpxLeads.length
                          ? `${fpxLeads.length} Find Planet X ${fpxLeads.length === 1 ? 'lead' : 'leads'} in this field (peaks the photometry confirms, one per object across the searches and the deep sums) · a lead, not a discovery`
                          : fpxAllOver
                            ? 'No Find Planet X lead in this field'
                            : 'Find Planet X is still running on this field'}
                      </div>
                      {fpxLeads.map((l, i) => (
                        <button key={i} class="btn sm leadbtn" onClick={l.open}>
                          <b>Peak {i + 1}</b> · S/N {l.sn} · {l.d} au{l.range ? ` (${l.range})` : ''} · photometry{' '}
                          {l.phot != null ? `${l.phot}σ` : 'n/a'} · {l.label}
                        </button>
                      ))}
                    </div>
                  )}
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
                          <DistantPanel
                            d={d as typeof distRef}
                            currentPass={shown?.pass ?? 0}
                            passLabel={passMeta ? season(passMeta.mjd_mid) : undefined}
                            auto
                            onPick={onPeak}
                          />
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
                          autoHold={fpxHold || fpxStopped || !fpxAllOver}
                          onState={(x) => {
                            setDeepSt((o) => (o[0] === x.state ? o : [x.state, o[1]]));
                            setDeepFr((o) => (o[0] === x.frac ? o : [x.frac, o[1]]));
                            setDeepCands((o) => (o[0] === x.candidates ? o : [x.candidates, o[1]]));
                          }}
                        />
                        <DeepSumPanel
                          ra={ra}
                          dec={dec}
                          fov={fov}
                          passes={passes.map((q) => q.mjd_mid)}
                          autoRun={[250, 800]}
                          autoHold={fpxHold || fpxStopped || !fpxAllOver || !['done', 'none', 'error'].includes(deepSt[0])}
                          onState={(x) => {
                            setDeepSt((o) => (o[1] === x.state ? o : [o[0], x.state]));
                            setDeepFr((o) => (o[1] === x.frac ? o : [o[0], x.frac]));
                            setDeepCands((o) => (o[1] === x.candidates ? o : [o[0], x.candidates]));
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
            <div class={`sbar ${trayOpen ? '' : 'tray'}`} data-testid="scans">
              <button
                class="tray-head"
                onClick={() => setTrayOpen((o) => !o)}
                aria-expanded={trayOpen}
                title={trayOpen ? 'Fold the scans' : 'Show every scan'}
              >
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
                  <div
                    class="sline sline-px"
                    data-testid="search-px"
                    title="Find Planet X: the visits of a pass added along trial distances and own motions (30–300 au)"
                  >
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
                        <button
                          class="btn sm mini"
                          onClick={(e) => goPx(e, true)}
                          title="The deep sum of all passes: its plan and time are shown before it starts"
                        >
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
                    </>
                  }
                />

            </section>
            {ptab === 'task' && (flow.marksList || flow.peopleSearch.count) && (
              <section class="psec" id="sec-marks" data-testid="sec-marks">
                <div class="psh">Marks</div>
                {flow.marksList}
                {/* never an empty heading: without marks of mine and without people's marks on show, it says so */}
                {!flow.marksHere.length && !(opts.people && flow.peopleSearch.state === 'done') && (
                  <div class="people-line faint" data-testid="marks-none">
                    none yet
                  </div>
                )}
                {opts.people && flow.peopleSearch.state === 'done' && (
                  <div class="people-line" data-testid="people-line">
                    {Number(flow.peopleSearch.count ?? 0) > 0 ? `People's marks here: ${flow.peopleSearch.count} places` : "People's marks here: none yet"}
                  </div>
                )}
              </section>
            )}
            {ptab === 'obj' && !(sel || selAn !== null || pairSel !== null) && (
              <section class="psec" id="sec-obj-none">
                <div class="obj-none">Click an object on the picture</div>
              </section>
            )}
            {ptab === 'obj' && (sel || selAn !== null || pairSel !== null) && (
              <section class="psec" id="sec-obj" data-testid="sec-obj">
                <div class="pobj">
                  {(sel || selAn !== null) && (
                    <div class="sel-head" data-testid="sel-head">
                      <b>
                        {flow.blind
                          ? 'The object in question'
                          : selAn !== null
                            ? `Anomaly ${selAn}`
                            : (sel?.name ?? (sel ? `${formatRA(sel.ra)} ${formatDec(sel.dec)}` : ''))}
                      </b>
                      {sel && (
                        <button class="mini" onClick={() => setSpecOpen(true)} title="The spectrum, wide" data-testid="open-spectrum">
                          Wide
                        </button>
                      )}
                    </div>
                  )}
                  {flow.blind && (
                    // a task's find before its answers: nothing that would say what it is (a control object looks like any other)
                    <div class="obj-blind mono-s" data-testid="obj-blind">
                      Catalogues and its record · after your answers
                    </div>
                  )}
                  {!flow.blind && sel && flow.hintAt(sel.ra, sel.dec) && (
                    <div class="obj-hint" data-testid="obj-hint" title="A ghost, a spike or the glow of a bright star, or the frame's edge, can look like a source">
                      {flow.hintAt(sel.ra, sel.dec)}
                    </div>
                  )}
                  {!flow.markOpen && flow.card}
                  {!flow.blind && sel?.finding && <FindStatus id={sel.finding} />}
                  <details class="obj-more" data-testid="obj-more" open style={flow.blind ? { display: 'none' } : undefined}>
                    <summary>More about it</summary>
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
                        {sel && (
                          <div class="obj-spec" data-testid="obj-spectrum">
                            <div class="obj-h">Its light</div>
                            <SpectrumTab src={sel} inPassport />
                          </div>
                        )}
                        <div class="obj-h">Catalogues</div>
                        <ContextRows
                          ra={an.result.anomalies.find((a) => a.n === selAn)!.ra}
                          dec={an.result.anomalies.find((a) => a.n === selAn)!.dec}
                          pmra={an.result.anomalies.find((a) => a.n === selAn)!.pmra}
                          pmdec={an.result.anomalies.find((a) => a.n === selAn)!.pmdec}
                        />
                      </>
                    ) : (
                      <>
                        {sel &&
                          portraitOf(
                            sel,
                            sel.name ?? 'selected source',
                            <>
                              <ObjectGuess src={sel} held={flow.selHeld} />
                              {seenText(sel) && <div class="obj-seen">{seenText(sel)}</div>}
                            </>,
                          )}
                        <div class="obj-h">How it moves</div>
                        {sel && (
                          <div class="okv obj-moves" data-testid="obj-moves">
                            {(() => {
                              // our passes' own measure of its motion (the field's mover search), beside the catalogue's
                              const [r0, d0] = sel.pmra || sel.pmdec ? atEpoch(sel.ra, sel.dec, sel.pmra ?? 0, sel.pmdec ?? 0, 2025.9) : [sel.ra, sel.dec];
                              const t = (mvr?.tracks ?? []).find((x) => Math.hypot((x.ra_2025_9 - r0) * Math.cos((d0 * Math.PI) / 180), x.dec_2025_9 - d0) * 3600 < 8);
                              return (
                                <>
                                  <div class="orow">
                                    <span class="faint">Measured</span>
                                    <span>
                                      {t
                                        ? `${rateText(t)} toward ${t.pa.toFixed(0)}° · ${t.passes.length} passes`
                                        : sel.finding?.startsWith('film:') || sel.moving
                                          ? // a body moving within one pass (a time-lapse track): its speed there; between passes it is elsewhere
                                            `${openFind && openFind !== 'failed' && openFind.id === sel.finding ? rateWord(openFind) || 'moves' : 'moves'} within one pass`
                                          : mv?.state === 'done'
                                            ? 'no motion between our passes'
                                            : 'the mover search is running'}
                                    </span>
                                  </div>
                                  {(sel.pmra || sel.pmdec) && sel.pmKind !== 'measured' ? (
                                    <div class="orow">
                                      <span class="faint hov" title="Its motion along right ascension and declination, milliarcseconds a year">pmRA, pmDec</span>
                                      <span class="mono-s">
                                        {(sel.pmra ?? 0).toFixed(1)}, {(sel.pmdec ?? 0).toFixed(1)} mas/yr
                                      </span>
                                    </div>
                                  ) : null}
                                </>
                              );
                            })()}
                          </div>
                        )}
                        <ObjectTab src={sel} onSpectrum={() => setSpecOpen(true)} held={flow.selHeld} compact />
                        {sel && objCard && objCard.key === cardKey(sel.ra, sel.dec) && (
                          <div class="why-card" data-testid="object-card">
                            <b>{objCard.title}</b>
                            {objCard.rows.map(([k, v]) => (
                              <span key={k}>
                                <span class="faint">{k}: </span>
                                {v}
                              </span>
                            ))}
                            {objCard.links && (
                              <span class="xlinks">
                                {objCard.links.map((l) => (
                                  <a key={l.label} target="_blank" rel="noopener" href={l.href}>
                                    {l.label}
                                  </a>
                                ))}
                              </span>
                            )}
                          </div>
                        )}
                        {sel &&
                          sel.name === 'Find Planet X lead' &&
                          (() => {
                            // the lead's own stamp: the pass's visits shifted along its orbit and added (the body shows only so), with its numbers
                            let best: { ds: Extract<DistantState, { state: 'ok' }>; c: DistantCandidate } | null = null;
                            let bd = 1e9;
                            for (const run of fpxRuns) {
                              const dsr = run.ds;
                              if (dsr.state !== 'ok') continue;
                              for (const c of dsr.r.candidates) {
                                const sep = Math.hypot((c.ra - sel.ra) * Math.cos((sel.dec * Math.PI) / 180), c.dec - sel.dec) * 3600;
                                if (sep < bd) {
                                  bd = sep;
                                  best = { ds: dsr, c };
                                }
                              }
                            }
                            return best && bd < 60 ? <LeadPassport ds={best.ds} c={best.c} ra0={ra} dec0={dec} /> : null;
                          })()}
                        {sel && (
                          <div class="obj-spec" data-testid="obj-spectrum">
                            <div class="obj-h">Its light</div>
                            <SpectrumTab src={sel} inPassport />
                          </div>
                        )}
                        {sel && <LightCurves src={sel} field={{ ra, dec, fov }} />}
                        <div class="obj-h">Catalogues</div>
                        {/* the same epoch rule as the heading's identification: a source with a motion carries its J2000 place, a place on the picture is where the thing is in 2025.9 */}
                        {sel && (
                          <ContextRows
                            ra={sel.ra}
                            dec={sel.dec}
                            epoch={sel.pmra || sel.pmdec ? 2000 : 2025.9}
                            pmra={sel.pmra ?? 0}
                            pmdec={sel.pmdec ?? 0}
                            held={flow.selHeld}
                          />
                        )}
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

                  </details>
                </div>
              </section>
            )}
            <section class="psec" id="sec-show" data-testid="sec-show" hidden={ptab !== 'view'}>
              <div class="psh">On the picture</div>
              <div class="swlist">
                    {sw(
                      'knownChk',
                      'Known objects',
                      'Known objects: a ring on every Gaia DR3 star, SIMBAD object and JPL body in the frame, at its place on the date shown. What moves and has no ring is unknown',
                      opts.known,
                      (v) => set({ known: v ? null : '0' }),
                      undefined,
                      '',
                      false,
                      flow.knownSearch,
                    )}
                    {sw(
                      'movChk',
                      'Machine finds',
                      'Machine finds: a ring on every mover, track, brown dwarf and Planet X peak the searches found (between passes in Months, inside the pass in Days)',
                      opts.movers && (film === null || (ft.state === 'ok' && ft.tracks.length > 0)),
                      film === null || (ft.state === 'ok' && ft.tracks.length > 0) ? (v) => set({ movers: v ? null : '0' }) : null,
                      ft.state === 'ok'
                        ? 'the asteroid search found no track in this pass'
                        : 'the asteroid search of this pass has not finished yet',
                      '',
                      false,
                      film === null ? machineSearch : null,
                    )}
                    {sw(
                      'redChk',
                      'Red sources',
                      'Red sources: a ring and the name on every source that is bright at 3.8–5 µm and faint at 1.1–1.6 µm against the field’s own stars (the brown dwarf candidates are among them)',
                      opts.red,
                      (v) => set({ red: v ? '1' : null }),
                      redv?.result
                        ? 'the red-source search found none in this field'
                        : 'the red-source search of this field has not finished yet',
                      '',
                      false,
                      searchOf('red sources'),
                    )}
                    {sw(
                      'anoChk',
                      'Anomalies',
                      'Anomaly rings: sources whose spectrum differs from an ordinary star of the field',
                      opts.anomalies,
                      (v) => set({ ano: v ? '1' : null }),
                      an?.result
                        ? 'the anomaly search found no anomaly in this field'
                        : 'the anomaly search of this field has not finished yet',
                      '',
                      false,
                      searchOf('anomalies'),
                    )}
                    {sw(
                      'peopleChk',
                      "People's marks",
                      "People's marks: what people said on this field (it moves, an artefact, not in the catalogues), merged by place with a count and never a name; shown where you have marked or answered too",
                      opts.people,
                      (v) => set({ people: v ? (inTask ? null : '1') : inTask ? '0' : null }),
                      undefined,
                      '',
                      false,
                      flow.peopleSearch,
                    )}
                    {sw(
                      'trailChk',
                      'Paths',
                      'Trajectory: the path of the test target (or of a found mover) over the passes, a dot at each pass date',
                      opts.trajectory,
                      (v) => set({ traj: v ? null : '0' }),
                    )}
                  {sw(
                    'archChk',
                    'Old surveys',
                    'Old surveys: the same field in DSS2 red (1985–1999), 2MASS Ks (1997–2001) and AllWISE W1 (2010), blinked before the SPHEREx passes',
                    opts.archive,
                    film !== null ? null : (v) => set({ arch: v ? '1' : null }),
                    'Months only',
                  )}
              </div>
            </section>
            <section class="psec" id="sec-view" data-testid="sec-view" hidden={ptab !== 'view'}>
              <div class="psh">View</div>
              <div class="seg sm vseg" role="group" aria-label="View">
                {VIEWS.map((v) => (
                  <button
                    key={v.id}
                    class={v.id === view && film === null ? 'on' : ''}
                    aria-pressed={v.id === view && film === null}
                    title={v.tip}
                    onClick={() => set({ view: v.id === 'picture' ? null : v.id, film: null })}
                    data-testid={`view-${v.id}`}
                  >
                    {VIEW_WORD[v.id] ?? v.name}
                  </button>
                ))}
              </div>
            </section>
            <section class="psec" id="sec-tune" data-testid="sec-tune" hidden={ptab !== 'view'}>
              <div class="psh">Picture look</div>
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
              <div class="swgrid chips">
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
                    {sw(
                      'ringsChk',
                      'Speed rings',
                      'Speed rings: how far a body of 1, 3, 10, 30, 100 or 300 ″/yr moves between the first and the last pass (″/h between the first and last visit in Days), centred on the selected source',
                      opts.rings,
                      (v) => set({ rings: v ? '1' : null }),
                    )}
                    {sw(
                      'maskChk',
                      'Artifacts',
                      'Artifacts: red where the SPHEREx pipeline flagged the pixel in many exposures of the season shown',
                      opts.artifacts,
                      wallView ? null : (v) => set({ art: v ? '1' : null }),
                      'shown on the Picture and in Days, not on the walls',
                    )}
                    {sw(
                      'ringChk',
                      'Target marker',
                      'Marker: a ring and the name of the test target, of the selected source and of the known asteroids, at their place on the date shown',
                      opts.marker,
                      (v) => set({ marker: v ? null : '0' }),
                    )}
              </div>
            </section>
              <section class="psec" id="sec-place" data-testid="sec-place" hidden={ptab !== 'view'}>
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
          </div>
        </aside>
        {specOpen && sel && (
          <div class="specsheet" role="dialog" aria-label="Spectrum" data-testid="spec-sheet">
            <div class="ss-head">
              <b>Spectrum · {sel.name ?? `${formatRA(sel.ra)} ${formatDec(sel.dec)}`}</b>
              <button class="mini" aria-label="Close the spectrum" onClick={() => setSpecOpen(false)} data-testid="spec-close">
                ×
              </button>
            </div>
            <div class="ss-body">
              <SpectrumTab src={sel} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
