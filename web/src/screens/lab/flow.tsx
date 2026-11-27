// The product-flow pieces of the lab (product spec §5 Lab): Known objects, marks, the ruler. One hook, `useLabFlow`, which lab.tsx
// calls; it returns what the lab draws on its overlay, what it puts over the picture and in the Controls panel, and its status line.
import { formatHash, parseHash } from '../../lib/route';
import { CHECK_GLYPH, CHECK_WORD, checksVerdict, useMarkChecks, useMarkVerdicts } from './markchecks';
import type { ComponentChildren } from 'preact';
import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import {
  addMark,
  allMarks,
  fieldKey,
  MARK_KINDS,
  MARK_LOOKS,
  LEVEL_OF_KIND,
  type MarkKind,
  marksSyncNote,
  lastMarkEffect,
  type MarkEffect,
  onMarks,
  removeMark,
  updateMark,
  type Mark,
  type MarkLevel,
} from '../../lib/marks';
import {
  catalogueAt,
  drawKnown,
  followKnown,
  KNOWN_OFF,
  knownStatus,
  knownUrl,
  matchKnown,
  type KnownDrawEnv,
  type KnownMatch,
  type KnownObject,
  type KnownState,
} from './known';
import { findPeaks, type Peak } from './detect';
import { rulerOf, rulerWord, type RulerPoint } from './ruler';
import { awardLine, focusKind, taskOfParams, taskTrack, useLabTask, type NextTask } from './task';
import { FindingClip, MoverView, StampsView } from './taskviews';
import { isBlindPlace } from './trackplace';
import type { Anomaly, Source } from '../spectrum';
import { FIELD_ASKING, followFieldKnown, heldAt, KNOWN_SOURCES, placeAt, withFindJpl, withFindKnown, withFindStatus, type FieldKnown, type Held } from './known';
import { atEpoch, SURVEY_EPOCH } from '../../lib/sky';
import { clearDo, pendingDo, useSignIn } from './signin';
import { useRetry, type SearchInfo } from './progress';
import './flow.css';

export interface FlowGrid {
  ra0: number;
  dec0: number;
  step_arcsec: number;
  n: number;
}

export interface FlowArgs {
  ra: number;
  dec: number;
  fov: number;
  /** the grid of what is on show (the field, or in Days the visits of the pass); null until the field is known */
  grid: FlowGrid | null;
  /** the dates on show, MJD: every pass of the field, or in Days every visit of the pass */
  epochs: number[];
  /** the date of the frame on show now */
  epochNow: number | null;
  /** the index of the pass on show (Months), null in Days */
  pass: number | null;
  /** one picture (not a wall of tiles): marks and the ruler are put there */
  single: boolean;
  /** the picture's side in CSS px */
  side: number;
  known: boolean;
  /** the People's marks layer is on */
  people: boolean;
  /** the service's key of the field on show (null until it is known) */
  fieldKey: string | null;
  /** the lab's address parameters, and how another lab address is opened */
  params: Record<string, string>;
  open: (params: Record<string, string>) => void;
  /** the passes of the field: index and a short date ("Sep ’25") */
  passes: { index: number; label: string }[];
  /** a small JPEG of the picture as it is now (a data URL), null when it cannot be made */
  thumb: () => string | null;
  /** the passes are being blinked (Months scale, Blink mode): the two pass pickers of the playback bar act */
  blinkable: boolean;
  /** the field is a grid square or one of its quarters (lab/square.tsx): its outline, its status words, the count for a task */
  square: {
    outline: [number, number][] | null;
    line: string;
    canCount: boolean;
    why: string;
    confirm: (result?: 'nothing' | 'marks') => Promise<boolean>;
  } | null;
  /** the lab's selected object (in a task: the object in question) */
  sel: Source | null;
  /** the mover tracks the search of the field found: places per pass */
  tracks: FlowTrack[];
  /** the anomalies the search of the field found */
  anomalies: Anomaly[];
  /** changes address parameters of the lab; opens a tab of its panel; selects an anomaly or a track by its number */
  set: (params: Record<string, string | null>) => void;
  setTab: (tab: 'ctl' | 'obj' | 'spec' | 'cand') => void;
  pickAnomaly: (n: number) => void;
  pickTrack: (i: number) => void;
  /** holds one frame on the picture: the visit `i` in Days, the pass `i` in Months */
  showFrame: (i: number) => void;
  /** holds the pass nearest a date (MJD) on the picture */
  showPass: (mjd: number) => void;
  /** holds the visit of the pass on show (Days) nearest a date (MJD) */
  showVisit: (mjd: number) => void;
  /** the visit on show in Days (null in Months) */
  visit: number | null;
  /** in Days: the index of the pass whose visits play (a mark put there belongs to it), null in Months */
  daysPass: number | null;
  /** the selected find's own catalogue answers (its record in Findings): what its badge stands on */
  findIdents?: { catalogue: string; state: string; name?: string | null }[] | null;
  /** the selected find's status in Findings (unexplained, known, blend…): its catalogue line agrees with it */
  findStatus?: string | null;
  /** a date's visit in the pass on show, numbered as the time line numbers it (1 to nVisits); null outside Days or off every visit */
  visitOf: (mjd: number) => number | null;
  /** how many visits the pass on show has in Days (0 in Months) */
  nVisits: number;
  /** the paths are drawn (the View tab's Paths): the task's, the people's, the machine's */
  paths: boolean;
  /** where the find of the task (or of the link) is; null without one */
  findAt: { ra: number; dec: number } | null;
  /** stops the time-lapse on the frame on show (Mark needs a still picture to click on) */
  pause: () => void;
  /** the field's passes (or the visits of the pass, in Days) are on the picture: a question can be answered by looking */
  ready: boolean;
}

const ICON_MARK =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" aria-hidden="true"><path d="M12 4l8 8-8 8-8-8z"/><circle cx="12" cy="12" r="1.2" fill="currentColor" stroke="none"/></svg>';
const ICON_RULER =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 16.5L16.5 4l3.5 3.5L7.5 20z"/><path d="M8 12.5l1.8 1.8M11 9.5l1.8 1.8M14 6.5l1.8 1.8"/></svg>';

export interface FlowTrack {
  passes: number[];
  mjd: number[];
  ra: number[];
  dec: number[];
  ra_2025_9: number;
  dec_2025_9: number;
  speed: number;
  pa: number;
}

export type FlowTool = 'mark' | 'ruler' | null;

export interface FlowDrawEnv extends Omit<KnownDrawEnv, 'pointer' | 'match'> {
  /** a wall of tiles: rings and marks without names */
  wall: boolean;
  /** a task about one object: nothing else may draw the eye (no rings on sources without a match) */
  focus?: boolean;
  /** a find opened in a wide field: its zoom (where it is drawn, and what part of the picture it shows) gets the rings too */
  zoom?: { rect: { x: number; y: number; w: number; h: number }; cx: number; cy: number; s: number } | null;
  /** the picture of the pass on show and its noise (null in Days, on a wall, or before it has arrived) */
  picture: { pass: number; n: number; rgb: Uint16Array; bg: number; sigma: number; step: number } | null;
}

const LEVEL_COLOUR: Record<MarkLevel, string> = { suspicious: '#ff9d5c', interesting: '#7fd8ff', artefact: '#aab4b9' };
const KIND_COLOUR: Record<MarkKind, string> = { moves: '#ffd08a', not_in_catalogues: '#7fd8ff', artefact: '#aab4b9', odd: '#d7a6ff' };
const KIND_WORD: Record<MarkKind, string> = { moves: 'It moves', not_in_catalogues: 'Not in catalogues', artefact: 'Artefact', odd: 'Something odd' };
const KIND_SHORT: Record<MarkKind, string> = { moves: 'It moves', not_in_catalogues: 'Not in catalogues', artefact: 'Artefact', odd: 'Odd' };
/** what the service does with a mark of each kind when it is sent (spec §5g): said in step 3 when nothing newer is known */
const SENT_WORD: Record<MarkKind, string> = {
  moves: 'checks running',
  not_in_catalogues: 'catalogues asked again',
  artefact: 'counted with the others',
  odd: 'in the Talk of this place',
};
const LOOK_WORD: Record<string, string> = { blinks: 'Blinks on and off', glare: 'Glare or ghost', spike: 'Spike', edge: 'Picture edge', hot_dot: 'One hot dot' };
/** what the server did with a mark, as the picture's status line says it (spec §5g) */
export function effectWord(e: MarkEffect | null): string {
  if (!e) return '';
  const n = typeof e.people === 'number' ? e.people : 0;
  const matches = Array.isArray(e['matches']) ? (e['matches'] as string[]) : [];
  switch (`${e.effect}:${e.state ?? ''}`) {
    case 'moves:needs_another_pass':
      return '';
    case 'moves:checking':
    case 'moves:started':
    case 'moves:running':
      return 'It moves · checks running · in your investigation';
    case 'moves:no_checks':
      return 'It moves · in your investigation';
    case 'not_in_catalogues:known':
      return `Known: ${matches.slice(0, 2).join(', ') || 'in a catalogue'}`;
    case 'not_in_catalogues:unknown':
      return 'In no catalogue · a finding found by people';
    case 'not_in_catalogues:no_answer':
      return 'Catalogues asked · no answer yet';
    case 'artefact:waiting':
      return 'Artefact · waiting for a second person';
    case 'artefact:agreed':
      return `Artefact · ${n} people agree`;
    case 'artefact:set_aside':
      return `Artefact · ${n} people agree · the machine's find set aside`;
    case 'odd:talk':
      return 'Something odd · in the Talk of this place';
    case 'odd:no_note':
      return 'Something odd · add a note in the label';
    default:
      return e.effect === 'moves' ? 'It moves · checks running' : '';
  }
}

/** the first mark of the same mover: its `link`, or (the account does not keep `link`) the earliest "It moves" mark of the same field
 *  with the same label; null for a mark that is its own head */
export function headOf(m: Mark, all: Mark[]): Mark | null {
  if (m.link) return all.find((q) => q.id === m.link) ?? null;
  if (m.kind !== 'moves') return null;
  const first = all
    .filter((q) => q.kind === 'moves' && q.field_key === m.field_key && q.label === m.label && !q.link)
    .sort((x, y) => x.created.localeCompare(y.created))[0];
  return first && first.id !== m.id ? first : null;
}

/** a mark's colour: its kind's, else the level of before */
const markColour = (m: Pick<Mark, 'kind' | 'level'>) => (m.kind ? KIND_COLOUR[m.kind] : LEVEL_COLOUR[m.level]);

export interface LabFlow {
  /** drawn on the lab's overlay every frame, after its own rings */
  draw(env: FlowDrawEnv): void;
  /** the pointer over the picture (CSS px inside it), null when it left */
  pointer(p: { x: number; y: number } | null): void;
  /** a click on the picture at CSS px; true when the click was taken (a mark put or opened, a ruler point) */
  click(x: number, y: number): boolean;
  /** the status line on the picture ('' when there is nothing to say) */
  status: string;
  /** over the picture: the strip of its tools (Mark, Ruler) */
  floats: ComponentChildren;
  /** in the playback bar, next to Blink: the two pass pickers of a Blink of two passes */
  blinkPick: ComponentChildren;
  /** the card of what is selected (a mark or a source) with its action */
  card: ComponentChildren;
  /** the field's marks, one row per mark (Task tab) */
  marksList: ComponentChildren;
  /** people set the machine's find at this place aside (spec §5g): it leaves the field's list */
  asideAt(ra: number, dec: number): boolean;
  /** on the picture's status line: the investigation of the field, opening it in the Cabinet */
  chip: ComponentChildren;
  /** a mark's card is open: the Object tab is the mark's alone */
  markOpen: boolean;
  /** the catalogue object the picture's rings have at a place on a date (decimal year), null when none within 8″ */
  catalogueAt(ra: number, dec: number, year: number): KnownObject | null;
  /** what the data says about a place that looks like an artefact: the frame's edge, a bright star's glow ('' when nothing) */
  hintAt(ra: number, dec: number): string;
  tool: FlowTool;
  marks: Mark[];
  /** the marks inside the frame on show */
  marksHere: Mark[];
  ruler: RulerPoint[];
  setTool(t: FlowTool): void;
  /** the two passes to blink between (their indexes), null when every pass plays */
  blink: [number, number] | null;
  /** the Task panel, null when the lab was opened without a task */
  taskPanel: ComponentChildren;
  /** a task about one object, the others not asked for: only that object is bright, every other overlay a quiet outline */
  focus: boolean;
  /** Known objects is on and a catalogue is still being asked */
  knownBusy: boolean;
  /** the Known objects lookup as the switch and the row say it: asking, how many, or who did not answer and the next try */
  knownSearch: SearchInfo;
  /** the People's marks of the field as its switch says it: how many places people marked here */
  peopleSearch: SearchInfo;
  /** what the field's lookup already holds about the selected source (a ringed known object or a deeper match), null when nothing */
  selHeld: Held | null;
  /** a task's find not answered yet: what the catalogues and Findings say of it waits for the answers */
  blind: boolean;
  /** a track's task is at its sums: the picture opens on them */
  trackSum: boolean;
  /** a square's lead has its questions open: the quarter's look waits (it counts on the quarter only) */
  leadOpen: boolean;
}

export function useLabFlow(a: FlowArgs): LabFlow {
  const [known, setKnown] = useState<KnownState>(KNOWN_OFF);
  const knownRef = useRef(known);
  knownRef.current = known;
  const epochsKey = a.epochs.map((e) => e.toFixed(4)).join(',');
  // a lookup that did not answer (or a catalogue that did not) is asked again by itself (lab/progress.tsx)
  const [knownTry, setKnownTry] = useState(0);
  // a catalogue that did not answer, Gaia, SIMBAD, JPL or a deeper one, once nothing is being asked any more: asked again on the retry
  // schedule with its countdown (the lookup itself never asks a silent one again)
  const knownSilent =
    known.state === 'failed' ||
    (known.state === 'ok' &&
      !known.pending.length &&
      !known.deepPending.length &&
      (KNOWN_SOURCES.some((c) => !known.answered[c]) || known.deepSilent.length > 0));
  const knownRetry = useRetry(knownSilent, () => setKnownTry((x) => x + 1), `${a.ra},${a.dec},${a.fov}`);
  useEffect(() => {
    if (!a.epochs.length || !Number.isFinite(a.ra) || !Number.isFinite(a.dec)) {
      setKnown(KNOWN_OFF);
      return;
    }
    // asked when the field opens, switch on or off: the card of a selected object reads the same answer as the picture's rings
    return followKnown(knownUrl(a.ra, a.dec, a.fov, a.epochs), setKnown);
  }, [a.ra, a.dec, a.fov, epochsKey, knownTry]);

  // a sign-in that did not finish comes back here with signin=google (or expired): said on the status line for a while, then the
  // address is cleaned (the person is still where they were)
  const [signinNote, setSigninNote] = useState('');
  useEffect(() => {
    const why = a.params['signin'];
    if (!why) return;
    setSigninNote(why === 'expired' ? 'Sign-in link expired · ask for a new one' : 'Sign-in did not finish');
    const r = parseHash(location.hash);
    const params = { ...r.params };
    delete params['signin'];
    history.replaceState(null, '', formatHash({ ...r, params }));
    const t = setTimeout(() => setSigninNote(''), 15000);
    return () => clearTimeout(t);
  }, [a.params['signin']]);
  // the field's one set of known objects (the service matched it once over the passes): the rings stay from pass to pass
  const [fieldKnown, setFieldKnown] = useState<FieldKnown>(FIELD_ASKING);
  const fieldRef = useRef(fieldKnown);
  fieldRef.current = fieldKnown;
  useEffect(() => {
    setFieldKnown(FIELD_ASKING);
    if (!Number.isFinite(a.ra) || !Number.isFinite(a.dec)) return;
    return followFieldKnown(a.ra, a.dec, a.fov, setFieldKnown);
  }, [a.ra, a.dec, a.fov]);

  const [, tick] = useState(0);
  useEffect(() => onMarks(() => tick((x) => x + 1)), []);
  const marks = allMarks();
  const marksRef = useRef(marks);
  marksRef.current = marks;
  const [tool, setTool] = useState<FlowTool>(null);
  // Mark armed: the time-lapse stops on the frame on show, so what is clicked stays where it is
  useEffect(() => {
    if (tool === 'mark') a.pause();
  }, [tool]);
  // spec §5g "It moves": after the first mark of a mover the lab shows the pass farthest from it and asks for the same thing there
  const [tapHere, setTapHere] = useState<{ id: string; label: string; ra: number; dec: number; pass: number; passLabel: string } | null>(null);
  const tapRef = useRef(tapHere);
  tapRef.current = tapHere;
  // a track people corrected (GET /api/v1/findings?ids=…: data.people_track): its path is drawn beside the machine's
  type PeopleTrack = { state?: string; rate?: number; n?: number; missing?: number[]; visits?: { ra: number; dec: number; mjd: number }[] };
  const [peopleTrack, setPeopleTrack] = useState<PeopleTrack | null>(null);
  const ptRef = useRef(peopleTrack);
  ptRef.current = peopleTrack;
  const findParam = a.params['task'] ? (a.params['finding'] ?? '') : '';
  useEffect(() => {
    setPeopleTrack(null);
    if (!findParam) return;
    let stop = false;
    fetch(`/api/v1/findings?ids=${encodeURIComponent(findParam)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { finds?: { data?: { people_track?: PeopleTrack } }[] } | null) => !stop && setPeopleTrack(d?.finds?.[0]?.data?.people_track ?? null))
      .catch(() => undefined);
    return () => {
      stop = true;
    };
  }, [findParam]);
  // the People's marks of this field (GET /api/field/{key}/people): places merged by kind with counts, words only where I said something
  type PeoplePlace = { ra: number; dec: number; people: number; mine: boolean; seen: boolean; text?: string; kinds?: { kind: string }[] };
  type PeopleFind = { finding_id: string; ra: number; dec: number; seen: boolean; set_aside?: boolean };
  const [people, setPeople] = useState<{ key: string; state: 'running' | 'done' | 'failed'; places: PeoplePlace[]; finds: PeopleFind[] }>({ key: '', state: 'running', places: [], finds: [] });
  const peopleRef = useRef(people);
  peopleRef.current = people;
  const pKey = fieldKey(a.ra, a.dec, a.fov);
  // read whether or not the layer is drawn: a find people set aside leaves this field's list for everyone
  useEffect(() => {
    let stop = false;
    setPeople((x) => (x.key === pKey ? x : { key: pKey, state: 'running', places: [], finds: [] }));
    fetch(`/api/field/${encodeURIComponent(pKey)}/people`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d: { places?: PeoplePlace[]; finds?: PeopleFind[] }) => !stop && setPeople({ key: pKey, state: 'done', places: d.places ?? [], finds: d.finds ?? [] }))
      .catch(() => !stop && setPeople({ key: pKey, state: 'failed', places: [], finds: [] }));
    return () => {
      stop = true;
    };
  }, [pKey, marks.length, marks.map((m) => m.kind ?? '').join(',')]);
  // another field (or the next task) starts with no tool: a Mark or Ruler left on would take the first click there
  useEffect(() => {
    setTool(null);
    setTapHere(null);
  }, [fieldKey(a.ra, a.dec, a.fov)]);
  const [open, setOpen] = useState<string | null>(null);
  const openRef = useRef(open);
  openRef.current = open;
  const [ruler, setRulerState] = useState<RulerPoint[]>([]);
  /** the ruler the lab laid itself between a find's two passes (its word has no rate of its own: the find's rate is the one) */
  const autoRuler = useRef(false);
  const setRuler: typeof setRulerState = (v) => {
    autoRuler.current = false;
    setRulerState(v);
  };
  const rulerSays = (r: RulerPoint[]) => rulerWord(rulerOf(r[0]!, r[1]!), { rate: !autoRuler.current });
  const rulerRef = useRef(ruler);
  rulerRef.current = ruler;
  const ptr = useRef<{ x: number; y: number } | null>(null);
  const argsRef = useRef(a);
  argsRef.current = a;

  // another field: the ruler and the open editor belong to the old one
  useEffect(() => {
    setRuler([]);
    setOpen(null);
  }, [a.ra, a.dec, a.fov]);

  const g = a.grid;
  const fovArc = g ? g.n * g.step_arcsec : 0;
  const cosd = g ? Math.cos((g.dec0 * Math.PI) / 180) : 1;
  const uvOf = (ra: number, dec: number): [number, number] =>
    g ? [0.5 - ((((ra - g.ra0 + 540) % 360) - 180) * cosd * 3600) / fovArc, 0.5 - ((dec - g.dec0) * 3600) / fovArc] : [-1, -1];
  const skyOf = (u: number, v: number): [number, number] =>
    g ? [(g.ra0 - ((u - 0.5) * fovArc) / 3600 / cosd + 360) % 360, g.dec0 - ((v - 0.5) * fovArc) / 3600] : [0, 0];
  const inFrame = (m: { ra: number; dec: number }) => {
    const [u, v] = uvOf(m.ra, m.dec);
    return u >= 0 && v >= 0 && u <= 1 && v <= 1;
  };
  const marksHere = useMemo(() => marks.filter(inFrame), [marks.length, marks, g?.ra0, g?.dec0, g?.n, g?.step_arcsec]);

  // the sources of each pass picture (found once), and what the catalogues have of them at the date shown
  const peaksRef = useRef(new Map<string, Peak[]>());
  const matchRef = useRef<{ key: string; m: KnownMatch } | null>(null);
  const unionRef = useRef<{ key: string; passes: Set<number>; shown: Set<string>; deep: Set<string>; lone: KnownMatch['noMatch'] }>({
    key: '',
    passes: new Set(),
    shown: new Set(),
    deep: new Set(),
    lone: [],
  });
  const [counts, setCounts] = useState<{ known: number; noMatch: number } | null>(null);
  const countsRef = useRef('');
  useEffect(() => {
    peaksRef.current.clear();
    matchRef.current = null;
  }, [a.ra, a.dec, a.fov]);
  const draw = (env: FlowDrawEnv) => {
    const { ctx, rects, wall, uvOf: uv, placed } = env;
    const kn = argsRef.current.known ? knownRef.current : KNOWN_OFF; // the switch decides what is drawn, not what is known
    let match: KnownMatch | null = null;
    const pic = env.picture;
    if (kn.state === 'ok' && pic) {
      const pk = `${pic.pass}:${pic.n}:${pic.rgb.length}:${pic.bg}`;
      let peaks = peaksRef.current.get(pk);
      if (!peaks) peaksRef.current.set(pk, (peaks = findPeaks(pic.rgb, pic.n, pic.bg, pic.sigma)));
      const mk = `${pk}:${kn.objects.length}:${kn.deep.length}:${env.year.toFixed(4)}`;
      if (matchRef.current?.key !== mk)
        matchRef.current = { key: mk, m: matchKnown(kn.objects, peaks, pic.n, pic.step, uv, env.year, kn.deep) };
      match = matchRef.current.m;
    }
    // one set of rings for the field, not one per pass: what any pass shown so far has at a catalogue's place is known, and stays
    // ringed in every pass (a star too faint to be found in one pass blinked its ring off and on); the counts only grow, never flip
    const uk = `${argsRef.current.ra},${argsRef.current.dec},${argsRef.current.fov}|${kn.objects.length}|${kn.deep.length}`;
    let un = unionRef.current;
    if (un.key !== uk) unionRef.current = un = { key: uk, passes: new Set(), shown: new Set(), deep: new Set(), lone: [] };
    if (match && pic && !un.passes.has(pic.pass)) {
      un.passes.add(pic.pass);
      for (const id of match.shown) un.shown.add(id);
      for (const id of match.deep ?? []) un.deep.add(id);
      const tol = 2.5 / pic.n;
      for (const q of match.noMatch) {
        const same = un.lone.find((o) => Math.abs(o.u - q.u) <= tol && Math.abs(o.v - q.v) <= tol);
        if (same) same.snr = Math.max(same.snr, q.snr);
        else un.lone.push({ ...q });
      }
      // a place first without a match may be matched in another pass (a moving star's place): known wins
    }
    const fk = fieldRef.current;
    let knDraw: KnownState = kn;
    if (fk.state === 'ok' && argsRef.current.known) {
      // the service's set: every catalogue source the field shows, ringed in every pass; the named and fast stars keep their motion
      // and their names from /api/known, the Solar System bodies stay per pass
      const keep = kn.state === 'ok' ? kn.objects.filter((o) => o.label || o.source === 'jpl') : [];
      const kept = new Set(keep.map((o) => o.id));
      // and the fainter catalogue stars a pass of this picture shows (the service keeps what half the passes show at S/N 8): the
      // union over the passes seen, never one pass alone, so they stay too; a star already in the service's set is not ringed twice
      const year = env.year;
      const near3 = (o: KnownObject, list: KnownObject[]) => {
        const at = placeAt(o, year);
        if (!at) return true;
        const c = Math.cos((at[1] * Math.PI) / 180);
        return list.some((q) => {
          const p = q.positions[0]!;
          return Math.hypot((((p.ra - at[0] + 540) % 360) - 180) * c, p.dec - at[1]) * 3600 < 3;
        });
      };
      const extra = kn.state === 'ok' ? kn.objects.filter((o) => un.shown.has(o.id) && !kept.has(o.id) && !near3(o, fk.objects)) : [];
      const extraDeep = kn.state === 'ok' ? kn.deep.filter((o) => un.deep.has(o.id) && !near3(o, [...fk.deep, ...fk.objects])) : [];
      const objects = [...keep, ...fk.objects.filter((o) => !kept.has(o.id)), ...extra];
      const deepAll = [...fk.deep, ...extraDeep];
      knDraw = { ...(kn.state === 'ok' ? kn : { ...KNOWN_OFF, answered: { gaia: true, simbad: true, jpl: false } }), state: 'ok', objects, deep: deepAll };
      match = {
        shown: new Set(objects.map((o) => o.id)),
        deep: new Set(deepAll.map((o) => o.id)),
        noMatch: fk.unmatched.map((q) => {
          const [u, v] = uv(q.ra, q.dec);
          return { u, v, snr: q.snr };
        }),
      };
    } else {
      match = un.passes.size ? { shown: un.shown, deep: un.deep, noMatch: un.lone } : match;
    }
    const ck = match ? `${match.shown.size + (match.deep?.size ?? 0)}:${match.noMatch.length}` : '';
    if (ck !== countsRef.current) {
      countsRef.current = ck;
      setCounts(match ? { known: match.shown.size + (match.deep?.size ?? 0), noMatch: match.noMatch.length } : null);
    }
    // a task about one object rings nothing else; and while a deeper catalogue is still being asked no source is ringed as
    // unmatched (the ring would appear and go when the catalogue arrives): the count on the status line says how many so far
    const noRings = env.focus || kn.deepPending.length > 0;
    // the passes seen so far at a place: a source there in one pass only may be a cosmic ray or a hot pixel
    const seenIn = (u: number, v: number): [number, number] | null => {
      if (!pic) return null;
      // one answer per pass (a pass's quick and full pictures are both kept): seen when either shows it
      const byPass = new Map<string, boolean>();
      const x = u * pic.n;
      const y = v * pic.n;
      for (const [key, pk] of peaksRef.current) {
        const [pass, n] = key.split(':');
        if (Number(n) !== pic.n) continue;
        const hit = pk.some((q) => Math.abs(q.x - x) <= 2 && Math.abs(q.y - y) <= 2);
        byPass.set(pass!, (byPass.get(pass!) ?? false) || hit);
      }
      const k = [...byPass.values()].filter(Boolean).length;
      return byPass.size ? [k, byPass.size] : null;
    };
    // a task's find not answered yet: nothing of the catalogues at its place (its answer may be known: a control object)
    // (the find's own places, whatever is selected now: another object's card must not uncover the find's ring)
    const bs = taskRef.current.blind ? argsRef.current.sel : null;
    const bp = bs?.finding ? (bs.pmra || bs.pmdec ? atEpoch(bs.ra, bs.dec, bs.pmra ?? 0, bs.pmdec ?? 0, env.year) : [bs.ra, bs.dec]) : null;
    const box0 = rects[0];
    const blindAt =
      taskRef.current.blind && box0
        ? [...(bp ? [bp] : []), ...(argsRef.current.findAt ? [[argsRef.current.findAt.ra, argsRef.current.findAt.dec]] : []), ...taskTrack.pts.map((q) => [q.ra, q.dec])].map((q) => {
            const [u, v] = uv(q[0]!, q[1]!);
            return { x: box0.x + u * box0.w, y: box0.y + v * box0.h, r: Math.max(14, (12 * box0.w) / env.fovArc) };
          })
        : null;
    drawKnown({ ...env, seenIn, blindAt, pointer: ptr.current, match: match && noRings ? { ...match, noMatch: [] } : match }, knDraw);
    const z = env.zoom;
    if (z && !wall) {
      // the zoom on a find: the same rings and names at the zoom's scale (the sources without a match too: what is near the find)
      const zm = (u: number, v: number): [number, number] => [(u - z.cx) / z.s + 0.5, (v - z.cy) / z.s + 0.5];
      const inZoom = (q: { u: number; v: number }) => Math.abs(q.u - z.cx) < z.s / 2 && Math.abs(q.v - z.cy) < z.s / 2;
      ctx.save();
      drawKnown(
        {
          ...env,
          rects: [z.rect],
          uvOf: (ra, dec) => zm(...uv(ra, dec)),
          fovArc: env.fovArc * z.s,
          placed: [],
          pointer: null,
          hidden: () => false,
          match: match
            ? {
                shown: match.shown,
                noMatch: kn.deepPending.length
                  ? []
                  : match.noMatch.filter(inZoom).map((q) => {
                      const [u, v] = zm(q.u, q.v);
                      return { ...q, u, v };
                    }),
              }
            : null,
        },
        kn,
      );
      ctx.restore();
    }
    // a track's task: its path through the visits, the direction, a dot per visit (red when tapped as missing), the ring on the visit
    // shown, so that "does one dot step along the arrow?" has the arrow and the dot on the picture
    const tt = taskTrack.pts;
    if (tt.length >= 2 && !wall && argsRef.current.paths) {
      const yr = (m: number) => 2000 + (m - 51544.5) / 365.25;
      let near = 0;
      tt.forEach((q, k) => Math.abs(yr(q.mjd) - env.year) < Math.abs(yr(tt[near]!.mjd) - env.year) && (near = k));
      // the frame on show is one of the track's own visits: the same number on the time line, never a neighbour a few hours away
      const shownVisit = argsRef.current.visitOf((env.year - 2000) * 365.25 + 51544.5);
      const onVisit = shownVisit != null && argsRef.current.visitOf(tt[near]!.mjd) === shownVisit;
      ctx.save();
      for (const box of rects) {
        ctx.save();
        ctx.beginPath();
        ctx.rect(box.x, box.y, box.w, box.h);
        ctx.clip();
        const xy = tt.map((q) => {
          const [u, v] = uv(q.ra, q.dec);
          return [box.x + u * box.w, box.y + v * box.h] as const;
        });
        ctx.beginPath();
        xy.forEach(([x, y], k) => (k ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
        ctx.setLineDash([5, 5]);
        ctx.lineWidth = 3;
        ctx.strokeStyle = 'rgba(0,0,0,0.55)';
        ctx.stroke();
        ctx.lineWidth = 1.4;
        ctx.strokeStyle = 'rgba(255,200,110,0.9)';
        ctx.stroke();
        ctx.setLineDash([]);
        // the arrow at the last visit
        const [x1, y1] = xy[xy.length - 1]!;
        const [x0, y0] = xy[xy.length - 2]!;
        const ang = Math.atan2(y1 - y0, x1 - x0);
        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.lineTo(x1 - 11 * Math.cos(ang - 0.45), y1 - 11 * Math.sin(ang - 0.45));
        ctx.moveTo(x1, y1);
        ctx.lineTo(x1 - 11 * Math.cos(ang + 0.45), y1 - 11 * Math.sin(ang + 0.45));
        ctx.lineWidth = 1.8;
        ctx.strokeStyle = 'rgba(255,200,110,0.95)';
        ctx.stroke();
        // every visit: a faint dot with its number (red when tapped as missing); the one on show is bright, with its ring below
        ctx.font = '600 11px ui-monospace, SFMono-Regular, Menlo, monospace';
        xy.forEach(([x, y], k) => {
          const miss = taskTrack.missing.includes(tt[k]!.i);
          const cur = k === near && onVisit;
          ctx.beginPath();
          ctx.arc(x, y, cur ? 3.4 : 3, 0, 2 * Math.PI);
          ctx.lineWidth = 2;
          ctx.strokeStyle = 'rgba(0,0,0,0.6)';
          ctx.stroke();
          ctx.fillStyle = miss ? '#ff6b6b' : cur ? '#ffd08a' : 'rgba(255,200,110,0.55)';
          ctx.fill();
          if (!cur) {
            const t = String(argsRef.current.visitOf(tt[k]!.mjd) ?? tt[k]!.i + 1);
            ctx.lineWidth = 3;
            ctx.strokeStyle = '#000';
            ctx.strokeText(t, x - 13, y + 4);
            ctx.fillStyle = miss ? '#ffb3b3' : 'rgba(255,214,150,0.8)';
            ctx.fillText(t, x - 13, y + 4);
          }
        });
        // a visit outside the picture: where the path leaves it, an arrow at the edge and its number (never cut silently)
        const inside = ([x, y]: readonly [number, number]) => x >= box.x && x <= box.x + box.w && y >= box.y && y <= box.y + box.h;
        const edgeOf = (from: readonly [number, number], to: readonly [number, number]): [number, number] => {
          // the last point of the segment from→to inside the box (from inside, to outside)
          let lo = 0;
          let hi = 1;
          for (let k = 0; k < 24; k++) {
            const t = (lo + hi) / 2;
            if (inside([from[0] + (to[0] - from[0]) * t, from[1] + (to[1] - from[1]) * t])) lo = t;
            else hi = t;
          }
          return [from[0] + (to[0] - from[0]) * lo, from[1] + (to[1] - from[1]) * lo];
        };
        const firstIn = xy.findIndex(inside);
        if (firstIn >= 0) {
          const outs: { k: number; at: [number, number]; dir: number }[] = [];
          let lastIn = firstIn;
          xy.forEach((p, k) => inside(p) && (lastIn = k));
          if (firstIn > 0) {
            const at = edgeOf(xy[firstIn]!, xy[firstIn - 1]!);
            outs.push({ k: 0, at, dir: Math.atan2(xy[firstIn - 1]![1] - at[1], xy[firstIn - 1]![0] - at[0]) });
          }
          if (lastIn < xy.length - 1) {
            const at = edgeOf(xy[lastIn]!, xy[lastIn + 1]!);
            outs.push({ k: xy.length - 1, at, dir: Math.atan2(xy[lastIn + 1]![1] - at[1], xy[lastIn + 1]![0] - at[0]) });
          }
          for (const o of outs) {
            const [ex, ey] = [o.at[0] - 6 * Math.cos(o.dir), o.at[1] - 6 * Math.sin(o.dir)];
            ctx.beginPath();
            ctx.moveTo(ex + 9 * Math.cos(o.dir), ey + 9 * Math.sin(o.dir));
            ctx.lineTo(ex + 9 * Math.cos(o.dir + 2.4), ey + 9 * Math.sin(o.dir + 2.4));
            ctx.lineTo(ex + 9 * Math.cos(o.dir - 2.4), ey + 9 * Math.sin(o.dir - 2.4));
            ctx.closePath();
            ctx.fillStyle = '#ffd08a';
            ctx.fill();
            // the pass's own visit numbers, as the time line says them
            const vn = argsRef.current.visitOf(tt[o.k]!.mjd);
            const lab = o.k === 0 ? `from visit ${vn ?? 1}` : `to visit ${vn ?? tt.length}`;
            ctx.font = '600 11px ui-monospace, SFMono-Regular, Menlo, monospace';
            const tw = ctx.measureText(lab).width;
            const lx = Math.min(box.x + box.w - tw - 6, Math.max(box.x + 6, ex - tw / 2 - 18 * Math.cos(o.dir)));
            // never on the title row or the band chips and status line at the bottom
            let ly = Math.min(box.y + box.h - 84, Math.max(box.y + 52, ey - 16 * Math.sin(o.dir) + 4));
            // never over what is already on the picture (the scale bar, a name): up a line until it is clear
            for (let t = 0; t < 4 && placed.some((q) => lx < q.x1 && lx + tw > q.x0 && ly - 11 < q.y1 && ly + 3 > q.y0); t++) ly -= 16;
            placed.push({ x0: lx, y0: ly - 11, x1: lx + tw, y1: ly + 3 });
            ctx.lineWidth = 3;
            ctx.strokeStyle = '#000';
            ctx.strokeText(lab, lx, ly);
            ctx.fillStyle = '#ffd08a';
            ctx.fillText(lab, lx, ly);
          }
        }
        const [rx, ry] = xy[near]!;
        if (onVisit) {
        ctx.beginPath();
        ctx.arc(rx, ry, 13, 0, 2 * Math.PI);
        ctx.lineWidth = 3;
        ctx.strokeStyle = 'rgba(0,0,0,0.55)';
        ctx.stroke();
        ctx.lineWidth = 1.6;
        ctx.strokeStyle = taskTrack.missing.includes(tt[near]!.i) ? '#ff6b6b' : '#ffd08a';
        ctx.stroke();
        ctx.font = '600 11px ui-monospace, SFMono-Regular, Menlo, monospace';
        ctx.lineWidth = 3;
        ctx.strokeStyle = '#000';
        const lab = `visit ${argsRef.current.visitOf(tt[near]!.mjd) ?? tt[near]!.i + 1}`;
        ctx.strokeText(lab, rx + 17, ry + 4);
        ctx.fillStyle = '#ffd08a';
        ctx.fillText(lab, rx + 17, ry + 4);
        }
        ctx.restore();
      }
      ctx.restore();
    }
    // People's marks: a small square per place people marked, the words where I said something too (never a name)
    const pp = argsRef.current.people && peopleRef.current.key === fieldKey(argsRef.current.ra, argsRef.current.dec, argsRef.current.fov) ? peopleRef.current.places : [];
    if (pp.length && !wall) {
      ctx.save();
      ctx.font = '600 11px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif';
      for (const box of rects)
        for (const q of pp) {
          if (!q.seen || q.mine && q.people === 1) continue; // only what others said, where I said something too
          const [u, v] = uv(q.ra, q.dec);
          const x = box.x + u * box.w;
          const y = box.y + v * box.h;
          if (env.hidden(x, y)) continue;
          ctx.lineWidth = 3;
          ctx.strokeStyle = 'rgba(0,0,0,0.55)';
          ctx.strokeRect(x - 7, y - 7, 14, 14);
          ctx.lineWidth = 1.3;
          ctx.strokeStyle = '#d7a6ff';
          ctx.strokeRect(x - 7, y - 7, 14, 14);
          if (q.text) {
            ctx.lineWidth = 3;
            ctx.strokeStyle = '#000';
            ctx.strokeText(q.text, x + 11, y + 4);
            ctx.fillStyle = '#e9d4ff';
            ctx.fillText(q.text, x + 11, y + 4);
          }
        }
      ctx.restore();
    }
    // the people's corrected path of this track: violet, dashed, beside the machine's
    const pt = ptRef.current;
    if (pt?.visits && pt.visits.length >= 2 && !wall && argsRef.current.paths) {
      ctx.save();
      for (const box of rects) {
        ctx.save();
        ctx.beginPath();
        ctx.rect(box.x, box.y, box.w, box.h);
        ctx.clip();
        ctx.beginPath();
        pt.visits.forEach((q, k) => {
          const [u, v] = uv(q.ra, q.dec);
          const x = box.x + u * box.w;
          const y = box.y + v * box.h;
          if (k) ctx.lineTo(x, y);
          else ctx.moveTo(x, y);
        });
        ctx.setLineDash([2, 4]);
        ctx.lineWidth = 3;
        ctx.strokeStyle = 'rgba(0,0,0,0.55)';
        ctx.stroke();
        ctx.lineWidth = 1.4;
        ctx.strokeStyle = '#d7a6ff';
        ctx.stroke();
        ctx.setLineDash([]);
        for (const q of pt.visits) {
          const [u, v] = uv(q.ra, q.dec);
          ctx.beginPath();
          ctx.arc(box.x + u * box.w, box.y + v * box.h, 2.4, 0, 2 * Math.PI);
          ctx.fillStyle = '#d7a6ff';
          ctx.fill();
        }
        ctx.restore();
      }
      ctx.restore();
    }
    // "tap it here": where the first mark of the mover was, a dashed ring of half a minute of arc on the pass asked
    const th = tapRef.current;
    if (th && !wall) {
      ctx.save();
      for (const box of rects) {
        const [u, v] = uv(th.ra, th.dec);
        const x = box.x + u * box.w;
        const y = box.y + v * box.h;
        const r = Math.max(14, (30 / env.fovArc) * box.w);
        ctx.beginPath();
        ctx.arc(x, y, r, 0, 2 * Math.PI);
        ctx.setLineDash([4, 4]);
        ctx.lineWidth = 3;
        ctx.strokeStyle = 'rgba(0,0,0,0.5)';
        ctx.stroke();
        ctx.lineWidth = 1.4;
        ctx.strokeStyle = '#ffd08a';
        ctx.stroke();
      }
      ctx.restore();
    }
    // the whole grid square: a thin dashed outline (a quarter of it is on show at 10′)
    const sqo = argsRef.current.square?.outline;
    if (sqo && sqo.length >= 4 && !wall) {
      ctx.save();
      for (const box of rects) {
        ctx.beginPath();
        sqo.forEach(([r, d], i) => {
          const [u, v] = uv(r, d);
          const x = box.x + u * box.w;
          const y = box.y + v * box.h;
          if (i) ctx.lineTo(x, y);
          else ctx.moveTo(x, y);
        });
        ctx.closePath();
        ctx.setLineDash([6, 5]);
        ctx.lineWidth = 2.4;
        ctx.strokeStyle = 'rgba(0,0,0,0.5)';
        ctx.stroke();
        ctx.lineWidth = 1;
        ctx.strokeStyle = 'rgba(255,214,140,0.75)';
        ctx.stroke();
      }
      ctx.restore();
    }
    // a tap task: the taps (a small cross each) and, for "Find it again", the strip where the lead should be
    const tv = taskRef.current;
    if (tv?.tapping && !wall) {
      ctx.save();
      for (const box of rects) {
        const path = tv.body?.predicted?.path;
        if (path && path.length > 1) {
          ctx.beginPath();
          path.forEach(([r, d], i) => {
            const [u, v] = uv(r, d);
            if (i) ctx.lineTo(box.x + u * box.w, box.y + v * box.h);
            else ctx.moveTo(box.x + u * box.w, box.y + v * box.h);
          });
          ctx.lineCap = 'round';
          ctx.lineWidth = 16;
          ctx.strokeStyle = 'rgba(255,214,140,0.16)';
          ctx.stroke();
          ctx.lineWidth = 1;
          ctx.setLineDash([4, 4]);
          ctx.strokeStyle = 'rgba(255,214,140,0.8)';
          ctx.stroke();
          ctx.setLineDash([]);
        }
        for (const t of tv.taps) {
          const [u, v] = uv(t.ra, t.dec);
          const x = box.x + u * box.w;
          const y = box.y + v * box.h;
          ctx.lineWidth = 3;
          ctx.strokeStyle = 'rgba(0,0,0,0.6)';
          for (const w of [3, 1.4]) {
            ctx.lineWidth = w;
            ctx.beginPath();
            ctx.moveTo(x - 7, y);
            ctx.lineTo(x + 7, y);
            ctx.moveTo(x, y - 7);
            ctx.lineTo(x, y + 7);
            ctx.stroke();
            ctx.strokeStyle = '#ffe27a';
          }
        }
      }
      ctx.restore();
    }
    const ms = marksRef.current;
    const rl = rulerRef.current;
    if (!ms.length && !rl.length) return;
    ctx.save();
    for (const box of rects) {
      ctx.save();
      ctx.beginPath();
      ctx.rect(box.x, box.y, box.w, box.h);
      ctx.clip();
      for (const m of ms) {
        const [u, v] = uv(m.ra, m.dec);
        if (u < 0 || v < 0 || u > 1 || v > 1) continue;
        const x = box.x + u * box.w;
        const y = box.y + v * box.h;
        const r = wall ? 4 : 6;
        const head = headOf(m, ms);
        if (head) {
          // the same mover on another pass: a dot, a thin line to where it was first marked
          const [hu, hv] = uv(head.ra, head.dec);
          ctx.beginPath();
          ctx.moveTo(box.x + hu * box.w, box.y + hv * box.h);
          ctx.lineTo(x, y);
          ctx.lineWidth = 2.6;
          ctx.strokeStyle = 'rgba(0,0,0,0.55)';
          ctx.stroke();
          ctx.lineWidth = 1;
          ctx.strokeStyle = markColour(head);
          ctx.stroke();
          ctx.beginPath();
          ctx.arc(x, y, 2.8, 0, 2 * Math.PI);
          ctx.fillStyle = head.id === openRef.current ? '#ffffff' : markColour(head);
          ctx.fill();
          continue;
        }
        const sel = m.id === openRef.current;
        // a mark: a small diamond with an open middle (the source stays seen), in the colour of its level
        ctx.beginPath();
        ctx.moveTo(x, y - r);
        ctx.lineTo(x + r, y);
        ctx.lineTo(x, y + r);
        ctx.lineTo(x - r, y);
        ctx.closePath();
        ctx.lineWidth = sel ? 4 : 3.2;
        ctx.strokeStyle = 'rgba(0,0,0,0.6)';
        ctx.stroke();
        ctx.lineWidth = sel ? 2 : 1.4;
        ctx.strokeStyle = sel ? '#ffffff' : markColour(m);
        ctx.stroke();
        if (wall) continue;
        placed.push({ x0: x - r - 2, y0: y - r - 2, x1: x + r + 2, y1: y + r + 2 });
        if (m.label) {
          ctx.font = '600 11px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
          const tw = ctx.measureText(m.label).width;
          // clear of the diamond, and on the side away from the line to the same mover's other place
          const tail = ms.find((q) => q.id !== m.id && headOf(q, ms)?.id === m.id);
          const tx = tail ? box.x + uv(tail.ra, tail.dec)[0] * box.w : x;
          const left = tail ? tx > x : x + r + 8 + tw > box.x + box.w - 4;
          const lx = left ? x - r - 8 - tw : x + r + 8;
          const ly = y - r - 5;
          ctx.lineWidth = 3;
          ctx.strokeStyle = '#000';
          ctx.strokeText(m.label, lx, ly);
          ctx.fillStyle = markColour(m);
          ctx.fillText(m.label, lx, ly);
          placed.push({ x0: lx, y0: ly - 11, x1: lx + tw, y1: ly + 2 });
        }
      }
      if (!wall && rl.length) {
        const pts = rl.map((p) => {
          const [u, v] = uv(p.ra, p.dec);
          return [box.x + u * box.w, box.y + v * box.h] as const;
        });
        // the ruler: its line to the second point, or to the pointer while the second is being chosen
        const end = pts[1] ?? (ptr.current ? ([ptr.current.x, ptr.current.y] as const) : null);
        ctx.strokeStyle = 'rgba(0,0,0,0.6)';
        ctx.lineWidth = 3.4;
        const line = () => {
          ctx.beginPath();
          ctx.moveTo(pts[0]![0], pts[0]![1]);
          if (end) ctx.lineTo(end[0], end[1]);
          ctx.stroke();
        };
        line();
        ctx.strokeStyle = '#ffe27a';
        ctx.lineWidth = 1.4;
        line();
        ctx.fillStyle = '#ffe27a';
        for (const [x, y] of pts) {
          ctx.beginPath();
          ctx.arc(x, y, 2.6, 0, 2 * Math.PI);
          ctx.fill();
        }
        if (pts.length === 2) {
          const s = rulerSays(rl);
          ctx.font = '600 11px ui-monospace, Menlo, monospace';
          const tw = ctx.measureText(s).width;
          const mx = Math.max(box.x + 4, Math.min(box.x + box.w - tw - 4, (pts[0]![0] + pts[1]![0]) / 2 + 8));
          const my = Math.max(box.y + 14, (pts[0]![1] + pts[1]![1]) / 2 - 8);
          ctx.lineWidth = 3;
          ctx.strokeStyle = '#000';
          ctx.strokeText(s, mx, my);
          ctx.fillText(s, mx, my);
        }
      }
      ctx.restore();
    }
    ctx.restore();
  };

  /** the mark under a point of the picture, within 9 px */
  const markAt = (x: number, y: number): Mark | null => {
    let best: Mark | null = null;
    let bd = 9;
    for (const m of marksRef.current) {
      const [u, v] = uvOf(m.ra, m.dec);
      const d = Math.hypot(u * a.side - x, v * a.side - y);
      if (d < bd) {
        bd = d;
        best = m;
      }
    }
    return best;
  };

  const click = (x: number, y: number): boolean => {
    if (!g || !a.single) return false;
    // a tap task (find it again, put the ring on it): the click is a tap at that place, on the frame on show
    const tv = taskRef.current;
    if (tv?.tapping && tool !== 'mark' && tool !== 'ruler') {
      const [ra, dec] = skyOf(x / a.side, y / a.side);
      tv.addTap({ ra, dec, pass: a.pass, frame: a.pass == null ? (a.visit ?? null) : null, mjd: a.epochNow != null ? 51544.5 + (a.epochNow - 2000) * 365.25 : null });
      return true;
    }
    const hit = markAt(x, y);
    if (tool === 'ruler') {
      // a ruler point: the mark under the click, else the click's own place; with the date of the frame on show
      const [ra, dec] = hit ? [hit.ra, hit.dec] : skyOf(x / a.side, y / a.side);
      const p: RulerPoint = { ra, dec, epoch: a.epochNow, pass: a.pass };
      setRuler((r) => (r.length === 1 ? [r[0]!, p] : [p]));
      return true;
    }
    // "tap it here": the click is the mover's place on this pass, even right beside its first mark
    if (hit && !(tool === 'mark' && tapHere)) {
      // a linked dot opens its mover's one mark
      const id = headOf(hit, marksRef.current)?.id ?? hit.id;
      setOpen(id === open ? null : id);
      if (id !== open) a.setTab('cand'); // its card, in the Task tab
      return true;
    }
    if (tool === 'mark') {
      // a guest's first mark: the popup offers to keep the marks under a name (the mark is put all the same, in this browser)
      const [ra, dec] = skyOf(x / a.side, y / a.side);
      if (!si.signedIn) {
        try {
          if (!sessionStorage.getItem('spherex.marks.asked')) {
            sessionStorage.setItem('spherex.marks.asked', '1');
            // the sign-in link comes back here with this mark in the address: it is put there again if this browser lost it
            si.ask('marks', `mark:${ra.toFixed(5)},${dec.toFixed(5)},${a.pass ?? a.daysPass ?? ''}`);
          }
        } catch {
          /* no store: no popup */
        }
      }
      const n = marksRef.current.length + 1;
      const m = addMark({
        field_key: fieldKey(a.ra, a.dec, a.fov),
        mjd: a.epochNow != null ? 51544.5 + (a.epochNow - 2000) * 365.25 : null,
        cell_id: a.params['cell'] ?? null,
        ra,
        dec,
        // in Days the mark belongs to the pass whose visits play (it used to be saved with no pass)
        pass: a.pass ?? a.daysPass,
        label: tapHere ? tapHere.label : `Mark ${n}`,
        level: tapHere ? LEVEL_OF_KIND.moves : 'suspicious',
        ...(tapHere ? { kind: 'moves' as const, link: tapHere.id } : {}),
        note: '',
      });
      if (tapHere) {
        setTapHere(null);
        setTool(null);
        setOpen(tapHere.id); // one mark: its card stays the first one's
        a.setTab('cand');
        return true;
      }
      setTool(null); // one mark a press of the tool: a stray click after it put another one
      setOpen(m.id);
      a.setTab('cand'); // its card, in the Task tab
      return true;
    }
    if (open) setOpen(null); // the click goes on to the lab: a source under it is selected
    return false;
  };

  /** what the data already says about a mark's place: the frame's edge, a bright star beside it ('' when nothing) */
  const hintOf = (m: { ra: number; dec: number }): string => {
    if (!g) return ''; // no picture yet: nothing to say about its edge
    const [u, v] = uvOf(m.ra, m.dec);
    const out: string[] = [];
    const edge = Math.min(u, v, 1 - u, 1 - v);
    if (edge >= 0 && edge < 0.03) out.push('at the frame edge');
    if (known.state === 'ok' && a.epochNow != null) {
      let best: { name: string; mag: number; d: number } | null = null;
      for (const o of known.objects) {
        if (o.source === 'jpl' || o.mag == null || o.mag > 10) continue;
        const at = placeAt(o, a.epochNow);
        if (!at) continue;
        const d = Math.hypot((((at[0] - m.ra + 540) % 360) - 180) * Math.cos((m.dec * Math.PI) / 180), at[1] - m.dec) * 3600;
        // a ghost or a spike of a bright star: within 60″, or 120″ of a star brighter than magnitude 6
        if (d > 2 && d < (o.mag <= 6 ? 120 : 60) && (!best || d < best.d)) best = { name: o.name, mag: o.mag, d };
      }
      if (best) out.push(`${best.d.toFixed(0)}″ from ${best.name || 'a bright star'} (mag ${best.mag.toFixed(1)})`);
    }
    return out.length ? `Artefact hint: ${out.join(' · ')}` : '';
  };

  // Blink of two chosen passes
  const [pair, setPair] = useState<[number | null, number | null]>([null, null]);
  const idx = a.passes.map((q) => q.index);
  const pa = pair[0] != null && idx.includes(pair[0]) ? pair[0] : null;
  const pb = pair[1] != null && idx.includes(pair[1]) && pair[1] !== pa ? pair[1] : null;
  const blink: [number, number] | null = a.blinkable && pa != null && pb != null ? [pa, pb] : null;
  useEffect(() => setPair([null, null]), [a.ra, a.dec, a.fov]);

  // ---- the lab only collects: the one action on a selected object is "Add to investigation" (a new one, or one of mine).
  // Notes, the checks and the submit are in the Investigation page of the Cabinet, nowhere in the lab.
  const [inv, setInv] = useState<{ id: string; title: string; marks: number } | null>(null);
  const [invLine, setInvLine] = useState('');
  /** the user's investigations, asked for when the list is opened: null = closed */
  const [invList, setInvList] = useState<{ id: string; title: string }[] | null>(null);
  useEffect(() => {
    setInvLine('');
    setInvList(null);
  }, [a.ra, a.dec, a.fov, open, a.sel?.ra, a.sel?.dec]);
  // the chip is the investigation of the field it was added from: another field has none until something is added there
  useEffect(() => setInv(null), [a.ra, a.dec, a.fov]);
  const si = useSignIn();
  const openInvList = async () => {
    if (invList) return setInvList(null);
    if (!si.signedIn) return si.ask('add', 'add'); // a guest: the popup; the link comes back with the list open
    setInvLine('');
    try {
      const r = await fetch('/api/me/investigations');
      if (r.status === 401) return setInvLine('Sign in to add to an investigation');
      if (!r.ok) throw new Error(String(r.status));
      const d = (await r.json()) as { investigations?: { id: string; title: string; submitted?: unknown }[] };
      setInvList((d.investigations ?? []).filter((x) => !x.submitted).map((x) => ({ id: x.id, title: x.title })));
    } catch {
      setInvLine('Investigations did not answer');
    }
  };
  /** the object as the investigation keeps it: its place, its marks, the ruler's result and the catalogue match, and this view */
  const addTo = async (target: string, title: string) => {
    const m = open ? (marks.find((x) => x.id === open) ?? null) : null;
    const place = m ? { ra: m.ra, dec: m.dec } : selNow();
    if (!place) return;
    setInvList(null);
    setInvLine('adding…');
    const cat = a.epochNow != null ? catalogueAt(known, place.ra, place.dec, a.epochNow) : null;
    const rl = ruler.length === 2 ? rulerOf(ruler[0]!, ruler[1]!) : null;
    const body = {
      // the service takes letters, digits and _-:. only (a comma was refused with 422): the place as src:RA_Dec
      id: m ? m.id : `src:${place.ra.toFixed(5)}_${place.dec.toFixed(5)}`,
      label: (m ? m.label : a.sel?.name) || `${place.ra.toFixed(5)} ${place.dec >= 0 ? '+' : ''}${place.dec.toFixed(5)}`,
      ra: place.ra,
      dec: place.dec,
      field_key: fieldKey(a.ra, a.dec, a.fov),
      ...(a.params['finding'] ? { finding_id: a.params['finding'] } : {}),
      // a mark is the object itself; a source takes the marks put on it (within 15″)
      mark_ids: m ? [m.id] : marksHere.filter((x) => x.level !== 'artefact' && sep(x.ra, x.dec, place.ra, place.dec) < 15).map((x) => x.id),
      ...(rl && rl.rate != null && rl.dt != null && Math.abs(rl.dt) >= 0.1
        ? {
            ruler: {
              rate: Number(rl.rate.toFixed(3)),
              pa: Number(rl.pa.toFixed(1)),
              shift: Number(rl.shift.toFixed(2)),
              ...(cat?.object?.pm_total != null
                ? { catalogue_rate: Number((cat.object.pm_total / 1000).toFixed(3)), object: cat.object.simbad || cat.object.name }
                : {}),
            },
          }
        : {}),
      ...(cat?.object
        ? {
            catalogue: {
              source: cat.object.source,
              name: cat.object.simbad || cat.object.name || cat.object.id,
              sep: Number((cat.sep ?? 0).toFixed(2)),
              ...(cat.object.type ? { type: cat.object.type } : {}),
            },
          }
        : {}),
      ref: location.hash.slice(0, 600),
      thumb: a.thumb(),
      ...(target === 'new' ? { title } : {}),
    };
    try {
      const r = await fetch(`/api/me/investigations/${encodeURIComponent(target)}/items`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (r.status === 401) return setInvLine('Sign in to add to an investigation');
      if (r.status === 409) return setInvLine('This investigation is closed or full');
      if (r.status >= 400 && r.status < 500) {
        // a refusal, in the service's own words (it did answer)
        const d = (await r.json().catch(() => null)) as { detail?: unknown } | null;
        const why = typeof d?.detail === 'string' ? d.detail : Array.isArray(d?.detail) ? 'the object could not be read' : `the service said ${r.status}`;
        return setInvLine(`Not added: ${why}`);
      }
      if (!r.ok) throw new Error(String(r.status));
      const got = (await r.json()) as { id?: string; title?: string; mark_ids?: string[] };
      if (!got.id) throw new Error('no id');
      setInv({ id: got.id, title: got.title ?? title, marks: got.mark_ids?.length ?? 0 });
      setInvLine(`Added to “${got.title ?? title}”`);
    } catch {
      setInvLine('Not added: the service did not answer');
    }
  };
  // ---- a task about one object (spec §5b): what the lab sets up and shows for its question
  const task = taskOfParams(a.params);
  const taskKey = task ? `${task.kind}|${task.id}|${task.finding_id}|${a.ra}|${a.dec}|${a.fov}` : '';
  const sep = (ra1: number, dec1: number, ra2: number, dec2: number) =>
    Math.hypot((((ra1 - ra2 + 540) % 360) - 180) * Math.cos((dec2 * Math.PI) / 180), dec1 - dec2) * 3600;
  // the object in question: the lab's selected object, else the field's centre (a find is opened at its own place)
  const at = a.sel ?? { ra: a.ra, dec: a.dec };
  // the places the object may be given at: the selected object's catalogue place and its place during the survey (a moving one
  // carries its J2000 place and its motion), the field's centre, and the place the address names for the find
  const spots = useMemo(() => {
    const out: [number, number][] = [[a.ra, a.dec]];
    const fr = Number(a.params['find_ra']);
    const fd = Number(a.params['find_dec']);
    if (a.params['find_ra'] != null && Number.isFinite(fr) && Number.isFinite(fd)) out.push([fr, fd]);
    if (a.sel) {
      out.push([a.sel.ra, a.sel.dec]);
      if (a.sel.pmra || a.sel.pmdec) out.push(atEpoch(a.sel.ra, a.sel.dec, a.sel.pmra ?? 0, a.sel.pmdec ?? 0, SURVEY_EPOCH));
    }
    return out;
  }, [a.ra, a.dec, a.sel?.ra, a.sel?.dec, a.sel?.pmra, a.sel?.pmdec, a.params['find_ra'], a.params['find_dec']]);
  const nearSpot = (ra: number, dec: number) => Math.min(...spots.map(([r, d]) => sep(ra, dec, r, d)));
  // the track of the search at the object: the nearest within 20″ of any of its places; kept for the task once found
  const found = useRef<{ key: string; track: number; anomaly: number }>({ key: '', track: -1, anomaly: -1 });
  if (found.current.key !== taskKey) found.current = { key: taskKey, track: -1, anomaly: -1 };
  const trackIx = useMemo(() => {
    const dOf = (t: FlowTrack) => Math.min(nearSpot(t.ra_2025_9, t.dec_2025_9), ...t.ra.map((r, k) => nearSpot(r, t.dec[k]!)));
    // a kept track must still be at the object (right after a move the old field's tracks and selection are still in the state)
    const kept = a.tracks[found.current.track];
    if (found.current.track >= 0 && kept && dOf(kept) < 20 && sep(kept.ra_2025_9, kept.dec_2025_9, a.ra, a.dec) < a.fov * 60) return found.current.track;
    let best = -1;
    let bd = 20;
    a.tracks.forEach((t, i) => {
      if (sep(t.ra_2025_9, t.dec_2025_9, a.ra, a.dec) > a.fov * 60) return; // not in this field
      const d = dOf(t);
      if (d < bd) {
        bd = d;
        best = i;
      }
    });
    found.current.track = best;
    return best;
  }, [a.tracks, spots, taskKey]);
  const track = trackIx >= 0 ? a.tracks[trackIx]! : null;
  const anomaly = useMemo(() => {
    const kept = a.anomalies.find((x) => x.n === found.current.anomaly);
    if (kept) return kept;
    let best: Anomaly | null = null;
    let bd = 15;
    for (const x of a.anomalies) {
      const now = atEpoch(x.ra, x.dec, x.pmra ?? 0, x.pmdec ?? 0, SURVEY_EPOCH);
      const d = Math.min(nearSpot(x.ra, x.dec), nearSpot(now[0], now[1]));
      if (d < bd) {
        bd = d;
        best = x;
      }
    }
    found.current.anomaly = best?.n ?? -1;
    return best;
  }, [a.anomalies, spots, taskKey]);
  const labelOf = (i: number) => a.passes.find((q) => q.index === i)?.label ?? `pass ${i + 1}`;
  const taskSrc: Source | null = task && a.grid ? at : null;
  /** the two passes of a mover's track farthest apart: the Blink plays those two, the ruler lies from one place to the other */
  const blinkTrack = (t: FlowTrack) => {
    let i0 = 0;
    let i1 = 1;
    let far = -1;
    for (let i = 0; i < t.passes.length; i++)
      for (let j = i + 1; j < t.passes.length; j++) {
        const d = sep(t.ra[i]!, t.dec[i]!, t.ra[j]!, t.dec[j]!);
        if (d > far) {
          far = d;
          i0 = i;
          i1 = j;
        }
      }
    const yr = (mjd: number) => 2000 + (mjd - 51544.5) / 365.25;
    setPair([t.passes[i0]!, t.passes[i1]!]);
    setRuler([
      { ra: t.ra[i0]!, dec: t.dec[i0]!, epoch: yr(t.mjd[i0]!), pass: t.passes[i0]! },
      { ra: t.ra[i1]!, dec: t.dec[i1]!, epoch: yr(t.mjd[i1]!), pass: t.passes[i1]! },
    ]);
    autoRuler.current = true;
  };
  // the panel's own pictures of the step (the two sums, the odd pictures) are on show
  const [panelShown, setPanelShown] = useState(false);
  const [panelNone, setPanelNone] = useState(false); // they cannot be made: only "Not sure" can be said
  // what the lab shows with the task: for a series step its `show`, else the task's kind decides
  const view = (show: string | null, body: NextTask | null): ComponentChildren => {
    const kind = task?.kind;
    if (show === 'blink2' || (!show && kind === 'moves'))
      return (
        <MoverView
          blink={blink ? `${labelOf(blink[0])} and ${labelOf(blink[1])}` : track ? 'all passes' : 'waits for the search of the field'}
          ruler={ruler.length === 2 ? rulerSays(ruler) : '—'}
          search={
            track
              ? `${track.speed.toFixed(1)}″/yr toward ${track.pa.toFixed(0)}° · ${track.passes.length} passes`
              : 'no track at this place yet'
          }
        />
      );
    if (show === 'sums' || (!show && (kind === 'finding' || kind === 'second_look'))) return <FindingClip id={body?.finding_id ?? task?.finding_id ?? null} onShown={setPanelShown} onNone={setPanelNone} />;
    // a red source: its own pass after pass, ringed, in the task panel over the sky (the sky is not made smaller); before, only the
    // two example pictures stood there and the object was in the field's small 2′ inset (27 Nov 2026, С1)
    if (show === 'picture' && (body?.finding_id ?? task?.finding_id ?? '').startsWith('red:'))
      return <FindingClip id={body?.finding_id ?? task?.finding_id ?? null} />;
    if (show === 'stamps') {
      const own = body?.anomaly;
      const src: Source | null = own ? { ra: own.ra, dec: own.dec, pmra: own.pmra ?? 0, pmdec: own.pmdec ?? 0 } : anomaly ? taskSrc : null;
      return <StampsView src={src} drivers={own?.drivers ?? anomaly?.drivers ?? null} onShown={setPanelShown} onNone={setPanelNone} />;
    }
    if (show === 'blink' || show === 'pictures')
      return (
        <div class="tv-line">
          {body?.pictures?.length
            ? `Seen in ${body.pictures.length} of its pass's ${a.nVisits || '…'} visits`
            : 'The visits of its pass'}
        </div>
      );
    if (show === 'old_surveys' || show === 'old_new') return <div class="tv-line">Old surveys play first: DSS 1985–99 · 2MASS 1997–2001 · WISE 2010</div>;
    if (kind === 'bad_picture')
      return (
        <div class="tv tv-clip">
          <div class="tv-clipbox">{body?.cutout ? <img src={body.cutout} alt="The exposure" /> : null}</div>
          <div class="tv-line" title={body?.exposure ?? ''}>
            {body?.exposure ? `Exposure ${body.exposure}` : 'asking for the exposure…'}
          </div>
        </div>
      );
    if (kind === 'find_again') return <div class="tv-line">{body?.predicted ? 'The strip where it should be is drawn on the next pass' : 'The next pass · a ring at its last place'}</div>;
    return null;
  };
  const others = a.params['others'] === '1';
  const taskView = useLabTask({
    view,
    others,
    setOthers: (on) => a.set({ others: on ? '1' : null }),
    params: a.params,
    open: a.open,
    ra: a.ra,
    dec: a.dec,
    fieldKey: a.grid ? fieldKey(a.ra, a.dec, a.fov) : null,
    marksHere,
    pass: a.pass,
    passesTotal: a.passes.length,
    square: a.square && a.square.outline ? a.square : null,
    markMissed: () => {
      setRuler([]);
      setTool('mark');
    },
    showPicture: (mjd) => a.showVisit(mjd),
    visitOf: a.visitOf,
    ready: a.ready,
    shown: panelShown,
    noPictures: panelNone,
  });
  // a square given for a lead in it (`lead=`): the lead's own question series, first, above the square's look
  const leadParams = useMemo<Record<string, string>>(
    (): Record<string, string> => {
      const lead = a.params['lead'];
      // the series the service names for it (why.ask.kind: moves, red, anomaly…), else the generic check of a find
      const kind = a.params['lead_kind'] || 'finding';
      return task?.kind === 'cell' && lead ? { task: kind, kind, finding: lead, find: lead } : {};
    },
    [task?.kind, a.params['lead'], a.params['lead_kind']],
  );
  const leadView = useLabTask({
    view,
    others,
    setOthers: (on) => a.set({ others: on ? '1' : null }),
    params: leadParams,
    open: a.open,
    ra: a.ra,
    dec: a.dec,
    fieldKey: a.grid ? fieldKey(a.ra, a.dec, a.fov) : null,
    marksHere,
    pass: a.pass,
    passesTotal: a.passes.length,
    square: null,
    markMissed: () => {
      setRuler([]);
      setTool('mark');
    },
    showPicture: (mjd) => a.showVisit(mjd),
    visitOf: a.visitOf,
    ready: a.ready,
    shown: panelShown,
    noPictures: panelNone,
    embedded: true,
    ...(a.params['lead_text'] ? { leadText: a.params['lead_text'] } : {}),
  });
  // the series the lab is set up for: the lead's while its questions are open, else the task's own
  const leadOpen = !!leadView.kind && leadView.blind;
  const act = leadOpen ? leadView : taskView;
  const actTask = leadOpen ? taskOfParams(leadParams) : task;
  const blindAny = taskView.blind || leadView.blind;
  // while the lead's questions are open the lab shows the lead's own field (the service's place and size for its task: the whole
  // track in sight, never at the quarter's edge); the quarter's own place waits in q_ra/q_dec/q_fov and comes back after
  const leadBody = leadView.body;
  useEffect(() => {
    const p = a.params;
    // (only a track needs its own field: any other lead stands in the quarter that is on show, and moving off it built another field)
    if (leadOpen && leadBody?.ra != null && leadBody.dec != null && leadBody.finding_id?.startsWith('film:') && p['q_ra'] == null) {
      const fov = leadBody.fov && leadBody.fov > 0 ? leadBody.fov : 10;
      a.open({ ...p, ra: leadBody.ra.toFixed(5), dec: leadBody.dec.toFixed(5), fov: String(fov), q_ra: p['ra'] ?? '', q_dec: p['dec'] ?? '', q_fov: p['fov'] ?? '10' });
    } else if (!leadOpen && leadView.kind && p['q_ra'] != null) {
      const { q_ra, q_dec, q_fov, ...rest } = p;
      a.open({ ...rest, ra: q_ra ?? '', dec: q_dec ?? '', fov: q_fov || '10' });
    }
  }, [leadOpen, leadBody?.ra, leadBody?.dec]);
  const taskRef = useRef({ ...taskView, blind: blindAny });
  taskRef.current = { ...taskView, blind: blindAny };
  // set up the lab for the step on show (once per step, when what it needs has arrived)
  const setUp = useRef('');
  const stepKey = `${taskKey}|${leadOpen ? 'lead' : ''}|${act.show ?? ''}|${act.body ? 1 : 0}`;
  useEffect(() => {
    const task = actTask;
    if (!task || setUp.current === stepKey) return;
    const show = act.show;
    const body = act.body;
    const visits = (body?.pictures ?? []).map((q) => q.mjd).filter((m): m is number => typeof m === 'number');
    // the old surveys belong to their own step: any other step leaves them (an archive left in the address by another task, or kept
    // by a reload, hid the step's own pictures: test of 27 Nov, 86.4, Н1)
    if (show !== 'old_surveys' && show !== 'old_new' && a.params['arch'] === '1') a.set({ arch: null });
    if (show === 'blink' || show === 'pictures') {
      // a track: the visits of its pass, in the Days scale
      if (!visits.length) return;
      setUp.current = stepKey;
      // the track's own pass: a pass left in the address from before (another task, another find) is not this track's
      const cur = Number(a.params['film']);
      const lo = Math.min(...visits) - 2;
      const hi = Math.max(...visits) + 2;
      // the film the track's search ran on when the service says it (built and warmed: no new film is made), else the middle of the visits
      const own = typeof body?.film === 'number' && body.film >= lo && body.film <= hi ? String(body.film) : null;
      if (!(cur >= lo && cur <= hi)) a.set({ film: own ?? (visits.reduce((x, y) => x + y, 0) / visits.length).toFixed(3), arch: null });
    } else if (show === 'blink2' || (!show && task.kind === 'moves')) {
      // two passes are a Months view: out of Days at once (a track task before this one may have left it there)
      if (a.params['film']) a.set({ film: null });
      if (!track || track.passes.length < 2) return;
      setUp.current = stepKey;
      blinkTrack(track);
      a.pickTrack(trackIx);
    } else if (show === 'old_surveys' || show === 'old_new') {
      setUp.current = stepKey;
      a.set({ arch: '1', film: null });
    } else if (show === 'passes') {
      setUp.current = stepKey;
      setPair([null, null]); // every pass plays
      a.set({ arch: null, film: null });
    } else if (show === 'stamps') {
      if (!anomaly) return;
      setUp.current = stepKey;
      a.pickAnomaly(anomaly.n);
    } else if (task.kind === 'find_again' && body?.predicted?.mjd != null) {
      setUp.current = stepKey;
      a.showPass(body.predicted.mjd);
    } else setUp.current = stepKey;
  }, [stepKey, track, anomaly]);
  // a square given for its lead: two cards in their order, "1 · Lead" then "2 · Task"; the one not open folds to its header line, so
  // the order is said and the sheet at rest holds one question (two stacked cards took the picture's height, 20 Nov 2026)
  const taskPanel = leadView.kind ? (
    <div class="tp-stack" data-open={leadOpen ? 'lead' : 'task'} data-testid="task-stack">
      {leadView.panel}
      {taskView.panel}
    </div>
  ) : (
    taskView.panel
  );

  const cur = open ? (marks.find((m) => m.id === open) ?? null) : null;
  // the open mover mark's checks (its investigation): followed while they run, ended by their verdict
  const curGroup = cur ? [cur.id, ...marks.filter((q) => q.id !== cur.id && headOf(q, marks)?.id === cur.id).map((q) => q.id)] : [];
  const markChecks = useMarkChecks(cur?.kind === 'moves' ? curGroup : [], si.signedIn);
  // every mark's verdict from the service (known, no match, waiting for JPL…): asked again when a mark changes
  const verdicts = useMarkVerdicts(si.signedIn, marks.map((m) => `${m.id}:${m.kind ?? ''}`).join(','));
  // Esc leaves a tool, then closes the open mark
  const toolRef = useRef(tool);
  toolRef.current = tool;
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (toolRef.current) {
        setTool(null);
        setRuler([]);
      } else if (openRef.current) setOpen(null);
    };
    window.addEventListener('keydown', on);
    return () => window.removeEventListener('keydown', on);
  }, []);
  // the lab's selected source changed: the card is that source's, not the mark's
  useEffect(() => setOpen(null), [a.sel?.ra, a.sel?.dec]);

  const pick = (t: FlowTool) => {
    setTool((c) => (c === t ? null : t));
    if (t !== 'ruler' || tool === 'ruler') setRuler([]);
  };
  // Mark and Ruler are modes of the picture: a small strip on its edge
  const strip =
    a.single && g ? (
      <div class="float toolstrip" role="group" aria-label="Picture tools" data-testid="tool-strip">
        {tool && (
          // what the tool waits for, beside it and whole (the status line cut it off)
          <span class="tool-tip" role="status" data-testid="tool-tip">
            {tool === 'mark' ? 'Click a place to mark it' : ruler.length === 2 ? rulerSays(ruler) : ruler.length ? 'Now click the second place' : 'Click two places to measure'}
          </span>
        )}
        <button
          class={tool === 'mark' ? 'on' : ''}
          aria-pressed={tool === 'mark'}
          aria-label="Mark"
          title="Mark (Esc leaves): click the picture to put a point; click a mark to open its card"
          onClick={() => pick('mark')}
          data-testid="tool-mark"
          dangerouslySetInnerHTML={{ __html: ICON_MARK }}
        />
        <button
          class={tool === 'ruler' ? 'on' : ''}
          aria-pressed={tool === 'ruler'}
          aria-label="Ruler"
          title="Ruler (Esc leaves): click two places, two marks, or the same source on two passes: the shift, the rate, the direction"
          onClick={() => pick('ruler')}
          data-testid="tool-ruler"
          dangerouslySetInnerHTML={{ __html: ICON_RULER }}
        />
      </div>
    ) : null;

  // Blink of two chosen passes: two compact pass pickers in the playback bar, next to Blink (they keep their place when Blink is
  // not the mode, so nothing in the bar moves)
  const blinkPick = (
    <span
      class="blinkpick"
      style={{ visibility: a.blinkable && idx.length > 1 ? 'visible' : 'hidden' }}
      role="group"
      aria-label="Blink of two passes"
    >
      {([0, 1] as const).map((k) => (
        <select
          key={k}
          class="inp"
          aria-label={k ? 'Second pass of the Blink' : 'First pass of the Blink'}
          title="Blink of two passes: choose two and only those play"
          value={String((k ? pb : pa) ?? '')}
          onChange={(e) => {
            const raw = (e.target as HTMLSelectElement).value;
            const v = raw === '' ? null : Number(raw);
            setPair(k ? [pa, v] : [v, pb]);
          }}
          data-testid={`blink-${k ? 'b' : 'a'}`}
        >
          {/* nothing chosen: every pass plays in turn ("A · all / B · all" read as all against all, nothing to blink: test of 27 Nov, M8) */}
          <option value="">{k ? (pa == null ? 'or pick two' : 'B · pick') : 'All passes'}</option>
          {a.passes.map((q) => (
            <option key={q.index} value={String(q.index)} disabled={q.index === (k ? pa : pb)}>
              {`${k ? 'B' : 'A'} · ${q.label}`}
            </option>
          ))}
        </select>
      ))}
    </span>
  );

  // ---- the card of what is selected (Object tab): a mark, or the lab's selected source. Note, "Add to investigation" and
  // "Check and submit" exist only here: with nothing selected there is nothing to note or submit.
  const matchAt = (ra: number, dec: number): string => {
    // a task's find not answered yet: the catalogues' word waits for the answers (a control object looks like any other)
    if (!cur && blindAny && isBlindPlace(ra, dec, a.findAt, taskTrack.pts)) return 'after your answers';
    const w = a.epochNow != null ? catalogueAt(known, ra, dec, a.epochNow).word : 'asking the catalogues…';
    // a find: JPL's answer is the find's own (asked for every visit of its track), never the picture's lookup, which may still be
    // waiting: "JPL did not answer" beside an UNEXPLAINED badge read as a contradiction
    const jpl = !cur ? a.findIdents?.find((i) => i.catalogue === 'sbident') : undefined;
    const w2 = !cur ? withFindStatus(w, a.findStatus) : w;
    const w3 = jpl ? withFindJpl(w2, jpl.state) : w2;
    // a find the service calls known (JPL named its body, SIMBAD its star): the line says so, as the find's card does
    return !cur && a.findStatus === 'known' ? withFindKnown(w3, a.findIdents) : w3;
  };
  // where the selected source is on the date shown: a moving one carries its J2000 place and its motion
  const selNow = (): { ra: number; dec: number } | null => {
    if (!a.sel) return null;
    if (!(a.sel.pmra || a.sel.pmdec)) return { ra: a.sel.ra, dec: a.sel.dec };
    const [r, d] = atEpoch(a.sel.ra, a.sel.dec, a.sel.pmra ?? 0, a.sel.pmdec ?? 0, a.epochNow ?? SURVEY_EPOCH);
    return { ra: r, dec: d };
  };
  const selPlace = cur ? { ra: cur.ra, dec: cur.dec } : selNow();
  const rulerNow = ruler.length === 2 ? rulerSays(ruler) : '';
  const newTitle = (cur ? cur.label : a.sel?.name) || `Lab ${a.ra.toFixed(4)} ${a.dec.toFixed(4)}`;
  const actions = (
    <>
      <div class="oc-row oc-add">
        <button
          class="btn sm primary"
          aria-expanded={!!invList}
          onClick={() => void openInvList()}
          title="An investigation is your find with its evidence (marks, the ruler, catalogue answers, this view): the checks run on it and you submit it from the Cabinet"
          data-testid="tool-add"
        >
          Add to investigation
        </button>
        {invList && (
          <div class="oc-menu" role="menu" data-testid="inv-menu">
            <button role="menuitem" onClick={() => void addTo('new', newTitle)} data-testid="inv-new">
              New investigation
            </button>
            {invList.map((x) => (
              <button key={x.id} role="menuitem" onClick={() => void addTo(x.id, x.title)}>
                {x.title}
              </button>
            ))}
          </div>
        )}
      </div>
      <div class="oc-line" data-testid="tools-line">
        {invLine}
        {inv && invLine.startsWith('Added') && (
          // where a find goes from here: its checks, then the submit, in the Cabinet
          <>
            {' · '}
            <a class="cb-link" href={`#/me?tab=investigations&inv=${encodeURIComponent(inv.id)}`} data-testid="inv-submit">
              Check and submit ›
            </a>
          </>
        )}
      </div>
    </>
  );
  // after a sign-in link that came from a mark: the mark is there (put again if this browser did not keep it), its card open
  useEffect(() => {
    const want = pendingDo(a.params);
    if (!want.startsWith('mark:') || !si.signedIn || !g) return;
    clearDo();
    const [mra, mdec, mpass] = want.slice(5).split(',');
    const ra = Number(mra);
    const dec = Number(mdec);
    if (!Number.isFinite(ra) || !Number.isFinite(dec)) return;
    const near = marksRef.current.find((q) => Math.hypot((q.ra - ra) * Math.cos((dec * Math.PI) / 180), q.dec - dec) * 3600 < 3);
    const m =
      near ??
      addMark({
        field_key: fieldKey(a.ra, a.dec, a.fov),
        mjd: a.epochNow != null ? 51544.5 + (a.epochNow - 2000) * 365.25 : null,
        cell_id: a.params['cell'] ?? null,
        ra,
        dec,
        pass: mpass ? Number(mpass) : (a.pass ?? a.daysPass),
        label: `Mark ${marksRef.current.length + 1}`,
        level: 'suspicious',
        note: '',
      });
    setOpen(m.id);
    a.setTab('cand');
  }, [si.signedIn, !!g, a.params['do']]);
  // after a sign-in link that came from "Add to investigation": the list opens once the selection is back
  useEffect(() => {
    if (pendingDo(a.params) === 'add' && si.signedIn && (a.sel || open)) {
      clearDo();
      void openInvList();
    }
  }, [si.signedIn, !!a.sel, open]);
  // a mark's way to the checks, in three steps: marked · what it is · what the service does with it (spec §5g)
  const markEff = lastMarkEffect();
  const stepThree = (m: Mark): { text: string; state: 'wait' | 'now' | 'done' | 'bad' | 'known'; signIn?: boolean } => {
    if (!m.kind) return { text: 'Checks', state: 'wait' };
    if (tapHere && tapHere.id === m.id) return { text: `Tap it in ${tapHere.passLabel}`, state: 'now' };
    if (!si.signedIn) return { text: 'Sign in to send it', state: 'now', signIn: true };
    // the service's own verdict on this mark (or on its mover's other mark), when it has one and no checks are running
    const vd = [m.id, ...marks.filter((q) => headOf(q, marks)?.id === m.id).map((q) => q.id)].map((id) => verdicts[id]).find(Boolean);
    if (vd && vd.state !== 'checking') {
      // known: a final answer, not a fault (its own quiet look); no catalogue match: the good news; the rest still waits
      const st: 'now' | 'done' | 'known' = vd.state === 'known' ? 'known' : vd.state === 'no_match' ? 'done' : 'now';
      return { text: vd.text, state: st };
    }
    if (m.kind === 'moves') {
      // the checks of its investigation, as they answer, then their verdict
      if (markChecks && markChecks !== 'none') {
        // the pill says it in a word; the verdict in full is the line under the checks
        const v = checksVerdict(markChecks);
        const rows = markChecks.checks;
        const answered = rows.filter((x) => ['pass', 'warn', 'fail'].includes(x.state)).length;
        return { text: v.state === 'bad' ? 'Checks ✗' : v.state === 'done' ? 'Checks ✓' : `Checks · ${answered} of ${rows.length || 8}`, state: v.state };
      }
      // one pass only: the checks start with its mark on another pass
      if (curGroup.length < 2 && a.passes.length >= 2) return { text: 'Mark it on another pass too', state: 'now' };
      return { text: markChecks === 'none' ? 'waiting for the service' : 'asking…', state: 'now' };
    }
    const mine = markEff && (markEff.id === m.id || marks.some((q) => q.id === markEff.id && headOf(q, marks)?.id === m.id));
    const w = mine ? effectWord(markEff) : '';
    // what the service did with a mark of this kind (said at once when it was put; after a reload, what it does)
    return { text: w || SENT_WORD[m.kind], state: 'done' };
  };
  const card =
    cur || a.sel ? (
      <div class="objcard" data-testid="flow-card">
        {cur ? (
          <>
            <div class="oc-head">
              <i class="oc-glyph" style={{ borderColor: markColour(cur) }} />
              <input
                class="inp"
                value={cur.label}
                maxLength={60}
                aria-label="Label of the mark"
                spellcheck={false}
                onInput={(e) => updateMark(cur.id, { label: (e.target as HTMLInputElement).value })}
                data-testid="mark-label"
              />
              <button
                class="mini"
                title="Remove this mark"
                onClick={() => {
                  for (const q of marks) if (q.id !== cur.id && headOf(q, marks)?.id === cur.id) removeMark(q.id);
                  removeMark(cur.id);
                  setOpen(null);
                }}
                data-testid="mark-remove"
              >
                Remove
              </button>
            </div>
            {(() => {
              const s3 = stepThree(cur);
              return (
                <ol class="oc-steps" aria-label="The way to the checks" data-testid="mark-steps">
                  <li class="done">Marked</li>
                  <li class={cur.kind ? 'done' : 'now'}>{cur.kind ? KIND_SHORT[cur.kind] : 'What is it?'}</li>
                  <li class={s3.state} title="What the service does with it: a search on two passes, a catalogue lookup, a count of artefacts, the Talk">
                    {s3.signIn ? (
                      <button class="lnk" onClick={() => si.ask('marks', `mark:${cur.ra.toFixed(5)},${cur.dec.toFixed(5)},${cur.pass ?? ''}`)}>
                        {s3.text}
                      </button>
                    ) : (
                      s3.text
                    )}
                  </li>
                </ol>
              );
            })()}
            {cur.kind === 'moves' && markChecks && markChecks !== 'none' && markChecks.checks.length > 0 && (
              <div class="oc-checks" data-testid="mark-checks">
                <div class={`oc-verdict v-${checksVerdict(markChecks).state}`} data-testid="mark-verdict">
                  {checksVerdict(markChecks).text}
                </div>
                {markChecks.checks.map((c) => (
                  <span key={c.id} class={`ck ck-${c.state}`} title={c.detail || c.state}>
                    {CHECK_GLYPH[c.state] ?? '…'} {CHECK_WORD[c.id] ?? c.id}
                  </span>
                ))}
                <a class="cb-link" href={`#/me?tab=investigations&inv=${encodeURIComponent(markChecks.inv)}`}>
                  {markChecks.state === 'ready' || markChecks.state === 'needs_comment' ? 'Check and submit ›' : 'In the Cabinet ›'}
                </a>
              </div>
            )}
            <div class="oc-kinds" role="group" aria-label="What it is" data-testid="mark-kinds">
              {MARK_KINDS.map((k) => (
                <button
                  key={k}
                  class={cur.kind === k ? 'on' : ''}
                  aria-pressed={cur.kind === k}
                  onClick={() => {
                    updateMark(cur.id, { kind: k, level: LEVEL_OF_KIND[k], look: k === 'artefact' ? (cur.look ?? null) : null });
                    if (k !== 'moves' || cur.pass == null || a.passes.length < 2) return setTapHere(null);
                    // a partner: a mover mark within a minute of arc on another pass
                    const partner = marks.some(
                      (m) => m.id !== cur.id && m.kind === 'moves' && m.pass != null && m.pass !== cur.pass && Math.hypot((m.ra - cur.ra) * Math.cos((cur.dec * Math.PI) / 180), m.dec - cur.dec) * 3600 < 60,
                    );
                    if (partner) return;
                    const far = [...a.passes].sort((x, y) => Math.abs(y.index - cur.pass!) - Math.abs(x.index - cur.pass!))[0]!;
                    a.showFrame(far.index);
                    setTool('mark');
                    setTapHere({ id: cur.id, label: cur.label, ra: cur.ra, dec: cur.dec, pass: far.index, passLabel: far.label });
                  }}
                  data-testid={`mark-kind-${k}`}
                >
                  <i style={{ background: KIND_COLOUR[k] }} />
                  {KIND_WORD[k]}
                </button>
              ))}
            </div>
            {cur.kind === 'artefact' && (
              <div class="oc-looks" role="group" aria-label="What it looks like" data-testid="mark-looks">
                {MARK_LOOKS.map((l) => (
                  <button key={l} class={cur.look === l ? 'on' : ''} aria-pressed={cur.look === l} onClick={() => updateMark(cur.id, { look: l })} data-testid={`mark-look-${l}`}>
                    {LOOK_WORD[l]}
                  </button>
                ))}
              </div>
            )}
            <dl class="oc-facts">
              <dt>Put on</dt>
              <dd>{cur.pass != null ? (a.passes.find((q) => q.index === cur.pass)?.label ?? `pass ${cur.pass + 1}`) : 'no pass'}</dd>
              <dt>Catalogues</dt>
              <dd data-testid="mark-match">{matchAt(cur.ra, cur.dec)}</dd>
              {rulerNow && (
                <>
                  <dt>Ruler</dt>
                  <dd>{rulerNow}</dd>
                </>
              )}
              {hintOf(cur) && (
                <>
                  <dt>Hint</dt>
                  <dd class="warn" data-testid="mark-hint">
                    {hintOf(cur).replace(/^Artefact hint: /, '')}
                  </dd>
                </>
              )}
            </dl>
            {actions}
          </>
        ) : a.sel && selPlace ? (
          <>
            <dl class="oc-facts">
              <dt>Catalogues</dt>
              <dd data-testid="sel-match">{matchAt(selPlace.ra, selPlace.dec)}</dd>
              {rulerNow && (
                <>
                  <dt>Ruler</dt>
                  <dd>{rulerNow}</dd>
                </>
              )}
            </dl>
            {actions}
          </>
        ) : null}
      </div>
    ) : null;

  // the field's marks, one row per mark (a mover marked on more passes is one row): in the Task tab
  const marksList =
    marksHere.length > 0 ? (
          <div class="oc-marks" data-testid="marks-list">
            <div class="psub">Marks in this field</div>
            {marksHere
              .filter((m) => !headOf(m, marks))
              .map((m) => {
                // a mover marked on more passes is one row: "It moves · 2 passes"
                const seen = 1 + marks.filter((q) => q.id !== m.id && headOf(q, marks)?.id === m.id).length;
                return (
                  <button key={m.id} class={`ft-mark ${m.id === open ? 'on' : ''}`} onClick={() => setOpen(m.id === open ? null : m.id)}>
                    <i style={{ background: markColour(m) }} />
                    <b>{m.label || 'Mark'}</b>
                    <span>{m.kind ? `${KIND_SHORT[m.kind]}${seen > 1 ? ` · ${seen} passes` : ''}` : 'no kind yet'}</span>
                  </button>
                );
              })}
          </div>
    ) : null;

  // the investigation of the field is seen on the picture's status line: a chip that opens it in the Cabinet
  const chip = inv ? (
    <a
      class="invchip"
      href={`#/me?tab=investigations&inv=${encodeURIComponent(inv.id)}`}
      title={`Open “${inv.title}” in the Cabinet`}
      data-testid="inv-chip"
    >
      Investigation · {inv.marks} mark{inv.marks === 1 ? '' : 's'} · check and submit ›
    </a>
  ) : null;

  const rulerStatus =
    tool === 'ruler' && ruler.length < 2
      ? ruler.length
        ? 'Ruler: click the second place'
        : 'Ruler: click the first place'
      : tool === 'ruler' && ruler.length === 2
        ? `Ruler: ${rulerSays(ruler)}`
        : '';
  const eff = lastMarkEffect();
  // the server decides: a mover it already checks (linked to marks nearby) needs no second tap
  useEffect(() => {
    if (tapHere && eff && eff.id === tapHere.id && eff.effect === 'moves' && eff.state !== 'needs_another_pass') {
      setTapHere(null);
      setTool(null);
    }
  }, [eff, tapHere?.id]);
  const status = [
    peopleTrack?.state ? `people: ${peopleTrack.state === 'relinked' ? `relinked · ${peopleTrack.rate != null ? `${peopleTrack.rate.toFixed(1)}″/h` : ''}${peopleTrack.missing?.length ? ` without ${peopleTrack.missing.length} visit${peopleTrack.missing.length === 1 ? '' : 's'}` : ''}` : peopleTrack.state}` : '',
    tapHere ? `It moves · tap it here in ${tapHere.passLabel}` : open && eff && (eff.id === open || marks.some((m) => m.id === eff.id && headOf(m, marks)?.id === open)) ? effectWord(eff) : '',
    a.square?.line ?? '',
    a.known ? knownStatus(known, counts, knownSilent ? knownRetry.nextIn : null) : '',
    tool === 'ruler' && ruler.length < 2 ? '' : rulerStatus,
    marksSyncNote(),
  ]
    .filter(Boolean)
    .join(' · ');
  // what a closed task earned stays on the line for a few seconds
  const [awardNow, setAwardNow] = useState('');
  useEffect(() => {
    const t = setInterval(() => setAwardNow(awardLine()), 1000); // a new render only when the line changes
    return () => clearInterval(t);
  }, []);

  // the selected source as the field's lookup knows it, on the date shown (a moving one from its J2000 place and motion)
  const heldPlace = a.sel ? selNow() : null;
  const selHeld = heldPlace && a.epochNow != null ? heldAt(known, heldPlace.ra, heldPlace.dec, a.epochNow) : null;
  return {
    selHeld,
    blind: blindAny,
    trackSum: act.series === 'track' && act.show === 'sums',
    leadOpen,
    draw,
    pointer: (p) => {
      ptr.current = p;
    },
    click,
    status: [signinNote, status, awardNow].filter(Boolean).join(' · '),
    floats: strip,
    blinkPick,
    card: (
      <>
        {card}
        {si.popup}
      </>
    ),
    marksList,
    asideAt: (ra, dec) => people.key === pKey && people.finds.some((f) => f.set_aside && sep(ra, dec, Number(f.ra), Number(f.dec)) < 6),
    chip,
    markOpen: !!cur,
    catalogueAt: (ra, dec, year) => catalogueAt(knownRef.current, ra, dec, year).object,
    hintAt: (ra, dec) => hintOf({ ra, dec }),
    tool,
    marks,
    marksHere,
    ruler,
    setTool,
    blink,
    taskPanel,
    focus: !!task && focusKind(task.kind) && !others,
    peopleSearch: {
      name: "people's marks",
      state: !a.people ? 'idle' : people.key !== pKey || people.state === 'running' ? 'running' : people.state === 'failed' ? 'failed' : 'done',
      count: people.key === pKey && people.state === 'done' ? String(people.places.filter((q) => !(q.mine && q.people === 1)).length) : undefined,
      ours: true,
    },
    knownSearch: {
      name: 'known objects',
      state:
        known.state === 'off'
          ? 'idle'
          : known.state === 'loading' || (known.state === 'ok' && (known.pending.length > 0 || known.deepPending.length > 0))
            ? 'running'
            : knownSilent
              ? 'failed'
              : 'done',
      count: known.state === 'ok' ? String(known.objects.length) : undefined,
      retry: knownSilent ? knownRetry : null,
    },
    knownBusy:
      a.known && (known.state === 'loading' || (known.state === 'ok' && (known.pending.length > 0 || known.deepPending.length > 0))),
  };
}
