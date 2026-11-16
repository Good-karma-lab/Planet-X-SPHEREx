import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { aim, type AimState } from '../lib/aim';
import { formatHash } from '../lib/route';
import { atEpoch, D2R, formatDec, formatRA, parseDec, parseRA, separation, SURVEY_EPOCH } from '../lib/sky';
import { icon } from '../ui/icons';
import { backdropYear, coldNote, imageDates, onImageDates, shownPlace, type ArchiveImage } from './globe/epochs';
import { sky, STAR_MAP_TO_VISIBLE, type SkyLayer } from './globe/sky';
import { catalogNow, kept, loadCatalog, type Obj, type SurveyId } from './globe/store';
import { CELL_DEG, KIND_COLOUR, KIND_SHORT, KIND_WORD, barText, cellCentre, coverageNow, drawCoverage, kindOf, loadCell, loadCoverage, parseId, pickCell, type CellCard, type Kind } from './globe/coverage';
import { equiOf, onEquiTile } from '../lib/equi';
import { HIPS, bakedOf, loadManifest, manifestKnown, tiles } from '../lib/hips';
import { KnownList } from './search/known';
import { drawBodyPath, drawMovingPath, placeAt, type BodyPath, type MovingObject } from './search/path';
import './globe/globe.css';

const KIND: Record<string, { cls: string; dot: string }> = {
  bd: { cls: 'orange', dot: '#ff9a5a' },
  fast: { cls: 'yellow', dot: '#ffe066' },
  mira: { cls: 'purple', dot: '#c9a2ff' },
  var: { cls: 'purple', dot: '#c9a2ff' },
  young: { cls: 'blue', dot: '#6ad0ff' },
};
const TARGET_CLASS: Record<string, string> = {
  "Barnard's Star": 'Red dwarf',
  'ε Indi B': 'T dwarf binary',
  'WISE 0855−0714': 'Y dwarf',
  'RW Aurigae': 'Young variable (T Tauri)',
  'R Draconis': 'Mira variable',
};

const STAR_MAP_CREDIT = 'NASA/Goddard Space Flight Center Scientific Visualization Studio. Gaia DR2: ESA/Gaia/DPAC.';
const SURVEYS: { id: SurveyId; name: string; layer: SkyLayer[]; note: string }[] = [
  {
    id: 'svs',
    name: 'Star map · Gaia (NASA SVS)',
    layer: [{ id: 'svs', gain: 1, black: 0 }],
    note: `Deep Star Maps 2020: a map of the stars made from Gaia DR2, Hipparcos and Tycho. ${STAR_MAP_CREDIT}`,
  },
  {
    id: 'dss2',
    name: 'Visible — DSS2',
    layer: [{ id: 'dss2', gain: 2.4, black: 16 }],
    note: 'Digitized Sky Survey: red, blue and infrared photographic plates, colour',
  },
  {
    id: 'spherex',
    name: 'SPHEREx — DR1',
    // three frames of the cube as the three colours
    layer: [
      { id: 'spx88', gain: 1.25, black: 0 },
      { id: 'spx40', gain: 1.25, black: 0 },
      { id: 'spx8', gain: 1.25, black: 0 },
    ],
    note: 'SPHEREx DR1 all-sky cube (IRSA, a visualisation product): 0.91 µm blue, 1.86 µm green, 4.50 µm red',
  },
  {
    id: '2mass',
    name: 'Near-infrared — 2MASS',
    layer: [{ id: '2mass', gain: 2.2, black: 0 }],
    note: '2MASS J, H and Ks (1.2, 1.7, 2.2 µm), colour',
  },
  {
    id: 'wise',
    name: 'Infrared — WISE',
    layer: [{ id: 'wise', gain: 2.4, black: 0 }],
    note: 'AllWISE W1, W2 and W4 (3.4, 4.6, 22 µm), colour',
  },
  {
    id: 'dust',
    name: 'Dust map',
    layer: [{ id: 'dust', gain: 1, black: 0 }],
    note: 'IRAS far infrared (IRIS: 12, 60, 100 µm): the dust of the Galaxy, colour',
  },
];
const SIZES = [2, 5, 10, 20, 60];
/** the plain word on a layer button (the survey's own name is its tooltip) */
/** the one line under the globe for each layer: what it is and who made it */
const LAYER_CAPTION: Record<string, string> = {
  svs: 'Star map · NASA SVS · Gaia DR2 (ESA/Gaia/DPAC)',
  dss2: 'Visible · DSS2 · STScI, via CDS',
  spherex: 'SPHEREx · DR1 all-sky cube · NASA/JPL-Caltech (IRSA)',
  '2mass': 'Near-infrared · 2MASS · UMass/IPAC-Caltech/NASA/NSF',
  wise: 'Infrared · AllWISE · NASA/JPL-Caltech/UCLA',
  dust: 'Dust map · IRAS IRIS · Miville-Deschênes & Lagache',
};
const LAYER_WORD: Record<string, string> = { svs: 'Star map', dss2: 'Visible', spherex: 'SPHEREx', '2mass': 'Near-infrared', wise: 'Infrared', dust: 'Dust map' };
/** the globe zoom limits; above ZOOM_NEAR a pin is an open ring so that the star under it shows */
const ZOOM_MIN = 0.8;
const ZOOM_MIN_INSIDE = 1.05;
const ZOOM_MAX = 400;
const ZOOM_NEAR = 30;
/** A name SIMBAD knows (CDS Sesame, answered from the browser): its place, type and proper motion (mas/yr). */
interface Named { n: string; type: string; ra: number; dec: number; pmra: number; pmdec: number }
/** Asks CDS Sesame for a name (SIMBAD first); null when it knows no such name. */
async function sesame(q: string, signal: AbortSignal): Promise<Named | null> {
  // the service's resolver first (cached, near SIMBAD); CDS Sesame from the browser when the service has none
  const sv = await fetch(`/api/v1/resolve?name=${encodeURIComponent(q)}`, { signal }).catch(() => null);
  if (sv && sv.ok) {
    const d = (await sv.json()) as { found?: boolean; name?: string; type?: string; ra?: number; dec?: number; pmra?: number; pmdec?: number };
    if (d.found === false || d.ra == null || d.dec == null) return null;
    return { n: d.name ?? q, type: d.type ?? '', ra: d.ra, dec: d.dec, pmra: d.pmra ?? 0, pmdec: d.pmdec ?? 0 };
  }
  const r = await fetch(`https://cds.unistra.fr/cgi-bin/nph-sesame/-oI/S?${encodeURIComponent(q)}`, { signal });
  if (!r.ok) return null;
  const t = await r.text();
  const j = t.match(/^%J\s+([\d.+-]+)\s+([\d.+-]+)/m);
  if (!j) return null;
  const n = (t.match(/^%I\.0\s+(.+)$/m)?.[1] ?? q).replace(/^\*+\s*/, '').trim();
  const p = t.match(/^%P\s+([\d.+-]+)\s+([\d.+-]+)/m);
  return { n, type: (t.match(/^%C\.0\s+(\S+)/m)?.[1] ?? '').trim(), ra: Number(j[1]), dec: Number(j[2]), pmra: p ? Number(p[1]) : 0, pmdec: p ? Number(p[2]) : 0 };
}
const pad2 = (v: number) => String(Math.floor(v)).padStart(2, '0');
/** RA and Dec as 17:57:48.5 +04:41:36, short enough for one line of a card */
function shortCoords(ra: number, dec: number): string {
  const h = (((ra % 360) + 360) % 360) / 15, hm = (h % 1) * 60, hs = (hm % 1) * 60;
  const ad = Math.abs(dec), dm = (ad % 1) * 60, ds = (dm % 1) * 60;
  return `${pad2(h)}:${pad2(hm)}:${hs.toFixed(1).padStart(4, '0')} ${dec < 0 ? '−' : '+'}${pad2(ad)}:${pad2(dm)}:${pad2(ds)}`;
}
const ROW_ICON = (n: string) => <span dangerouslySetInnerHTML={{ __html: icon(n, 16) }} style={{ display: 'contents' }} />;

/** a catalogue object faster than this (mas/yr) gets its path drawn when selected */
const PATH_PM = 50;

/** Demo fields: places where the tool shows something worth seeing, with the view they open in. */
const DEMOS: { n: string; what: string; ra: number; dec: number; fov: number; film?: number }[] = [
  { n: '3I/ATLAS', what: 'interstellar comet · film of 14 Dec 2025', ra: 166.8274, dec: 5.2787, fov: 60, film: 61023.6 },
  {
    n: 'Asteroids near the ecliptic',
    what: 'film of the 14–20 Dec 2025 pass · known bodies ringed',
    ra: 169.8917,
    dec: 4.3514,
    fov: 60,
    film: 61027,
  },
  { n: 'Eris field', what: 'the dwarf planet beyond Neptune', ra: 27.378, dec: -0.12, fov: 20 },
];

/** Small bodies the tool shows in a film: each opens the film of the pass in which SPHEREx saw it, with the body ringed. */
const SMALL: { n: string; what: string; ra: number; dec: number; fov: number; film: number }[] = [
  { n: '3I/ATLAS', what: 'interstellar comet · 55 SPHEREx images on 14 Dec 2025', ra: 166.8274, dec: 5.2787, fov: 60, film: 61023.6 },
  {
    n: '(9952) 1991 AK',
    what: 'main-belt asteroid · V 18.1 · 119 images, 14–20 Dec 2025',
    ra: 169.8917,
    dec: 4.3514,
    fov: 60,
    film: 61027,
  },
  {
    n: '(9078) 1994 PB2',
    what: 'main-belt asteroid · V 18.9 · 118 images, 14–20 Dec 2025',
    ra: 169.8917,
    dec: 4.3514,
    fov: 60,
    film: 61027,
  },
  {
    n: '(11020) Orwell',
    what: 'main-belt asteroid · V 18.8 · 121 images, 14–20 Dec 2025',
    ra: 169.8917,
    dec: 4.3514,
    fov: 60,
    film: 61027,
  },
  {
    n: '(43959) 1997 CB26',
    what: 'main-belt asteroid · V 18.4 · 107 images, 14–20 Dec 2025',
    ra: 169.8917,
    dec: 4.3514,
    fov: 60,
    film: 61027,
  },
];

function mollweideTheta(phi: number): number {
  let th = phi;
  for (let i = 0; i < 12; i++) {
    const d = 2 + 2 * Math.cos(2 * th);
    if (Math.abs(d) < 1e-9) break;
    th -= (2 * th + Math.sin(2 * th) - Math.PI * Math.sin(phi)) / d;
  }
  return th;
}

function openLab(ra: number, dec: number, fov: number) {
  location.hash = formatHash({ screen: 'lab', params: { ra: ra.toFixed(5), dec: dec.toFixed(5), fov: String(fov) } });
}

/** Where a catalogue object is during the SPHEREx survey: the place the lab opens (and the warm set is built for). */
const epochPlace = (o: Obj): [number, number] =>
  o.pmra != null && o.pmdec != null ? atEpoch(o.ra, o.dec, o.pmra, o.pmdec, SURVEY_EPOCH) : [o.ra, o.dec];

const moving = (o: Obj): MovingObject | null =>
  o.pmra != null && o.pmdec != null && Math.hypot(o.pmra, o.pmdec) > PATH_PM ? { name: o.n, ra: o.ra, dec: o.dec, pmra: o.pmra, pmdec: o.pmdec } : null;

/** The archive images of a test target or the selected object (asked for once); other objects use the surveys' middle years. */
const datesOf = (o: Obj, sel: Obj | null): ArchiveImage[] | null | undefined => (o.b || o === sel ? imageDates(o.n, o.ra, o.dec) : undefined);

/** Sky span (degrees) the view should show for an object: its path from the earliest image to 2050, or a few field widths. */
function spanOf(o: Obj, fov: number): number {
  const mo = moving(o);
  if (!mo) return (fov / 60) * 3;
  const imgs = imageDates(o.n, o.ra, o.dec);
  const y0 = Math.min(1950, ...(imgs ?? []).map((x) => x.year));
  return Math.max((fov / 60) * 1.2, (Math.hypot(mo.pmra, mo.pmdec) * (2050 - y0)) / 3.6e6);
}


type V3 = [number, number, number];
/** The view's own axes in the sky's coordinates: c to the centre of the view, e to the left of the picture (east), n up. */
interface Frame {
  c: V3;
  e: V3;
  n: V3;
}
const cross3 = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const dot3 = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];

/** The frame of a view centred on (ra, dec) degrees and turned by roll radians about its axis. */
function frameOf(raDeg: number, decDeg: number, roll: number): Frame {
  const ra = raDeg * D2R;
  const de = decDeg * D2R;
  const c: V3 = [Math.cos(de) * Math.cos(ra), Math.cos(de) * Math.sin(ra), Math.sin(de)];
  const e0: V3 = [-Math.sin(ra), Math.cos(ra), 0];
  const n0: V3 = [-Math.sin(de) * Math.cos(ra), -Math.sin(de) * Math.sin(ra), Math.cos(de)];
  const cr = Math.cos(roll);
  const sr = Math.sin(roll);
  return {
    c,
    e: [e0[0] * cr + n0[0] * sr, e0[1] * cr + n0[1] * sr, e0[2] * cr + n0[2] * sr],
    n: [n0[0] * cr - e0[0] * sr, n0[1] * cr - e0[1] * sr, n0[2] * cr - e0[2] * sr],
  };
}

/** The centre, the roll and so on of a frame (the roll is measured from the standard north-up frame at that centre, so it stays continuous over a pole). */
function viewOf(F: Frame): { ra: number; dec: number; roll: number } {
  const ra = Math.atan2(F.c[1], F.c[0]);
  const de = Math.asin(Math.max(-1, Math.min(1, F.c[2])));
  const e0: V3 = [-Math.sin(ra), Math.cos(ra), 0];
  const n0: V3 = [-Math.sin(de) * Math.cos(ra), -Math.sin(de) * Math.sin(ra), Math.cos(de)];
  return { ra: ((ra / D2R) % 360 + 360) % 360, dec: de / D2R, roll: Math.atan2(dot3(F.e, n0), dot3(F.e, e0)) };
}

/** The frame turned by the rotation (about the axis u × v, by the angle between them) that takes the direction u to the direction v: the way a trackball turns. */
function turnFrame(F: Frame, u: V3, v: V3): Frame {
  const a = cross3(u, v);
  const s = Math.hypot(a[0], a[1], a[2]);
  if (s < 1e-12) return F;
  const k: V3 = [a[0] / s, a[1] / s, a[2] / s];
  const th = Math.atan2(s, dot3(u, v));
  const ct = Math.cos(th);
  const st = Math.sin(th);
  // Rodrigues' rotation of one vector about k by th
  const rot = (x: V3): V3 => {
    const kx = cross3(k, x);
    const kd = dot3(k, x) * (1 - ct);
    return [x[0] * ct + kx[0] * st + k[0] * kd, x[1] * ct + kx[1] * st + k[1] * kd, x[2] * ct + kx[2] * st + k[2] * kd];
  };
  return { c: rot(F.c), e: rot(F.e), n: rot(F.n) };
}

/** An address that names a place (#/explore?ra=..&dec=..[&fov=..]) puts the aim there; the view opens on it. Null when it names none. */
function hashAim(): { ra: number; dec: number } | null {
  const q = new URLSearchParams(location.hash.split('?')[1] ?? '');
  const ra = Number(q.get('ra'));
  const dec = Number(q.get('dec'));
  if (!q.get('ra') || !q.get('dec') || !Number.isFinite(ra) || !Number.isFinite(dec) || Math.abs(dec) > 90) return null;
  const fov = Number(q.get('fov'));
  aim.set({ ra: ((ra % 360) + 360) % 360, dec, ...(SIZES.includes(fov) ? { fov } : {}) }, 'address');
  return { ra: ((ra % 360) + 360) % 360, dec };
}

const covKind = (c: CellCard): Kind | null => kindOf({ state: c.state, finds: c.n_finds, viewers: c.viewers });
/** The state of a square in the words of its card. */
function covStateWord(c: CellCard): string {
  const base =
    c.state === 'searched' ? 'Searched by the machine' : c.state === 'searching' ? 'Being searched now' : c.state === 'queued' ? 'In the queue of the search' : c.state === 'failed' ? 'The search failed' : 'Not searched yet';
  return `${base}${c.n_finds > 0 ? ' · has finds' : ''}${c.viewers > 0 ? ' · viewed by people' : ''}`;
}
/** The line of the legend: how much of the sky is done (from the list on hand; the argument only says "read again"). */
function covLine(_tick: number): string {
  const c = coverageNow();
  if (!c) return 'reading the grid…';
  const searched = c.counts?.searched ?? [...c.cells.values()].filter((x) => x.state === 'searched').length;
  return `${searched.toLocaleString('en-US')} of ${c.total.toLocaleString('en-US')} squares searched`;
}

export function Globe() {
  const linked0 = useRef<{ ra: number; dec: number } | null | undefined>(undefined);
  if (linked0.current === undefined) linked0.current = hashAim();
  const [catalog, setCatalog] = useState<Obj[]>(catalogNow);
  const [a, setA] = useState<AimState>(aim.get());
  const [surveyPick, setSurvey] = useState<SurveyId>(kept.survey);
  // the baked tiles of the star map: known once the service's manifest is read
  const [, setManifestTick] = useState(0);
  useEffect(() => {
    void loadManifest().then(() => setManifestTick((t) => t + 1));
  }, []);
  const svsOn = (!!bakedOf('svs') && !!HIPS['svs']) || !!equiOf('svs');
  // until the manifest has answered, the star map waits (a dark sky), not DSS2 that is then replaced; if it has none, DSS2 stands in
  const survey: SurveyId = surveyPick === 'svs' && !svsOn && manifestKnown() ? 'dss2' : surveyPick;
  const [proj, setProj] = useState<'globe' | 'moll'>(kept.proj);
  const [inside, setInside] = useState(kept.inside);
  // zoomed in past the resolution of the star map: DSS2 is faded in over it, and the credit line says so
  // zoomed past what the star map holds the layer itself changes to Visible (as if chosen by hand) and back when zoomed out again; a layer chosen by hand is never changed
  const autoVisible = useRef(false);
  // the layer whose picture is ON SHOW (the painter keeps the old picture until the new layer's tiles are under the view): the epoch caption and the pins follow it
  const [shownLayer, setShownLayer] = useState<SurveyId>(kept.survey);
  const shownRef = useRef<SurveyId>(kept.survey);
  // the zoom slider: its position (0 = the widest view, 1 = the deepest) follows wheel and pinch; the globe's effect fills the API
  const [zt, setZt] = useState(0.3);
  const zoomApi = useRef<{ set: (t: number) => void; by: (k: number) => void }>({ set: () => {}, by: () => {} });
  const [catOpen, setCatOpen] = useState(kept.catOpen);
  const [filter, setFilter] = useState(kept.filter);
  const [query, setQuery] = useState(kept.query);
  const [page, setPage] = useState(kept.page);
  const [per, setPer] = useState(kept.per);
  const [sel, setSel] = useState<Obj | null>(() => {
    const o = catalogNow().find((x) => x.n === kept.sel) ?? null;
    // the lab may have moved the aim while the globe was not on screen: the selection then no longer applies
    return o && separation(...epochPlace(o), aim.get().ra, aim.get().dec) < 0.05 ? o : null;
  });
  const [count, setCount] = useState<{ n: number; by: Record<string, number> } | null>(null);
  const [raIn, setRaIn] = useState(formatRA(a.ra));
  const [decIn, setDecIn] = useState(formatDec(a.dec));
  const [, setDatesTick] = useState(0);
  // the Coverage layer (the grid of squares over any background): on from the switch, or from a link with cov=1 (the landing's "Start searching")
  const [covOn, setCovOn] = useState(() => kept.covOn || new URLSearchParams(location.hash.split('?')[1] ?? '').get('cov') === '1');
  const [covTick, setCovTick] = useState(0);
  // the square whose card is open: its id at once, its card when the service has answered
  const [cellId, setCellId] = useState<string | null>(null);
  const [cell, setCell] = useState<CellCard | null>(null);
  const cv = useRef<HTMLCanvasElement>(null);
  // a comet or asteroid chosen in the list: its path since April 2025 and where it is today
  const [bodyPath, setBodyPath] = useState<BodyPath | null>(null);
  const live = useRef({ survey, proj, inside, a, sel, catalog, bodyPath, covOn, cellId });
  live.current = { survey, proj, inside, a, sel, catalog, bodyPath, covOn, cellId };
  const dirty = useRef(true);
  // the globe opens wide: a view kept from a deep zoom (a 5′ frame) comes back as about 50° across, centred on the same place (decided once, when it opens)
  const view = useRef(
    linked0.current
      ? { ra: linked0.current.ra, dec: linked0.current.dec, zoom: 2.4 }
      : kept.view
        ? { ...kept.view, zoom: kept.view.zoom > 8 ? 2.4 : kept.view.zoom }
        : { ra: a.ra, dec: a.dec, zoom: 1.6 },
  );
  kept.view = view.current;
  const flyRef = useRef<(ra: number, dec: number, spanDeg?: number) => void>(() => {});

  // the choices survive a visit to another screen
  useEffect(() => {
    Object.assign(kept, { survey: autoVisible.current ? 'svs' : surveyPick, proj, inside, covOn, catOpen, filter, query, page, per, sel: sel?.n ?? null });
  }, [surveyPick, proj, inside, covOn, catOpen, filter, query, page, per, sel]);

  useEffect(() => {
    void loadCatalog().then((c) => setCatalog((old) => (old === c ? old : c)));
    const offDates = onImageDates(() => {
      dirty.current = true;
      setDatesTick((t) => t + 1);
    });
    const offAim = aim.on((next, source) => {
      setA(next);
      if (source !== 'input') {
        setRaIn(formatRA(next.ra));
        setDecIn(formatDec(next.dec));
      }
      dirty.current = true;
    });
    return () => {
      offDates();
      offAim();
    };
  }, []);

  // the dates of the test targets' archive images, so that their pins stand on the right picture
  useEffect(() => {
    for (const o of catalog) if (o.b) imageDates(o.n, o.ra, o.dec);
  }, [catalog]);

  // exposures under the aim frame, from the index
  useEffect(() => {
    setCount(null);
    const ctl = new AbortController();
    const t = setTimeout(() => {
      void fetch(`/api/v1/coverage?ra=${a.ra.toFixed(5)}&dec=${a.dec.toFixed(5)}&fov=${a.fov}`, { signal: ctl.signal })
        .then((r) => (r.ok ? r.json() : null))
        .then(
          (c: { n_exposures: number; by_release: Record<string, number> } | null) => c && setCount({ n: c.n_exposures, by: c.by_release }),
        )
        .catch(() => {});
    }, 350);
    return () => {
      clearTimeout(t);
      ctl.abort();
    };
  }, [a.ra, a.dec, a.fov]);

  useEffect(() => {
    dirty.current = true;
  }, [survey, proj, inside, sel, catalog, bodyPath, covOn, cellId, covTick]);

  // the coverage list: read when the layer is switched on, and again every minute while it is on (the machine keeps searching)
  useEffect(() => {
    if (!covOn) return;
    let on = true;
    const read = (fresh: boolean) => void loadCoverage(fresh).then(() => on && setCovTick((t) => t + 1));
    read(false);
    const iv = setInterval(() => read(true), 60000);
    return () => {
      on = false;
      clearInterval(iv);
    };
  }, [covOn]);
  // the card of the chosen square
  useEffect(() => {
    setCell(null);
    if (!cellId) return;
    let on = true;
    void loadCell(cellId).then((c) => on && setCell(c));
    return () => {
      on = false;
    };
  }, [cellId, covTick]);
  useEffect(() => {
    if (!covOn) setCellId(null);
  }, [covOn]);

  // the sky canvas
  useEffect(() => {
    const c = cv.current;
    if (!c) return;
    const ctx = c.getContext('2d')!;
    let W = 10;
    let H = 10;
    let dpr = 1;
    let raf = 0;
    let lastSig = '';
    let lastLayer = '';
    let settleAt = 0;
    let settling = false;
    const resize = () => {
      const r = c.getBoundingClientRect();
      dpr = Math.min(2, devicePixelRatio || 1);
      W = Math.max(10, r.width);
      H = Math.max(10, r.height);
      c.width = W * dpr;
      c.height = H * dpr;
      dirty.current = true;
    };
    const ro = new ResizeObserver(resize);
    ro.observe(c.parentElement!);
    resize();
    const offTile = tiles.onTile(() => (sky.stale = true));
    const offEqui = onEquiTile(() => (sky.stale = true));

    const R = () => (Math.min(W, H) / 2) * 0.9 * view.current.zoom;
    /** seen from inside the globe */
    const inn = () => live.current.proj === 'globe' && live.current.inside;
    /** the widest view: a perspective picture stays natural up to about 110° across */
    const zoomMin = () => (inn() ? ZOOM_MIN_INSIDE : ZOOM_MIN);
    // the Mollweide map: zoom and the shift of its centre (it zooms and pans like the globe turns)
    const mv = { z: 1, x: 0, y: 0 };
    let syncZt = (): void => {};
    const mw = () => Math.min(W - 20, (H - 20) * 2) * mv.z;
    const mcx = () => W / 2 + mv.x;
    const mcy = () => H / 2 + mv.y;
    const basis = (): Frame => frameOf(view.current.ra, view.current.dec, view.current.roll ?? 0);
    type B = ReturnType<typeof basis>;
    const project = (ra: number, dec: number, b: B): [number, number, boolean] => {
      if (live.current.proj === 'moll') {
        const th = mollweideTheta(dec * D2R);
        const lam = (((ra - 180 + 540) % 360) - 180) * D2R;
        return [mcx() - ((lam * Math.cos(th)) / Math.PI) * (mw() / 2), mcy() - Math.sin(th) * (mw() / 4), true];
      }
      const r = ra * D2R;
      const d = dec * D2R;
      const v = [Math.cos(d) * Math.cos(r), Math.cos(d) * Math.sin(r), Math.sin(d)];
      const z = v[0]! * b.c[0]! + v[1]! * b.c[1]! + v[2]! * b.c[2]!;
      if (inn()) {
        if (z < 0.05) return [0, 0, false];
        return [
          W / 2 - ((v[0]! * b.e[0]! + v[1]! * b.e[1]! + v[2]! * b.e[2]!) / z) * R(),
          H / 2 - ((v[0]! * b.n[0]! + v[1]! * b.n[1]! + v[2]! * b.n[2]!) / z) * R(),
          true,
        ];
      }
      if (z < 0.02) return [0, 0, false];
      return [
        W / 2 - (v[0]! * b.e[0]! + v[1]! * b.e[1]! + v[2]! * b.e[2]!) * R(),
        H / 2 - (v[0]! * b.n[0]! + v[1]! * b.n[1]! + v[2]! * b.n[2]!) * R(),
        true,
      ];
    };
    const unproject = (px: number, py: number): [number, number] | null => {
      if (live.current.proj === 'moll') {
        const X = (px - mcx()) / (mw() / 2);
        const Y = -(py - mcy()) / (mw() / 4);
        if (X * X + Y * Y > 1) return null;
        const th = Math.asin(Y);
        const phi = Math.asin((2 * th + Math.sin(2 * th)) / Math.PI);
        const lam = (Math.PI * X) / Math.cos(th);
        return [(((180 - lam / D2R) % 360) + 360) % 360, phi / D2R];
      }
      let sx = (W / 2 - px) / R();
      let sy = (H / 2 - py) / R();
      let z: number;
      if (inn()) {
        const n = Math.sqrt(1 + sx * sx + sy * sy);
        z = 1 / n;
        sx /= n;
        sy /= n;
      } else {
        const rr = sx * sx + sy * sy;
        if (rr > 1) return null;
        z = Math.sqrt(1 - rr);
      }
      const b = basis();
      const v = [0, 1, 2].map((i) => z * b.c[i]! + sx * b.e[i]! + sy * b.n[i]!);
      return [(((Math.atan2(v[1]!, v[0]!) / D2R) % 360) + 360) % 360, Math.asin(Math.max(-1, Math.min(1, v[2]!))) / D2R];
    };

    /** the size of one square of the grid on screen (at the centre of the globe; along the equator of the map), CSS pixels */
    const cellPx = () => (live.current.proj === 'moll' ? (mw() / 360) * CELL_DEG : R() * CELL_DEG * D2R);
    // where each test target's pin and the selected object's pin were drawn: the click finds them there
    let pins: { o: Obj; x: number; y: number }[] = [];
    const font = getComputedStyle(document.body).fontFamily;
    const draw = () => {
      const st = live.current;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      if (sky.shown) {
        ctx.imageSmoothingEnabled = true;
        ctx.drawImage(sky.canvas, 0, 0, W, H);
      }
      const b = basis();
      // grid
      ctx.save();
      ctx.strokeStyle = 'rgba(255,255,255,.10)';
      ctx.lineWidth = 1;
      ctx.fillStyle = 'rgba(255,255,255,.45)';
      ctx.font = `11px ${font}`;
      const line = (pts: [number, number][]) => {
        ctx.beginPath();
        let pen = false;
        let lx = 0;
        for (const [ra, de] of pts) {
          const p = project(ra, de, b);
          if (!p[2] || (st.proj === 'moll' && pen && Math.abs(p[0] - lx) > W / 2)) {
            pen = false;
            if (!p[2]) continue;
          }
          if (pen) ctx.lineTo(p[0], p[1]);
          else ctx.moveTo(p[0], p[1]);
          pen = true;
          lx = p[0];
        }
        ctx.stroke();
      };
      for (let ra = 0; ra < 360; ra += 30) {
        const pts: [number, number][] = [];
        for (let de = -90; de <= 90; de += 3) pts.push([ra, de]);
        line(pts);
        const lp = project(ra, 2, b);
        if (lp[2]) ctx.fillText(`${ra / 15}h`, lp[0] + 3, lp[1] - 3);
      }
      for (let de = -60; de <= 60; de += 30) {
        const pts: [number, number][] = [];
        for (let ra = 0; ra <= 360; ra += 3) pts.push([ra, de]);
        line(pts);
        const lp = project(st.proj === 'moll' ? 170 : view.current.ra, de, b);
        if (lp[2]) ctx.fillText(`${de > 0 ? '+' : ''}${de}°`, lp[0] + 3, lp[1] - 3);
      }
      ctx.restore();
      ctx.strokeStyle = 'rgba(255,255,255,.22)';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      if (st.proj === 'globe' && !st.inside) ctx.arc(W / 2, H / 2, R(), 0, 7);
      else if (st.proj === 'globe') ctx.rect(-5, -5, 1, 1);
      else ctx.ellipse(mcx(), mcy(), mw() / 2, mw() / 4, 0, 0, 7);
      ctx.stroke();
      if (st.covOn) {
        const half = Math.hypot(W, H) / 2 / R();
        drawCoverage(ctx, coverageNow(), {
          project: (ra, dec) => project(ra, dec, b),
          ra: view.current.ra,
          dec: view.current.dec,
          radius: st.proj === 'moll' ? 180 : inn() ? Math.atan(half) / D2R : half >= 1 ? 90 : Math.asin(half) / D2R,
          cellPx: cellPx(),
          W,
          H,
          flat: st.proj === 'moll',
          selected: st.cellId,
          font,
        });
      }
      const near = st.proj === 'globe' && view.current.zoom > ZOOM_NEAR;
      // zoomed past the star map DSS2 shows over it: the pins and the path stand on the DSS2 plate's date, not on Gaia's
      // and on the layer whose picture is ON SHOW: a new layer's date is taken only when its tiles are under the view (a pin never jumps to an empty sky)
      const shownId = SURVEYS.find((x) => x.layer.map((l) => l.id).join('+') === sky.shownKey)?.id ?? st.survey;
      if (shownId !== shownRef.current) {
        shownRef.current = shownId;
        setShownLayer(shownId);
      }
      pins = [];
      if (st.bodyPath) drawBodyPath(ctx, (ra, dec) => project(ra, dec, b), st.bodyPath, W / 2, font);
      // the selected object's path: from its earliest archive image to 2050, a dot at every image date, the SPHEREx stretch, and its
      // pin at its place on the date of the backdrop shown (the path is drawn larger than true when it would be under 60 px)
      let pathPin = false;
      const mo = st.sel && moving(st.sel);
      if (st.sel && mo) {
        const imgs = imageDates(st.sel.n, st.sel.ra, st.sel.dec) ?? [];
        const by = backdropYear(shownId, imgs);
        // a dot at the middle of the calendar year of each image, so that its label reads the year the image was taken
        const years = imgs.map((x) => Math.floor(x.year) + 0.499);
        const y0 = Math.min(by, ...years);
        const P = (y: number) => project(...placeAt(mo, y), b);
        const p25 = P(2025.5);
        if (p25[2]) {
          const a0 = P(y0);
          const a1 = P(2050);
          const len = Math.hypot(a1[0] - a0[0], a1[1] - a0[1]);
          const m = len < 60 && len > 0 ? 60 / len : 1;
          const pm = (ra: number, dec: number): [number, number, boolean] => {
            const q = project(ra, dec, b);
            return q[2] ? [p25[0] + m * (q[0] - p25[0]), p25[1] + m * (q[1] - p25[1]), true] : q;
          };
          drawMovingPath(ctx, pm, mo, { imageYears: years, backdropYear: by, zoomedIn: near, maxJump: W / 2, font, note: coldNote(st.sel.sp, shownId) });
          const q = pm(...placeAt(mo, by));
          pins.push({ o: st.sel, x: q[0], y: q[1] });
          pathPin = true;
          if (m > 1) {
            // a small note at the bottom edge of the frame, never under a label
            ctx.save();
            ctx.font = `500 10px ${font}`;
            ctx.lineWidth = 3;
            ctx.strokeStyle = '#000';
            ctx.fillStyle = '#8fa6b0';
            ctx.textAlign = 'center';
            const t = `path ${Math.floor(y0)}→2050 drawn ×${m.toFixed(0)} larger than true`;
            ctx.strokeText(t, W / 2, H - 8);
            ctx.fillText(t, W / 2, H - 8);
            ctx.restore();
          }
        }
      }
      // test targets (and a selected object without a path): a pin at the place on the date of the backdrop; zoomed in, an open ring
      ctx.font = `600 12.5px ${font}`;
      for (const o of st.catalog) {
        if (!(o.b || o === st.sel) || (o === st.sel && pathPin)) continue;
        const p = project(...shownPlace(o, shownId, datesOf(o, st.sel)), b);
        if (!p[2]) continue;
        pins.push({ o, x: p[0], y: p[1] });
        const lx = near ? 14 : 9;
        ctx.strokeStyle = '#000';
        ctx.fillStyle = '#ffb454';
        if (near) {
          ctx.lineWidth = 4;
          ctx.beginPath();
          ctx.arc(p[0], p[1], 9, 0, 7);
          ctx.stroke();
          ctx.strokeStyle = '#ffb454';
          ctx.lineWidth = 2;
          ctx.stroke();
          ctx.strokeStyle = '#000';
        } else {
          ctx.lineWidth = 3;
          ctx.beginPath();
          ctx.arc(p[0], p[1], 5, 0, 7);
          ctx.fill();
          ctx.stroke();
        }
        if (view.current.zoom > 1.5 || st.proj === 'moll' || o === st.sel) {
          ctx.lineWidth = 3;
          const cn = coldNote(o.sp, shownId);
          const txt = cn ? `${o.n} · ${cn}` : o.n;
          ctx.strokeText(txt, p[0] + lx, p[1] + 4);
          ctx.fillStyle = '#fff';
          ctx.fillText(txt, p[0] + lx, p[1] + 4);
        }
      }
      // aim frame
      const p = project(st.a.ra, st.a.dec, b);
      if (p[2]) {
        const skyDeg = st.a.fov / 60;
        const truePx = st.proj === 'globe' ? R() * skyDeg * D2R : (mw() / 360) * skyDeg;
        const s = Math.max(truePx, 26);
        ctx.save();
        ctx.translate(p[0], p[1]);
        ctx.strokeStyle = '#6aaaff';
        ctx.lineWidth = 2;
        ctx.shadowColor = '#000';
        ctx.shadowBlur = 6;
        ctx.strokeRect(-s / 2, -s / 2, s, s);
        ctx.beginPath();
        ctx.moveTo(-s, 0);
        ctx.lineTo(-s / 2 - 4, 0);
        ctx.moveTo(s, 0);
        ctx.lineTo(s / 2 + 4, 0);
        ctx.moveTo(0, -s);
        ctx.lineTo(0, -s / 2 - 4);
        ctx.moveTo(0, s);
        ctx.lineTo(0, s / 2 + 4);
        ctx.stroke();
        ctx.shadowBlur = 0;
        ctx.fillStyle = '#6aaaff';
        ctx.font = `700 12px ${font}`;
        ctx.fillText(`${st.a.fov}′`, s / 2 + 6, -s / 2 - 4);
        ctx.restore();
        frame = { x: p[0], y: p[1], s };
      } else frame = null;
    };
    let frame: { x: number; y: number; s: number } | null = null;
    let anim: { t0: number; dur: number; ra0: number; dra: number; de0: number; dde: number; z0: number; zm: number; z1: number; r0?: number } | null = null;
    const loop = () => {
      raf = requestAnimationFrame(loop);
      const now = performance.now();
      if (anim) {
        const t = Math.min(1, (now - anim.t0) / anim.dur);
        const e = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
        view.current.ra = (((anim.ra0 + anim.dra * e) % 360) + 360) % 360;
        view.current.dec = anim.de0 + anim.dde * e;
        if (anim.r0) view.current.roll = anim.r0 * (1 - e);
        // the scale follows a curve in log zoom through zm: out while the globe turns far, in at the end
        view.current.zoom = Math.exp((1 - e) * (1 - e) * anim.z0 + 2 * e * (1 - e) * anim.zm + e * e * anim.z1);
        if (t >= 1) anim = null;
        dirty.current = true;
      }
      // the sky: coarse while the view moves, full resolution (in slices) once it has rested 200 ms or when tiles arrive
      const st = live.current;
      syncZt();
      if (st.proj === 'globe') {
        if (st.survey === 'svs' && R() > STAR_MAP_TO_VISIBLE && HIPS['dss2']) {
          autoVisible.current = true;
          setSurvey('dss2');
        } else if (st.survey === 'dss2' && autoVisible.current && R() < STAR_MAP_TO_VISIBLE * 0.94) {
          autoVisible.current = false;
          setSurvey('svs');
        }
      }
      const sv = { ra: view.current.ra, dec: view.current.dec, roll: view.current.roll ?? 0, R: R(), proj: st.proj, inside: st.inside && st.proj === 'globe', W, H, mz: mv.z, mx: mv.x, my: mv.y };
      const sig = [sv.ra, sv.dec, sv.roll, sv.R, sv.proj, sv.inside, W, H, mv.z, mv.x, mv.y].join();
      const layer = SURVEYS.find((x) => x.id === st.survey)!.layer;
      if (st.survey !== lastLayer) {
        lastLayer = st.survey;
        sky.stale = true;
      }
      let changed = false;
      if (sig !== lastSig) {
        lastSig = sig;
        settling = true;
        settleAt = now + 200;
        changed = sky.frame(sv, layer, true);
      } else if (settling) {
        if (now >= settleAt) settling = false;
      } else changed = sky.frame(sv, layer, false);
      if (changed || dirty.current) {
        dirty.current = false;
        draw();
      }
    };
    // a view kept from an earlier visit that no longer shows the aim (the lab moved it) turns to it
    {
      const p = project(aim.get().ra, aim.get().dec, basis());
      if (!p[2] || p[0] < 0 || p[0] > W || p[1] < 0 || p[1] > H) {
        view.current.ra = aim.get().ra;
        view.current.dec = aim.get().dec;
      }
    }

    // back from another screen with the same view: the kept picture stays sharp while a full pass refreshes it
    {
      const k = sky.shown;
      const v = view.current;
      if (k && k.proj === live.current.proj && !!k.inside === (live.current.inside && k.proj === 'globe') && k.W === W && k.H === H && k.ra === v.ra && k.dec === v.dec && k.R === R()) {
        lastSig = [k.ra, k.dec, k.R, k.proj, !!k.inside, W, H, mv.z, mv.x, mv.y].join();
        sky.stale = true;
      }
    }
    loop();
    const flyTo = (ra: number, dec: number, spanDeg?: number) => {
      if (live.current.proj === 'moll') return;
      const th = separation(ra, dec, view.current.ra, view.current.dec) * D2R;
      const z0 = view.current.zoom;
      const minWH = Math.min(W, H);
      const z1 = spanDeg
        ? Math.max(zoomMin(), Math.min(ZOOM_MAX, (0.42 * minWH) / (spanDeg * D2R) / ((minWH / 2) * 0.9)))
        : z0;
      if (!spanDeg && (inn() ? Math.tan(Math.min(th, 1.5)) * R() < minWH * 0.3 : th < (Math.PI / 2) * 0.9 && R() * Math.sin(th) < minWH * 0.3)) return;
      // the widest view on the way: the whole move fits in it
      const zm = Math.min(z0, z1, Math.max(1.2, 1 / Math.max(1e-6, th)));
      const lz0 = Math.log(z0);
      const lz1 = Math.log(z1);
      const lzm = zm < Math.min(z0, z1) ? 2 * Math.log(zm) - (lz0 + lz1) / 2 : (lz0 + lz1) / 2;
      anim = {
        t0: performance.now(),
        dur: th > 0.3 || Math.abs(lz1 - lz0) > 3 ? 1400 : 900,
        ra0: view.current.ra,
        dra: ((ra - view.current.ra + 540) % 360) - 180,
        de0: view.current.dec,
        dde: dec - view.current.dec,
        z0: lz0,
        zm: lzm,
        z1: lz1,
        r0: view.current.roll ?? 0,
      };
    };
    flyRef.current = flyTo;
    northRef.current = () => {
      // the view turns about its own axis until north is up again
      anim = { t0: performance.now(), dur: 450, ra0: view.current.ra, dra: 0, de0: view.current.dec, dde: 0, z0: Math.log(view.current.zoom), zm: Math.log(view.current.zoom), z1: Math.log(view.current.zoom), r0: ((((view.current.roll ?? 0) + Math.PI) % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI) - Math.PI };
    };

    // the map keeps its centre within the picture, so that it cannot be dragged away
    const clampMap = () => {
      const lx = Math.max(0, (mw() - W) / 2 + 40);
      const ly = Math.max(0, (mw() / 2 - H) / 2 + 40);
      mv.x = Math.max(-lx, Math.min(lx, mv.x));
      mv.y = Math.max(-ly, Math.min(ly, mv.y));
    };
    let drag: { x: number; y: number; ra: number; dec: number; moved: boolean; mx: number; my: number; F: Frame; p0: V3 } | null = null;
    /** the ray through a pixel in the view's own axes (inside: perspective; outside: the sphere's surface, the rim for a pixel beside it) */
    const rayAt = (px: number, py: number, Rv: number): V3 => {
      const sx = (W / 2 - px) / Rv;
      const sy = (H / 2 - py) / Rv;
      if (inn()) {
        const n = Math.sqrt(1 + sx * sx + sy * sy);
        return [sx / n, sy / n, 1 / n];
      }
      const rr = sx * sx + sy * sy;
      if (rr > 1) {
        const s = Math.sqrt(rr);
        return [sx / s, sy / s, 0];
      }
      return [sx, sy, Math.sqrt(1 - rr)];
    };
    const worldOf = (F: Frame, r: V3): V3 => [0, 1, 2].map((i) => r[2] * F.c[i]! + r[0] * F.e[i]! + r[1] * F.n[i]!) as V3;
    const setFrame = (F: Frame) => {
      const v = viewOf(F);
      view.current.ra = v.ra;
      view.current.dec = v.dec;
      view.current.roll = v.roll;
    };
    c.onpointerdown = (e) => {
      c.setPointerCapture(e.pointerId);
      const rc = c.getBoundingClientRect();
      const F0 = basis();
      const p0 = worldOf(F0, rayAt(e.clientX - rc.left, e.clientY - rc.top, R()));
      drag = { x: e.clientX, y: e.clientY, ra: view.current.ra, dec: view.current.dec, moved: false, mx: mv.x, my: mv.y, F: F0, p0 };
      anim = null;
    };
    c.onpointermove = (e) => {
      if (!drag) return;
      const dx = e.clientX - drag.x;
      const dy = e.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
      if (drag.moved && live.current.proj === 'moll' && mv.z > 1) {
        mv.x = drag.mx + dx;
        mv.y = drag.my + dy;
        clampMap();
        dirty.current = true;
      } else if (drag.moved && live.current.proj === 'globe') {
        // a free turn like a trackball: the place grabbed stays under the cursor, there is no pole to wind round (no yaw/pitch)
        const rc = c.getBoundingClientRect();
        const u = worldOf(drag.F, rayAt(e.clientX - rc.left, e.clientY - rc.top, R()));
        setFrame(turnFrame(drag.F, u, drag.p0));
        dirty.current = true;
      }
    };
    c.onpointerup = (e) => {
      const r = c.getBoundingClientRect();
      const px = e.clientX - r.left;
      const py = e.clientY - r.top;
      if (drag && !drag.moved) {
        // the pins where they were drawn (the place on the backdrop's date), then the catalogue dots the same way
        let near: Obj | null = null;
        let bd = 12;
        for (const q of pins) {
          const d = Math.hypot(q.x - px, q.y - py);
          if (d < bd) {
            bd = d;
            near = q.o;
          }
        }
        if (near) {
          setSel(near);
          const [ra, dec] = epochPlace(near);
          aim.set({ ra, dec }, 'map');
        } else {
          const s = unproject(px, py);
          if (s && live.current.covOn) {
            // the Coverage layer is on: the click opens the card of the square under it, and the aim frame takes the square (20′)
            const id = pickCell(coverageNow(), s[0], s[1], cellPx());
            const p = parseId(id)!;
            setSel(null);
            setCellId(id);
            aim.set({ ...cellCentre(p.band, p.col), fov: 20 }, 'map');
          } else if (s) {
            setSel(null);
            aim.set({ ra: s[0], dec: s[1] }, 'map');
          }
        }
      }
      drag = null;
    };
    c.ondblclick = (e) => {
      const r = c.getBoundingClientRect();
      const f = frame;
      if (f && Math.abs(e.clientX - r.left - f.x) < f.s && Math.abs(e.clientY - r.top - f.y) < f.s) showAreaRef.current();
    };
    // the zoom slider and its buttons: the same zoom as the wheel. With an object selected the globe first turns so that it is in the centre and the zoom is on it; with
    // nothing selected the zoom is on the centre of the picture
    let ztSent = -1;
    let turnToken = 0;
    const zoomAbout = (k: number) => {
      anim = null;
      // the zoom is about the aim frame (the field, or the object the aim sits on), which the drag centres too
      const target: [number, number] = [aim.get().ra, aim.get().dec];
      if (live.current.proj === 'moll') {
        const z1 = Math.max(1, Math.min(40, mv.z * k));
        const kz = z1 / mv.z;
        mv.x = -(W / 2 - mcx()) * kz;
        mv.y = -(H / 2 - mcy()) * kz;
        mv.z = z1;
        {
          const pp = project(target[0], target[1], basis());
          if (pp[2]) {
            mv.x += W / 2 - pp[0];
            mv.y += H / 2 - pp[1];
          }
        }
        if (mv.z <= 1.0001) {
          mv.x = 0;
          mv.y = 0;
        }
        clampMap();
      } else {
        const F = basis();
        // the selected object, else the aim frame, is turned to the centre (smoothly, 0.35 s), and the zoom is about the centre
        const c = frameOf(target[0], target[1], 0).c;
        const cosA = Math.max(-1, Math.min(1, dot3(F.c, c)));
        const th = Math.acos(cosA);
        if (th > 1e-4) {
          const token = ++turnToken;
          const t0 = performance.now();
          const step = () => {
            if (token !== turnToken) return;
            const e = Math.min(1, (performance.now() - t0) / 350);
            const ee = e * e * (3 - 2 * e);
            const sa = Math.sin((1 - ee) * th) / Math.sin(th);
            const sb = Math.sin(ee * th) / Math.sin(th);
            const v: V3 = [sa * F.c[0] + sb * c[0], sa * F.c[1] + sb * c[1], sa * F.c[2] + sb * c[2]];
            setFrame(turnFrame(F, F.c, v));
            dirty.current = true;
            if (e < 1) requestAnimationFrame(step);
          };
          requestAnimationFrame(step);
        }
        view.current.zoom = Math.max(zoomMin(), Math.min(ZOOM_MAX, view.current.zoom * k));
      }
      dirty.current = true;
    };
    const ztNow = (): number => {
      const [lo, hi, z] = live.current.proj === 'moll' ? [1, 40, mv.z] : [zoomMin(), ZOOM_MAX, view.current.zoom];
      return Math.max(0, Math.min(1, Math.log(z / lo) / Math.log(hi / lo)));
    };
    zoomApi.current = {
      by: (k) => zoomAbout(k),
      set: (t) => {
        const [lo, hi, z] = live.current.proj === 'moll' ? [1, 40, mv.z] : [zoomMin(), ZOOM_MAX, view.current.zoom];
        zoomAbout((lo * (hi / lo) ** t) / z);
      },
    };
    syncZt = () => {
      const t = ztNow();
      if (Math.abs(t - ztSent) > 0.003) {
        ztSent = t;
        setZt(t);
      }
    };
    c.onwheel = (e) => {
      e.preventDefault();
      if (live.current.proj === 'moll') {
        // the map zooms about the cursor
        const r = c.getBoundingClientRect();
        const qx = e.clientX - r.left;
        const qy = e.clientY - r.top;
        const dpx = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? H : 1);
        const z1 = Math.max(1, Math.min(40, mv.z * Math.max(0.5, Math.min(2, Math.exp(-dpx * 0.0022)))));
        const kz = z1 / mv.z;
        mv.x = qx - W / 2 - (qx - mcx()) * kz;
        mv.y = qy - H / 2 - (qy - mcy()) * kz;
        mv.z = z1;
        if (mv.z <= 1.0001) {
          mv.x = 0;
          mv.y = 0;
        }
        clampMap();
        dirty.current = true;
        return;
      }
      anim = null;
      // a mouse-wheel notch (100 px) zooms ×1.25: twenty notches about ×90; a trackpad's small steps zoom in proportion
      const px = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? H : 1);
      const k = Math.max(0.5, Math.min(2, Math.exp(-px * 0.0022)));
      // the zoom is about the cursor: the place under it stays under it
      const rc = c.getBoundingClientRect();
      const qx = e.clientX - rc.left;
      const qy = e.clientY - rc.top;
      const F = basis();
      const p = worldOf(F, rayAt(qx, qy, R()));
      view.current.zoom = Math.max(zoomMin(), Math.min(ZOOM_MAX, view.current.zoom * k));
      const sx = (W / 2 - qx) / R();
      const sy = (H / 2 - qy) / R();
      if (inn() || sx * sx + sy * sy < 0.97) setFrame(turnFrame(F, worldOf(F, rayAt(qx, qy, R())), p));
      dirty.current = true;
    };
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      offTile();
      offEqui();
    };
  }, []);
  // an address changed without a reload (#/explore?ra=..&dec=..): the aim goes there and the globe flies to it
  useEffect(() => {
    const onHash = () => {
      const h = hashAim();
      if (h) flyRef.current(h.ra, h.dec, 50);
      if (new URLSearchParams(location.hash.split('?')[1] ?? '').get('cov') === '1') setCovOn(true);
    };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  const showAreaRef = useRef<() => void>(() => {});
  const northRef = useRef<() => void>(() => {});
  /** switches the camera; the zoom keeps the same angular scale at the centre, within the limits of the new camera */
  const setInsideCam = (v: boolean) => {
    live.current.inside = v;
    view.current.zoom = Math.max(v ? ZOOM_MIN_INSIDE : ZOOM_MIN, view.current.zoom);
    dirty.current = true;
    setInside(v);
  };

  const list = useMemo(() => {
    let L = catalog;
    if (filter === 'bench') L = L.filter((o) => o.b);
    else if (filter === 'bd') L = L.filter((o) => o.k === 'bd');
    else if (filter === 'fast') L = L.filter((o) => o.k === 'fast');
    else if (filter === 'var') L = L.filter((o) => o.k === 'mira' || o.k === 'young' || o.k === 'var');
    else if (filter === 'demo' || filter === 'small') L = [];
    const q = query.trim().toLowerCase();
    if (q) {
      const m = q.match(/^(.+?)[,;]\s*(.+)$/);
      const ra = m ? parseRA(m[1]!) : null;
      const de = m ? parseDec(m[2]!) : null;
      if (ra !== null && de !== null)
        L = [...L].sort((x, y) => separation(x.ra, x.dec, ra, de) - separation(y.ra, y.dec, ra, de)).slice(0, 200);
      else L = L.filter((o) => o.n.toLowerCase().includes(q) || o.c.toLowerCase().includes(q) || o.sp.toLowerCase().includes(q));
    }
    return L;
  }, [catalog, filter, query]);
  const ql = query.trim().toLowerCase();
  const fixedList = (filter === 'demo' ? DEMOS : filter === 'small' ? SMALL : []).filter((d) => !ql || d.n.toLowerCase().includes(ql) || d.what.toLowerCase().includes(ql));
  const [knownCount, setKnownCount] = useState(0);
  // a name the snapshot does not hold: ask SIMBAD (debounced), and offer it at the top of the list
  const [named, setNamed] = useState<Named | null>(null);
  const [asking, setAsking] = useState<'' | 'asking' | 'none' | 'slow'>('');
  const isCoord = /^(.+?)[,;]\s*(.+)$/.test(ql) && /\d/.test(ql);
  useEffect(() => {
    setNamed(null);
    setAsking('');
    if (filter === 'demo' || filter === 'small' || ql.length < 3 || isCoord) return;
    if (list.some((o) => o.n.toLowerCase() === ql)) return;
    const ctl = new AbortController();
    const t = setTimeout(() => {
      setAsking('asking');
      void sesame(query.trim(), ctl.signal)
        .then((n) => {
          setNamed(n);
          setAsking(n ? '' : 'none');
        })
        .catch(() => setAsking((a) => (a === 'asking' ? 'slow' : a)));
    }, 450);
    const give = setTimeout(() => ctl.abort(), 20000);
    return () => {
      clearTimeout(t);
      clearTimeout(give);
      ctl.abort();
    };
  }, [ql, filter, list.length]);
  const namedNew = named && !list.some((o) => separation(o.ra, o.dec, named.ra, named.dec) < 0.01);
  const total = filter === 'demo' ? fixedList.length : filter === 'small' ? fixedList.length + knownCount : list.length + (namedNew ? 1 : 0);
  const pages = Math.max(1, Math.ceil(list.length / per));
  const pg = Math.min(page, pages);
  const slice = list.slice((pg - 1) * per, pg * per);
  const nBench = catalog.filter((o) => o.b).length;

  /** Selects an object and turns the globe to it; `zoom`: also scale the view to the object's path (Show on map). */
  const select = (o: Obj, zoom = false) => {
    setSel(o);
    const [ra, dec] = epochPlace(o);
    aim.set({ ra, dec }, 'catalog');
    if (zoom && proj === 'moll') {
      // a map has no zoom: the globe shows the object
      live.current.proj = 'globe';
      setProj('globe');
    }
    flyRef.current(ra, dec, zoom ? spanOf(o, a.fov) : undefined);
  };
  // a link that names an object (#/explore?sel=<name>&path=1, e.g. the landing's "Watch a star move"): select it once the catalogue
  // is in, and with path=1 fly to it with its path drawn, as Show on map does
  const linked = useRef(false);
  useEffect(() => {
    if (linked.current || !catalog.length) return;
    const q = new URLSearchParams(location.hash.split('?')[1] ?? '');
    const name = q.get('sel');
    if (!name) return;
    const o = catalog.find((x) => x.n.toLowerCase() === name.toLowerCase());
    if (!o) return;
    linked.current = true;
    // opened from a link (the landing's "Watch a star move"): the globe shows about 50° of the sky around the object, not the 5′ frame; its path is drawn larger than true
    setTimeout(() => {
      select(o, false);
      flyRef.current(...epochPlace(o), 50);
    }, 300);
  }, [catalog]);

  /** The lab at the object's place during the SPHEREx survey (where it is in the pictures the lab shows). */
  const openObj = (o: Obj) => {
    const [ra, dec] = epochPlace(o);
    aim.set({ ra, dec }, 'catalog');
    openLab(ra, dec, a.fov);
  };
  /** Show area: the field under the aim frame. When the aim is on a catalogue object (its J2000 place, its place on the backdrop,
   *  or its survey-epoch place) the field opens where the object is during the survey, as "Open in lab" does. */
  const showArea = () => {
    const cur = aim.get();
    const on = (o: Obj) => {
      const tol = 3 / 3600;
      return (
        separation(...epochPlace(o), cur.ra, cur.dec) < 2 / 3600 ||
        separation(o.ra, o.dec, cur.ra, cur.dec) < tol ||
        separation(...shownPlace(o, survey, datesOf(o, sel)), cur.ra, cur.dec) < tol
      );
    };
    const hit = sel && on(sel) ? sel : catalog.find((o) => o.pmra != null && on(o));
    if (hit) openObj(hit);
    else openLab(cur.ra, cur.dec, cur.fov);
  };
  showAreaRef.current = showArea;
  const goAim = () => {
    const ra = parseRA(raIn);
    const dec = parseDec(decIn);
    if (ra === null || dec === null) return;
    if (Math.abs(ra - a.ra) < 1e-6 && Math.abs(dec - a.dec) < 1e-6) return;
    setSel(null);
    aim.set({ ra, dec }, 'input');
    flyRef.current(ra, dec);
  };
  const motion = (o: Obj) => {
    if (o.k === 'mira' || o.k === 'var') return 'Variable';
    if (o.pmra == null || o.pmdec == null) return 'Motion: —';
    const pm = Math.hypot(o.pmra, o.pmdec);
    return `Motion: ${pm >= 1000 ? `${(pm / 1000).toFixed(2)} ″/yr` : `${Math.round(pm)} mas/yr`}`;
  };
  const pagerNums = [...new Set([1, 2, 3, pg - 1, pg, pg + 1, pages])].filter((p) => p >= 1 && p <= pages).sort((x, y) => x - y);
  const sv = SURVEYS.find((x) => x.id === survey)!;
  // the selected object on the backdrop shown: the date its pin stands at, and the dates of the archive images of its place
  const selDates = sel && moving(sel) ? imageDates(sel.n, sel.ra, sel.dec) : undefined;
  const selYear = sel ? backdropYear(shownLayer, selDates) : 0;
  const selImage = selDates?.find((x) => x.backdrop === shownLayer);

  return (
    <div class="screen">
      <div class="globe-wrap">
        <canvas ref={cv} aria-label="Sky globe" data-testid="sky" />
        <div class="zoomctl dark-glass" role="group" aria-label="Zoom" data-testid="zoom-slider">
          <button aria-label="Zoom in" title="Zoom in" onClick={() => zoomApi.current.by(1.5)}>
            +
          </button>
          <div class="zs">
            <input
              type="range"
              min="0"
              max="1000"
              value={Math.round(zt * 1000)}
              aria-label="Zoom, from the whole sphere to the deepest view"
              onInput={(e) => zoomApi.current.set(Number((e.target as HTMLInputElement).value) / 1000)}
            />
          </div>
          <button aria-label="Zoom out" title="Zoom out" onClick={() => zoomApi.current.by(1 / 1.5)}>
            −
          </button>
        </div>
        <div class="map-ui map-bar" data-testid="map-bar">
          <div class="aimrow dark-glass" role="group" aria-label="Aim">
            <label class="fld" for="gRa">
              <span>RA</span>
              <input
                id="gRa"
                class={`inp coord ${parseRA(raIn) === null ? 'bad' : ''}`}
                spellcheck={false}
                value={raIn}
                onInput={(e) => setRaIn((e.target as HTMLInputElement).value)}
                onKeyDown={(e) => e.key === 'Enter' && goAim()}
                onBlur={goAim}
              />
            </label>
            <label class="fld" for="gDec">
              <span>Dec</span>
              <input
                id="gDec"
                class={`inp coord ${parseDec(decIn) === null ? 'bad' : ''}`}
                spellcheck={false}
                value={decIn}
                onInput={(e) => setDecIn((e.target as HTMLInputElement).value)}
                onKeyDown={(e) => e.key === 'Enter' && goAim()}
                onBlur={goAim}
              />
            </label>
            <label class="fld" for="gFov">
              <span>FOV</span>
              <select id="gFov" class="inp fovsel" value={String(a.fov)} onChange={(e) => aim.set({ fov: Number((e.target as HTMLSelectElement).value) }, 'fov')} title="Field of view of the frame">
                {SIZES.map((s) => (
                  <option key={s} value={String(s)}>
                    {s}′
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div class="seg" role="group" aria-label="Projection">
            <button class={proj === 'globe' ? 'on' : ''} onClick={() => setProj('globe')}>
              <span dangerouslySetInnerHTML={{ __html: icon('globe', 16) }} style={{ display: 'contents' }} />
              3D globe
            </button>
            <button class={proj === 'moll' ? 'on' : ''} onClick={() => setProj('moll')}>
              <span dangerouslySetInnerHTML={{ __html: icon('map-trifold', 16) }} style={{ display: 'contents' }} />
              Mollweide map
            </button>
          </div>
          {proj === 'globe' && (
            <div class="seg sm" role="group" aria-label="Camera" data-testid="cam">
              <button title="Turn the view until north is up" aria-label="North up" data-testid="north-up" onClick={() => northRef.current()}>
                N↑
              </button>
              <button class={inside ? 'on' : ''} aria-pressed={inside} title="The sky seen from the centre of the sphere" onClick={() => setInsideCam(true)}>
                Inside
              </button>
              <button class={!inside ? 'on' : ''} aria-pressed={!inside} title="The sphere seen from outside" onClick={() => setInsideCam(false)}>
                Outside
              </button>
            </div>
          )}
          <div class="seg sm" role="group" aria-label="Sky background">
            {SURVEYS.filter((s) => s.id !== 'svs' || svsOn).map((s) => (
              <button key={s.id} class={s.id === survey ? 'on' : ''} aria-pressed={s.id === survey} title={`${s.name}: ${s.note}`} onClick={() => {
                autoVisible.current = false;
                setSurvey(s.id);
              }}>
                {LAYER_WORD[s.id] ?? s.name}
              </button>
            ))}
          </div>
          <div class="seg sm" role="group" aria-label="Grid of squares">
            <button class={covOn ? 'on' : ''} aria-pressed={covOn} data-testid="cov-toggle" title="The grid of 20′ squares over the sky: searched by the machine, viewed by people, has finds, being searched. Click a square for its card." onClick={() => setCovOn(!covOn)}>
              <span dangerouslySetInnerHTML={{ __html: icon('scan', 14) }} style={{ display: 'contents' }} />
              Coverage
            </button>
          </div>
        </div>
        {covOn && cellId && (
          <div class="cov-card dark-glass" data-testid="cov-card" role="dialog" aria-label={`Square ${cellId}`}>
            <div class="cov-head">
              <b>Square {cellId}</b>
              <button class="cov-x" aria-label="Close the card" onClick={() => setCellId(null)}>
                ×
              </button>
            </div>
            {cell && cell.id === cellId ? (
              <>
                <div class="cov-state" data-testid="cov-state">
                  <i class="cov-sw" style={{ background: covKind(cell) ? KIND_COLOUR[covKind(cell)!] : 'transparent' }} />
                  {covStateWord(cell)}
                </div>
                <dl class="cov-facts">
                  <dt>Finds</dt>
                  <dd data-testid="cov-finds">{cell.state === 'searched' || cell.n_finds ? cell.n_finds.toLocaleString('en-US') : '—'}</dd>
                  <dt>Bar</dt>
                  <dd data-testid="cov-bar">{barText(cell.bar) || (cell.state === 'searched' ? 'not kept' : '—')}</dd>
                  <dt>Viewed by</dt>
                  <dd data-testid="cov-viewers">{cell.viewers === 1 ? '1 person' : `${cell.viewers.toLocaleString('en-US')} people`}</dd>
                  <dt>Centre</dt>
                  <dd>{shortCoords(cell.ra, cell.dec)}</dd>
                  {cell.searched_at && (
                    <>
                      <dt>Searched</dt>
                      <dd>{cell.searched_at.slice(0, 10)}</dd>
                    </>
                  )}
                </dl>
                <div class="cov-btns">
                  <button class="btn sm" data-testid="cov-open" onClick={() => openLab(cell.open.ra, cell.open.dec, cell.open.fov)}>
                    Open in the lab
                  </button>
                  <button
                    class="btn sm primary"
                    data-testid="cov-take"
                    title="Open the lab with the task: look through this square"
                    onClick={() => {
                      location.hash = formatHash({ screen: 'lab', params: { ra: cell.open.ra.toFixed(5), dec: cell.open.dec.toFixed(5), fov: String(cell.open.fov), task: 'cell', cell: cell.id } });
                    }}
                  >
                    Take this square
                  </button>
                </div>
              </>
            ) : (
              <div class="cov-state faint">Asking for the square…</div>
            )}
          </div>
        )}
        <div class="map-bl">
          {(count || sel) && (
            <div class="map-read">
              {count &&
                (count.n ? (
                  <span style={{ color: 'var(--good)' }}>
                    {count.n.toLocaleString('en-US')} exposures (DR1 {count.by['dr1'] ?? 0}, QR3 {count.by['qr3'] ?? 0})
                  </span>
                ) : (
                  <span class="faint">no SPHEREx exposures here</span>
                ))}
              {sel && (
                <>
                  {count ? ' · ' : ''}
                  <button class="lnk" onClick={() => openObj(sel)} title="Open this field in the spectral lab">
                    Open {sel.n.length > 26 ? `${sel.n.slice(0, 24)}…` : sel.n} in the lab
                  </button>
                </>
              )}
            </div>
          )}
          {sel && moving(sel) && (
            <div class="map-read map-dates" data-testid="globe-dates">
              On this picture: <b>{sel.n.length > 26 ? `${sel.n.slice(0, 24)}…` : sel.n}</b> in {selYear.toFixed(1)}
              {selImage ? ` (${selImage.name} image)` : shownLayer === 'svs' ? ' (Gaia epoch)' : shownLayer === 'spherex' ? ' (SPHEREx survey)' : shownLayer === 'dust' ? ' (SPHEREx survey; no stars on the dust map)' : ' (middle of the survey)'}
              {selDates === undefined ? (
                <span class="faint"> · asking IRSA for the dates of the archive images…</span>
              ) : selDates ? (
                <span class="faint"> · images: {selDates.map((x) => `${Math.floor(x.year)} ${x.name}`).join(' · ')}</span>
              ) : (
                <span class="faint"> · IRSA gave no image dates; survey middles used</span>
              )}
            </div>
          )}
          {covOn && (
            <div class="map-read cov-legend" data-testid="cov-legend">
              {(['searched', 'viewed', 'finds', 'searching'] as Kind[]).map((k) => (
                <span key={k} class="cov-key" title={KIND_WORD[k]}>
                  <i class={`cov-sw${k === 'searching' ? ' dash' : ''}`} style={{ background: k === 'searching' ? 'transparent' : KIND_COLOUR[k], borderColor: KIND_COLOUR[k] }} />
                  {KIND_SHORT[k]}
                </span>
              ))}
              <span class="faint" data-testid="cov-count">{covLine(covTick)}</span>
              <span class="cov-do" data-testid="cov-do">click a square to take it</span>
            </div>
          )}
          <div class="map-credit" data-testid="map-credit">
            {LAYER_CAPTION[shownLayer]}
          </div>
          <div class="map-help" title={`Background: ${sv.note} · IRSA / CDS HiPS`}>
          <b>Drag</b> to rotate · <b>scroll</b> to zoom · <b>click</b> to aim · <b>double-click</b> the frame to open it in the lab. Orange
          ★ are test targets.
        </div>
        </div>
        <button class="btn primary big map-open" onClick={showArea} title="Open the field under the frame in the spectral lab">
          <span dangerouslySetInnerHTML={{ __html: icon('scan', 18) }} style={{ display: 'contents' }} />
          Show area
        </button>
      </div>
      <div class={`cat-drawer${catOpen ? ' open' : ''}`} data-testid="cat-drawer">
        <button class="cat-handle" onClick={() => setCatOpen(!catOpen)} aria-expanded={catOpen} title={catOpen ? 'Slide the catalogue away' : 'Slide the catalogue out'}>
          <span class="cat-arrow">{catOpen ? '›' : '‹'}</span>
          <span class="cat-word">Catalogue</span>
        </button>
      <aside class="catalog glass">
        <div class="cat-top">
          <input
            class="inp search"
            placeholder="Search a name or coordinates"
            autocomplete="off"
            value={query}
            onInput={(e) => {
              setQuery((e.target as HTMLInputElement).value);
              setPage(1);
            }}
          />
          <div class="chips">
            {(
              [
                ['all', 'SIMBAD snapshot'],
                ['bench', `Test targets (${nBench})`],
                ['bd', 'Brown dwarfs'],
                ['fast', 'Fast stars'],
                ['var', 'Variables'],
                ['demo', `Demo fields (${DEMOS.length})`],
                ['small', 'Comets / Asteroids'],
              ] as const
            ).map(([k, l]) => (
              <button
                key={k}
                class={`chip ${filter === k ? 'on' : ''}`}
                onClick={() => {
                  setFilter(k);
                  setPage(1);
                }}
                data-testid={`chip-${k}`}
              >
                {l}
              </button>
            ))}
          </div>
        </div>
        <div class="cat-list">
          {filter !== 'demo' && filter !== 'small' && !slice.length && !namedNew && catalog.length > 0 && (
            <div class="empty">
              {asking === 'asking' ? 'Not in this list; asking SIMBAD for that name…' : asking === 'none' ? 'SIMBAD knows no object of that name.' : asking === 'slow' ? 'SIMBAD did not answer in time.' : 'Nothing in this list.'} Coordinates work too, e.g. “17h57m48s, +04d41m”.
            </div>
          )}
          {(filter === 'demo' || filter === 'small') && ql && !fixedList.length && filter === 'demo' && <div class="empty">No demo field matches “{query.trim()}”.</div>}
          {(filter === 'demo' || filter === 'small') &&
            fixedList.map((d) => (
              <div key={d.n} class="row" onClick={() => {
                setSel(null);
                aim.set({ ra: d.ra, dec: d.dec, fov: d.fov }, 'catalog');
              }}>
                <div class="nm">
                  <span class="t" title={d.n}>{d.n}</span>
                  <span class="badge orange">{d.film ? 'film' : 'field'}</span>
                </div>
                <div class="meta" title={d.what}>
                  <span class="co">{shortCoords(d.ra, d.dec)} · {d.fov}′</span>
                  <span class="mv">{d.what}</span>
                </div>
                <div class="gobtns">
                  <button
                    class="btn sm primary go ico"
                    title="Show on map"
                    aria-label={`Show ${d.n} on the map`}
                    onClick={(e) => {
                      e.stopPropagation();
                      setSel(null);
                      aim.set({ ra: d.ra, dec: d.dec, fov: d.fov }, 'catalog');
                      flyRef.current(d.ra, d.dec, (d.fov / 60) * 1.5);
                    }}
                  >
                    {ROW_ICON('target')}
                  </button>
                  <button
                    class="btn sm go ico"
                    title="Open in lab"
                    aria-label={`Open ${d.n} in the lab`}
                    onClick={(e) => {
                      e.stopPropagation();
                      location.hash = formatHash({
                        screen: 'lab',
                        params: { ra: d.ra.toFixed(5), dec: d.dec.toFixed(5), fov: String(d.fov), ...(d.film ? { film: String(d.film) } : {}) },
                      });
                    }}
                  >
                    {ROW_ICON('chart-line-up')}
                  </button>
                </div>
              </div>
            ))}
          {filter === 'small' && (
            <div class="known-head">
              <span class="mono-s">Every comet and asteroid of the JPL Small-Body Database: Show on map draws its path since April 2025</span>
            </div>
          )}
          {filter === 'small' && (
            <KnownList
              query={query.trim()}
              onCount={setKnownCount}
              selected={bodyPath?.body.spkid ?? null}
              today={bodyPath}
              onShow={(p) => {
                setBodyPath(p);
                aim.set({ ra: p.today.ra, dec: p.today.dec, fov: 10 }, 'catalog');
                flyRef.current(p.today.ra, p.today.dec, 30);
              }}
              onOpen={(_b, c) => {
                location.hash = formatHash({
                  screen: 'lab',
                  params: { ra: c.ra.toFixed(5), dec: c.dec.toFixed(5), fov: '10', film: ((c.mjd0 + c.mjd1) / 2).toFixed(3) },
                });
              }}
            />
          )}
          {filter !== 'demo' && filter !== 'small' && namedNew && named && (
            <div class="row named">
              <div class="nm">
                <span class="t" title={named.n}>{named.n}</span>
                <span class="badge">SIMBAD{named.type ? ` · ${named.type}` : ''}</span>
              </div>
              <div class="meta">
                <span class="co">{shortCoords(named.ra, named.dec)}</span>
                <span class="mv">{Math.hypot(named.pmra, named.pmdec) >= 50 ? `Motion: ${(Math.hypot(named.pmra, named.pmdec) / 1000).toFixed(2)} ″/yr` : 'found by name in SIMBAD'}</span>
              </div>
              <div class="gobtns">
                <button
                  class="btn sm primary go ico"
                  title="Show on map"
                  aria-label={`Show ${named.n} on the map`}
                  onClick={() => {
                    aim.set({ ra: named.ra, dec: named.dec }, 'catalog');
                    flyRef.current(named.ra, named.dec, 2);
                  }}
                >
                  {ROW_ICON('target')}
                </button>
                <button
                  class="btn sm go ico"
                  title="Open in lab"
                  aria-label={`Open ${named.n} in the lab`}
                  onClick={() => {
                    location.hash = formatHash({ screen: 'lab', params: { ra: named.ra.toFixed(5), dec: named.dec.toFixed(5), fov: '5' } });
                  }}
                >
                  {ROW_ICON('chart-line-up')}
                </button>
              </div>
            </div>
          )}
          {filter !== 'demo' &&
            filter !== 'small' &&
            slice.map((o) => {
              const { ra, dec } = o;
              return (
                <div
                  key={o.n}
                  class={`row ${sel === o ? 'on' : ''}`}
                  onClick={() => select(o)}
                  onDblClick={() => openObj(o)}
                  data-testid={o.b ? `target-${o.n}` : undefined}
                >
                  <div class="nm">
                    {o.b ? <span title="Test target of this project">★</span> : null}
                    <span class="t" title={o.n}>
                      {o.n}
                    </span>
                    <span class={`badge ${KIND[o.k]?.cls ?? ''}`}>{o.b ? (TARGET_CLASS[o.n] ?? o.c) : o.c}</span>
                  </div>
                  <div class="meta">
                    <span class="co">{shortCoords(ra, dec)}</span>
                    <span class="mv">{motion(o).replace(/^Motion:\s*/, '')}</span>
                  </div>
                  <div class="gobtns">
                    <button
                      class="btn sm primary go ico"
                      title="Show on map: fly to it, zoom and draw its path"
                      aria-label={`Show ${o.n} on the map`}
                      onClick={(e) => {
                        e.stopPropagation();
                        select(o, true);
                      }}
                    >
                      {ROW_ICON('target')}
                    </button>
                    <button
                      class="btn sm go ico"
                      title="Open in lab"
                      aria-label={`Open ${o.n} in the lab`}
                      onClick={(e) => {
                        e.stopPropagation();
                        openObj(o);
                      }}
                    >
                      {ROW_ICON('chart-line-up')}
                    </button>
                  </div>
                </div>
              );
            })}
        </div>
        <div class="pager">
          <button class="pg" disabled={pg === 1} onClick={() => setPage(pg - 1)}>
            ‹
          </button>
          {pagerNums.map((p, i) => (
            <>
              {i > 0 && p - pagerNums[i - 1]! > 1 && <span class="faint">…</span>}
              <button key={p} class={`pg ${p === pg ? 'on' : ''}`} onClick={() => setPage(p)}>
                {p}
              </button>
            </>
          ))}
          <button class="pg" disabled={pg === pages} onClick={() => setPage(pg + 1)}>
            ›
          </button>
          <select
            title="Objects per page"
            value={String(per)}
            onChange={(e) => {
              setPer(Number((e.target as HTMLSelectElement).value));
              setPage(1);
            }}
          >
            {[25, 50, 100].map((v) => (
              <option value={String(v)}>{v} / page</option>
            ))}
          </select>
          <span class="cnt">Total: {total.toLocaleString('en-US')}</span>
        </div>
      </aside>
      </div>
    </div>
  );
}
