// The product-flow pieces of the lab (product spec §5 Lab): Known objects, marks, the ruler. One hook, `useLabFlow`, which lab.tsx
// calls; it returns what the lab draws on its overlay, what it puts over the picture and in the Controls panel, and its status line.
import type { ComponentChildren } from 'preact';
import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { addMark, allMarks, fieldKey, MARK_LEVELS, marksSyncNote, onMarks, removeMark, updateMark, type Mark, type MarkLevel } from '../../lib/marks';
import { drawKnown, followKnown, KNOWN_OFF, knownStatus, knownUrl, matchKnown, type KnownDrawEnv, type KnownMatch, type KnownState } from './known';
import { findPeaks, type Peak } from './detect';
import { rulerOf, rulerWord, type RulerPoint } from './ruler';
import { awardLine, ChecksList, checksStatus, chipWord, declaredWord, declareSubmit, focusKind, followDeclared, taskOfParams, useChecks, useLabTask, type DeclaredInv } from './task';
import { SOURCE_WORD } from './known';
import { AnomalyView, MoverView, RedView } from './taskviews';
import type { Anomaly, Source } from '../spectrum';
import { motionOf } from './known';
import { placeAt } from './known';
import { atEpoch, SURVEY_EPOCH } from '../../lib/sky';
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
  /** the picture of the pass on show and its noise (null in Days, on a wall, or before it has arrived) */
  picture: { pass: number; n: number; rgb: Uint16Array; bg: number; sigma: number; step: number } | null;
}

const LEVEL_COLOUR: Record<MarkLevel, string> = { suspicious: '#ff9d5c', interesting: '#7fd8ff', artefact: '#aab4b9' };
const LEVEL_WORD: Record<MarkLevel, string> = { suspicious: 'Suspicious', interesting: 'Interesting', artefact: 'Artefact' };

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
  /** in the Object tab: the card of what is selected (a mark or a source) with its note and the two actions, and the field's marks */
  card: ComponentChildren;
  /** on the picture's status line: the investigation of the field, opening it in the Cabinet */
  chip: ComponentChildren;
  /** a mark's card is open: the Object tab is the mark's alone */
  markOpen: boolean;
  tool: FlowTool;
  marks: Mark[];
  /** the marks inside the frame on show */
  marksHere: Mark[];
  ruler: RulerPoint[];
  setTool(t: FlowTool): void;
  /** a mark can be put on what is on show (one picture, its grid known): the Mark button of touch windows is on only then */
  markable: boolean;
  /** the two passes to blink between (their indexes), null when every pass plays */
  blink: [number, number] | null;
  /** the Task panel, null when the lab was opened without a task */
  taskPanel: ComponentChildren;
  /** a task about one object, the others not asked for: only that object is bright, every other overlay a quiet outline */
  focus: boolean;
  /** Known objects is on and a catalogue is still being asked */
  knownBusy: boolean;
}

export function useLabFlow(a: FlowArgs): LabFlow {
  const [known, setKnown] = useState<KnownState>(KNOWN_OFF);
  const knownRef = useRef(known);
  knownRef.current = known;
  const epochsKey = a.epochs.map((e) => e.toFixed(4)).join(',');
  useEffect(() => {
    if (!a.epochs.length || !Number.isFinite(a.ra) || !Number.isFinite(a.dec)) {
      setKnown(KNOWN_OFF);
      return;
    }
    if (!a.known) {
      // the overlay is off: the question is asked once all the same when the field opens, so that the service has the
      // catalogues by the time the switch is turned on (it goes on asking them in the background)
      setKnown(KNOWN_OFF);
      void fetch(knownUrl(a.ra, a.dec, a.fov, a.epochs)).catch(() => {});
      return;
    }
    return followKnown(knownUrl(a.ra, a.dec, a.fov, a.epochs), setKnown);
  }, [a.known, a.ra, a.dec, a.fov, epochsKey]);

  const [, tick] = useState(0);
  useEffect(() => onMarks(() => tick((x) => x + 1)), []);
  const marks = allMarks();
  const marksRef = useRef(marks);
  marksRef.current = marks;
  const [tool, setTool] = useState<FlowTool>(null);
  const [open, setOpen] = useState<string | null>(null);
  const openRef = useRef(open);
  openRef.current = open;
  const [ruler, setRuler] = useState<RulerPoint[]>([]);
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
  const [counts, setCounts] = useState<{ known: number; noMatch: number } | null>(null);
  const countsRef = useRef('');
  useEffect(() => {
    peaksRef.current.clear();
    matchRef.current = null;
  }, [a.ra, a.dec, a.fov]);
  const draw = (env: FlowDrawEnv) => {
    const { ctx, rects, wall, uvOf: uv, placed } = env;
    const kn = knownRef.current;
    let match: KnownMatch | null = null;
    const pic = env.picture;
    if (kn.state === 'ok' && pic) {
      const pk = `${pic.pass}:${pic.n}:${pic.rgb.length}:${pic.bg}`;
      let peaks = peaksRef.current.get(pk);
      if (!peaks) peaksRef.current.set(pk, (peaks = findPeaks(pic.rgb, pic.n, pic.bg, pic.sigma)));
      const mk = `${pk}:${kn.objects.length}:${kn.deep.length}:${env.year.toFixed(4)}`;
      if (matchRef.current?.key !== mk) matchRef.current = { key: mk, m: matchKnown(kn.objects, peaks, pic.n, pic.step, uv, env.year, kn.deep) };
      match = matchRef.current.m;
    }
    const ck = match ? `${match.shown.size}:${match.noMatch.length}` : '';
    if (ck !== countsRef.current) {
      countsRef.current = ck;
      setCounts(match ? { known: match.shown.size, noMatch: match.noMatch.length } : null);
    }
    // a task about one object rings nothing else; and while a deeper catalogue is still being asked no source is ringed as
    // unmatched (the ring would appear and go when the catalogue arrives): the count on the status line says how many so far
    const noRings = env.focus || kn.deepPending.length > 0;
    drawKnown({ ...env, pointer: ptr.current, match: match && noRings ? { ...match, noMatch: [] } : match }, kn);
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
        ctx.strokeStyle = sel ? '#ffffff' : LEVEL_COLOUR[m.level];
        ctx.stroke();
        if (wall) continue;
        placed.push({ x0: x - r - 2, y0: y - r - 2, x1: x + r + 2, y1: y + r + 2 });
        if (m.label) {
          ctx.font = '600 11px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
          const tw = ctx.measureText(m.label).width;
          const lx = x + r + 6 + tw > box.x + box.w - 4 ? x - r - 6 - tw : x + r + 6;
          ctx.lineWidth = 3;
          ctx.strokeStyle = '#000';
          ctx.strokeText(m.label, lx, y - r - 2);
          ctx.fillStyle = LEVEL_COLOUR[m.level];
          ctx.fillText(m.label, lx, y - r - 2);
          placed.push({ x0: lx, y0: y - r - 13, x1: lx + tw, y1: y - r });
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
          const s = rulerWord(rulerOf(rl[0]!, rl[1]!));
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
    const hit = markAt(x, y);
    if (tool === 'ruler') {
      // a ruler point: the mark under the click, else the click's own place; with the date of the frame on show
      const [ra, dec] = hit ? [hit.ra, hit.dec] : skyOf(x / a.side, y / a.side);
      const p: RulerPoint = { ra, dec, epoch: a.epochNow, pass: a.pass };
      setRuler((r) => (r.length === 1 ? [r[0]!, p] : [p]));
      return true;
    }
    if (hit) {
      setOpen(hit.id === open ? null : hit.id);
      if (hit.id !== open) a.setTab('obj'); // its card
      return true;
    }
    if (tool === 'mark') {
      const [ra, dec] = skyOf(x / a.side, y / a.side);
      const n = marksRef.current.length + 1;
      const m = addMark({
        field_key: fieldKey(a.ra, a.dec, a.fov),
        mjd: a.epochNow != null ? 51544.5 + (a.epochNow - 2000) * 365.25 : null,
        cell_id: a.params['cell'] ?? null,
        ra,
        dec,
        pass: a.pass,
        label: `Mark ${n}`,
        level: 'suspicious',
        note: '',
      });
      setOpen(m.id);
      a.setTab('obj'); // its card
      return true;
    }
    if (open) setOpen(null); // the click goes on to the lab: a source under it is selected
    return false;
  };

  /** what the data already says about a mark's place: the frame's edge, a bright star beside it ('' when nothing) */
  const hintOf = (m: Mark): string => {
    const [u, v] = uvOf(m.ra, m.dec);
    const out: string[] = [];
    if (Math.min(u, v, 1 - u, 1 - v) < 0.03) out.push('at the frame edge');
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

  // the investigation of this field: saved frames and notes, the checks, the submit
  const [inv, setInv] = useState<string | null>(a.params['inv'] ?? null);
  const [invNote, setInvNote] = useState('');
  const [invLine, setInvLine] = useState('');
  /** the investigation's state as the chip says it: "checking 7 of 8", "submitted" */
  const [invState, setInvState] = useState('');
  const [showChecks, setShowChecks] = useState(false);
  const checks = useChecks();
  useEffect(() => {
    setInv(a.params['inv'] ?? null);
    setInvLine('');
    setInvState('');
    setShowChecks(false);
    checks.reset();
  }, [a.ra, a.dec, a.fov, a.params['inv']]);
  const call = async (path: string, body: unknown): Promise<{ status: number; json: Record<string, unknown> | null }> => {
    try {
      const r = await fetch(path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
      return { status: r.status, json: r.ok ? ((await r.json().catch(() => null)) as Record<string, unknown> | null) : null };
    } catch {
      return { status: 0, json: null };
    }
  };
  // a mark's own note is the note of what is added; a source has a note field of its own in its card
  const noteNow = (open ? marks.find((m) => m.id === open)?.note : null) ?? invNote;
  const saveFrame = async () => {
    setInvLine('saving…');
    const frame = { ref: location.hash.slice(0, 600), title: `Lab ${a.ra.toFixed(4)} ${a.dec.toFixed(4)} · ${a.fov}′`, note: noteNow, thumb: a.thumb() };
    let r;
    if (inv) r = await call(`/api/me/investigations/${encodeURIComponent(inv)}/frames`, frame);
    else
      r = await call('/api/me/investigations', {
        title: marksHere[0]?.label || frame.title,
        finding_id: a.params['finding'] ?? null,
        mark_ids: marksHere.map((m) => m.id),
        notes: noteNow ? [{ text: noteNow }] : [],
        frames: [frame],
        checks_job: checks.view.job,
      });
    const id = r.json?.['id'];
    if (r.status >= 200 && r.status < 300) {
      if (typeof id === 'string') setInv(id);
      setInvNote('');
      setInvLine('Saved to the investigation');
    } else setInvLine(r.status === 401 ? 'Sign in to save' : 'Not saved: the service did not answer');
  };
  const followStop = useRef<() => void>(() => {});
  useEffect(() => () => followStop.current(), [a.ra, a.dec, a.fov]);
  // "Check and submit", said once: every check starts, the investigation is made if there is none, the submit is declared; the
  // slow checks go on on the server, which submits by itself when all have answered without a fail. The user may leave.
  const runChecks = async () => {
    setShowChecks(true);
    setInvLine('starting the checks…');
    const real = marksHere.filter((m) => m.level !== 'artefact');
    const at = (open ? real.find((m) => m.id === open) : null) ?? real[0];
    const job = await checks.start(fieldKey(a.ra, a.dec, a.fov), at?.ra ?? a.ra, at?.dec ?? a.dec, real);
    if (!job) return setInvLine('Checks did not answer');
    let id = inv;
    if (!id) {
      const r = await call('/api/me/investigations', {
        title: at?.label || `Lab ${a.ra.toFixed(4)} ${a.dec.toFixed(4)} · ${a.fov}′`,
        finding_id: a.params['finding'] ?? null,
        mark_ids: real.map((m) => m.id),
        notes: noteNow ? [{ text: noteNow }] : [],
        frames: [{ ref: location.hash.slice(0, 600), title: `Lab ${a.ra.toFixed(4)} ${a.dec.toFixed(4)} · ${a.fov}′`, note: '', thumb: a.thumb() }],
        checks_job: job,
      });
      id = typeof r.json?.['id'] === 'string' ? (r.json['id'] as string) : null;
      if (!id) return setInvLine(r.status === 401 ? 'Sign in to submit' : 'Not saved: the service did not answer');
      setInv(id);
    }
    const d = await declareSubmit(id, job);
    const say = (x: DeclaredInv | null, status = 200) => {
      setInvLine(declaredWord(x, status));
      setInvState(chipWord(x));
    };
    say(d.inv, d.status);
    followStop.current();
    if (d.inv?.state === 'checking') followStop.current = followDeclared(id, (x) => say(x));
  };
  // "Measurer": a ruler rate on an object with a catalogue motion goes to the investigation, once per measurement
  const measured = useRef('');
  useEffect(() => {
    if (!inv || ruler.length !== 2 || known.state !== 'ok') return;
    const r = rulerOf(ruler[0]!, ruler[1]!);
    if (r.rate == null || r.dt == null || Math.abs(r.dt) < 0.1) return;
    const end = ruler[1]!;
    let best: { name: string; rate: number; d: number } | null = null;
    for (const o of known.objects) {
      if (o.source === 'jpl' || end.epoch == null) continue;
      const at = placeAt(o, end.epoch);
      const mo = motionOf(o);
      if (!at || !mo || mo.rate < 0.05) continue;
      const d = Math.hypot((((at[0] - end.ra + 540) % 360) - 180) * Math.cos((end.dec * Math.PI) / 180), at[1] - end.dec) * 3600;
      if (d < 8 && (!best || d < best.d)) best = { name: o.name, rate: mo.rate, d };
    }
    const k = best ? `${inv}:${best.name}:${r.rate.toFixed(2)}` : '';
    if (!best || measured.current === k) return;
    measured.current = k;
    void fetch(`/api/me/investigations/${encodeURIComponent(inv)}`, {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ ruler: { rate: Number(r.rate.toFixed(3)), catalogue_rate: Number(best.rate.toFixed(3)), object: best.name } }),
    }).catch(() => {});
  }, [inv, ruler, known.state]);
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
    if (found.current.track >= 0 && found.current.track < a.tracks.length) return found.current.track;
    let best = -1;
    let bd = 20;
    a.tracks.forEach((t, i) => {
      const d = Math.min(nearSpot(t.ra_2025_9, t.dec_2025_9), ...t.ra.map((r, k) => nearSpot(r, t.dec[k]!)));
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
  // set up once per task, when what it needs has arrived
  const setUp = useRef('');
  useEffect(() => {
    if (!task || setUp.current === taskKey) return;
    if (task.kind === 'moves') {
      if (!track || track.passes.length < 2) return;
      setUp.current = taskKey;
      // the two passes with the largest shift between them: the Blink plays those two, the ruler lies from one to the other
      let i0 = 0;
      let i1 = 1;
      let far = -1;
      for (let i = 0; i < track.passes.length; i++)
        for (let j = i + 1; j < track.passes.length; j++) {
          const d = sep(track.ra[i]!, track.dec[i]!, track.ra[j]!, track.dec[j]!);
          if (d > far) {
            far = d;
            i0 = i;
            i1 = j;
          }
        }
      const yr = (mjd: number) => 2000 + (mjd - 51544.5) / 365.25;
      setPair([track.passes[i0]!, track.passes[i1]!]);
      setRuler([
        { ra: track.ra[i0]!, dec: track.dec[i0]!, epoch: yr(track.mjd[i0]!), pass: track.passes[i0]! },
        { ra: track.ra[i1]!, dec: track.dec[i1]!, epoch: yr(track.mjd[i1]!), pass: track.passes[i1]! },
      ]);
      a.pickTrack(trackIx);
    } else if (task.kind === 'red') {
      setUp.current = taskKey;
      a.set({ arch: '1' }); // the old surveys (DSS2, 2MASS, AllWISE) play before the SPHEREx passes
      a.setTab('obj'); // the passport
    } else if (task.kind === 'anomaly') {
      if (!anomaly) return;
      setUp.current = taskKey;
      a.pickAnomaly(anomaly.n); // its passport
    } else setUp.current = taskKey;
  }, [taskKey, track, anomaly]);
  const labelOf = (i: number) => a.passes.find((q) => q.index === i)?.label ?? `pass ${i + 1}`;
  const taskSrc: Source | null = task && (task.kind === 'red' || task.kind === 'anomaly') && a.grid ? at : null;
  // what the lab shows with the task's question, or with one step of a tree of questions (`show` of the step)
  const view = (show: string | null): ComponentChildren => {
    if (task?.kind === 'moves')
      return (
        <MoverView
          blink={blink ? `${labelOf(blink[0])} and ${labelOf(blink[1])}` : track ? 'all passes' : 'waits for the search of the field'}
          ruler={ruler.length === 2 ? rulerWord(rulerOf(ruler[0]!, ruler[1]!)) : '—'}
          search={track ? `${track.speed.toFixed(1)}″/yr toward ${track.pa.toFixed(0)}° · ${track.passes.length} passes` : 'no track at this place yet'}
        />
      );
    if (task?.kind === 'red') return <RedView src={taskSrc} show={show} passes={a.passes.length} />;
    if (task?.kind === 'anomaly') return <AnomalyView src={taskSrc} anomaly={anomaly} show={show} />;
    return null;
  };
  const others = a.params['others'] === '1';
  const taskPanel = useLabTask({
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
  });

  const cur = open ? (marks.find((m) => m.id === open) ?? null) : null;
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
    if (t !== 'ruler') setRuler([]);
  };
  // Mark and Ruler are modes of the picture: a small strip on its edge
  const strip =
    a.single && g ? (
      <div class="float toolstrip" role="group" aria-label="Picture tools" data-testid="tool-strip">
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
    <span class="blinkpick" style={{ visibility: a.blinkable && idx.length > 1 ? 'visible' : 'hidden' }} role="group" aria-label="Blink of two passes">
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
          <option value="">{k ? 'all' : 'all'}</option>
          {a.passes.map((q) => (
            <option key={q.index} value={String(q.index)} disabled={q.index === (k ? pa : pb)}>
              {q.label}
            </option>
          ))}
        </select>
      ))}
    </span>
  );

  // ---- the card of what is selected (Object tab): a mark, or the lab's selected source. Note, "Add to investigation" and
  // "Check and submit" exist only here: with nothing selected there is nothing to note or submit.
  /** what the catalogues have at a place on the date shown: the nearest drawn object within 8″, else a deeper-catalogue entry within 3″ */
  const matchAt = (ra: number, dec: number): string => {
    if (known.state !== 'ok' || a.epochNow == null) return a.known ? 'asking…' : 'Known objects is off';
    let best: { o: (typeof known.objects)[number]; d: number } | null = null;
    for (const o of known.objects) {
      const p = placeAt(o, a.epochNow);
      if (!p) continue;
      const d = sep(p[0], p[1], ra, dec);
      if (d < 8 && (!best || d < best.d)) best = { o, d };
    }
    if (best) {
      const o = best.o;
      const mo = o.source === 'jpl' ? null : motionOf(o);
      return [
        `${SOURCE_WORD[o.source]}${o.name ? ` · ${o.name}` : ''}`,
        o.mag != null ? `mag ${o.mag.toFixed(1)}` : '',
        mo && mo.rate >= 0.05 ? `${mo.rate.toFixed(2)}″/yr` : '',
        `${best.d.toFixed(1)}″ away`,
      ]
        .filter(Boolean)
        .join(' · ');
    }
    for (const o of known.deep) {
      const p = placeAt(o, a.epochNow);
      if (p && sep(p[0], p[1], ra, dec) < 3) return `In a deeper catalogue (${o.id.split(':')[0] === 'gaia' ? 'Gaia to G 21' : o.id.split(':')[0] === 'catwise' ? 'CatWISE2020' : o.id.split(':')[0] === 'allwise' ? 'AllWISE' : '2MASS'})`;
    }
    return known.deepSilent.length ? `No match so far · ${known.deepSilent.join(', ')} ${known.deepPending.length ? 'being asked' : 'did not answer'}` : 'In no catalogue';
  };
  const selPlace = cur ? { ra: cur.ra, dec: cur.dec } : a.sel ? at : null;
  const rulerNow = ruler.length === 2 ? rulerWord(rulerOf(ruler[0]!, ruler[1]!)) : '';
  const actions = (
    <>
      <div class="oc-row" style={{ display: cur ? 'none' : undefined }}>
        <input
          class="inp oc-note"
          value={invNote}
          maxLength={400}
          placeholder="Note for the investigation"
          aria-label="Note for the investigation"
          onInput={(e) => setInvNote((e.target as HTMLInputElement).value)}
          data-testid="inv-note"
        />
      </div>
      <div class="oc-row">
        <button class="btn sm" onClick={() => void saveFrame()} title="Add to investigation: this frame, the marks of the field and the note" data-testid="tool-save">
          Add to investigation
        </button>
        <button
          class="btn sm primary"
          disabled={checks.view.state === 'running'}
          onClick={() => void runChecks()}
          title="Check and submit: every check starts and the submit is declared; the service submits by itself when all have answered and none failed"
          data-testid="tool-check"
        >
          Check and submit
        </button>
      </div>
      <div class="oc-line" data-testid="tools-line">
        {invLine || (showChecks ? checksStatus(checks.view) : '')}
      </div>
      {showChecks && <ChecksList view={checks.view} />}
    </>
  );
  const card =
    cur || a.sel || marksHere.length ? (
      <div class="objcard" data-testid="flow-card">
        {cur ? (
          <>
            <div class="oc-head">
              <i class="oc-glyph" style={{ borderColor: LEVEL_COLOUR[cur.level] }} />
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
                  removeMark(cur.id);
                  setOpen(null);
                }}
                data-testid="mark-remove"
              >
                Remove
              </button>
            </div>
            <div class="seg sm oc-levels" role="group" aria-label="Level">
              {MARK_LEVELS.map((l) => (
                <button key={l} class={cur.level === l ? 'on' : ''} aria-pressed={cur.level === l} onClick={() => updateMark(cur.id, { level: l })} data-testid={`mark-level-${l}`}>
                  <i style={{ background: LEVEL_COLOUR[l] }} />
                  {LEVEL_WORD[l]}
                </button>
              ))}
            </div>
            <input
              class="inp"
              value={cur.note}
              maxLength={400}
              placeholder="Note on the mark"
              aria-label="Note on the mark"
              onInput={(e) => updateMark(cur.id, { note: (e.target as HTMLInputElement).value })}
              data-testid="mark-note"
            />
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
        {marksHere.length > 0 && (
          <div class="oc-marks" data-testid="marks-list">
            <div class="psub">Marks in this field</div>
            {marksHere.map((m) => (
              <button key={m.id} class={`ft-mark ${m.id === open ? 'on' : ''}`} onClick={() => setOpen(m.id === open ? null : m.id)}>
                <i style={{ background: LEVEL_COLOUR[m.level] }} />
                <b>{m.label || 'Mark'}</b>
                <span>{LEVEL_WORD[m.level]}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    ) : null;

  // the investigation of the field is seen on the picture's status line: a chip that opens it in the Cabinet
  const realMarks = marksHere.filter((m) => m.level !== 'artefact').length;
  const chip = inv ? (
    <a class="invchip" href={`#/me?tab=investigations&inv=${encodeURIComponent(inv)}`} title="Open the investigation in the Cabinet" data-testid="inv-chip">
      Investigation · {realMarks} mark{realMarks === 1 ? '' : 's'}
      {invState ? ` · ${invState}` : ''}
    </a>
  ) : null;

  const rulerStatus =
    tool === 'ruler' && ruler.length < 2
      ? ruler.length
        ? 'Ruler: second point'
        : 'Ruler: first point'
      : ruler.length === 2
        ? `Ruler: ${rulerWord(rulerOf(ruler[0]!, ruler[1]!))}`
        : '';
  const status = [knownStatus(known, counts), tool === 'mark' ? 'Mark: click a place' : '', rulerStatus, marksSyncNote()].filter(Boolean).join(' · ');
  // what a closed task earned stays on the line for a few seconds
  const [awardNow, setAwardNow] = useState('');
  useEffect(() => {
    const t = setInterval(() => setAwardNow(awardLine()), 1000); // a new render only when the line changes
    return () => clearInterval(t);
  }, []);

  return {
    draw,
    pointer: (p) => {
      ptr.current = p;
    },
    click,
    status: [status, awardNow].filter(Boolean).join(' · '),
    floats: strip,
    blinkPick,
    card,
    chip,
    markOpen: !!cur,
    tool,
    marks,
    marksHere,
    ruler,
    setTool,
    markable: a.single && !!g,
    blink,
    taskPanel,
    focus: !!task && focusKind(task.kind) && !others,
    knownBusy: known.state === 'loading' || (known.state === 'ok' && (known.pending.length > 0 || known.deepPending.length > 0)),
  };
}
