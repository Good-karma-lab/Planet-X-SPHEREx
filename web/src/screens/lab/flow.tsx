// The product-flow pieces of the lab (product spec §5 Lab): Known objects, marks, the ruler. One hook, `useLabFlow`, which lab.tsx
// calls; it returns what the lab draws on its overlay, what it puts over the picture and in the Controls panel, and its status line.
import type { ComponentChildren } from 'preact';
import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { addMark, allMarks, fieldKey, MARK_LEVELS, marksSyncNote, onMarks, removeMark, updateMark, type Mark, type MarkLevel } from '../../lib/marks';
import { drawKnown, followKnown, KNOWN_OFF, knownStatus, knownUrl, matchKnown, type KnownDrawEnv, type KnownMatch, type KnownState } from './known';
import { findPeaks, type Peak } from './detect';
import { rulerOf, rulerWord, type RulerPoint } from './ruler';
import { awardLine, ChecksList, checksStatus, declaredWord, declareSubmit, followDeclared, useChecks, useLabTask } from './task';
import { motionOf } from './known';
import { placeAt } from './known';
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
}

export type FlowTool = 'mark' | 'ruler' | null;

export interface FlowDrawEnv extends Omit<KnownDrawEnv, 'pointer' | 'match'> {
  /** a wall of tiles: rings and marks without names */
  wall: boolean;
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
  /** over the picture: the editor of the open mark */
  floats: ComponentChildren;
  /** the Tools section of the Controls panel */
  tools: ComponentChildren;
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
}

export function useLabFlow(a: FlowArgs): LabFlow {
  const [known, setKnown] = useState<KnownState>(KNOWN_OFF);
  const knownRef = useRef(known);
  knownRef.current = known;
  const epochsKey = a.epochs.map((e) => e.toFixed(4)).join(',');
  useEffect(() => {
    if (!a.known || !a.epochs.length || !Number.isFinite(a.ra) || !Number.isFinite(a.dec)) {
      setKnown(KNOWN_OFF);
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
      const mk = `${pk}:${kn.objects.length}:${env.year.toFixed(4)}`;
      if (matchRef.current?.key !== mk) matchRef.current = { key: mk, m: matchKnown(kn.objects, peaks, pic.n, pic.step, uv, env.year) };
      match = matchRef.current.m;
    }
    const ck = match ? `${match.shown.size}:${match.noMatch.length}` : '';
    if (ck !== countsRef.current) {
      countsRef.current = ck;
      setCounts(match ? { known: match.shown.size, noMatch: match.noMatch.length } : null);
    }
    drawKnown({ ...env, pointer: ptr.current, match }, kn);
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
      return true;
    }
    if (open) {
      setOpen(null);
      return true;
    }
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
  const [pair, setPair] = useState<[number, number]>([0, 1]);
  const [blinkOn, setBlinkOn] = useState(false);
  const idx = a.passes.map((q) => q.index);
  const pa = idx.includes(pair[0]) ? pair[0] : (idx[0] ?? 0);
  const pb = idx.includes(pair[1]) && pair[1] !== pa ? pair[1] : (idx.find((i) => i !== pa) ?? pa);
  const blink: [number, number] | null = blinkOn && a.pass !== null && idx.length > 1 && pa !== pb ? [pa, pb] : null;

  // the investigation of this field: saved frames and notes, the checks, the submit
  const [inv, setInv] = useState<string | null>(a.params['inv'] ?? null);
  const [invNote, setInvNote] = useState('');
  const [invLine, setInvLine] = useState('');
  const [showChecks, setShowChecks] = useState(false);
  const checks = useChecks();
  useEffect(() => {
    setInv(a.params['inv'] ?? null);
    setInvLine('');
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
  const saveFrame = async () => {
    setInvLine('saving…');
    const frame = { ref: location.hash.slice(0, 600), title: `Lab ${a.ra.toFixed(4)} ${a.dec.toFixed(4)} · ${a.fov}′`, note: invNote, thumb: a.thumb() };
    let r;
    if (inv) r = await call(`/api/me/investigations/${encodeURIComponent(inv)}/frames`, frame);
    else
      r = await call('/api/me/investigations', {
        title: marksHere[0]?.label || frame.title,
        finding_id: a.params['finding'] ?? null,
        mark_ids: marksHere.map((m) => m.id),
        notes: invNote ? [{ text: invNote }] : [],
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
        notes: invNote ? [{ text: invNote }] : [],
        frames: [{ ref: location.hash.slice(0, 600), title: `Lab ${a.ra.toFixed(4)} ${a.dec.toFixed(4)} · ${a.fov}′`, note: '', thumb: a.thumb() }],
        checks_job: job,
      });
      id = typeof r.json?.['id'] === 'string' ? (r.json['id'] as string) : null;
      if (!id) return setInvLine(r.status === 401 ? 'Sign in to submit' : 'Not saved: the service did not answer');
      setInv(id);
    }
    const d = await declareSubmit(id, job);
    setInvLine(declaredWord(d.inv, d.status));
    followStop.current();
    if (d.inv?.state === 'checking') followStop.current = followDeclared(id, (x) => setInvLine(declaredWord(x)));
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
  const taskPanel = useLabTask({
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
  let floats: ComponentChildren = null;
  if (cur && g && a.single && inFrame(cur)) {
    const [u, v] = uvOf(cur.ra, cur.dec);
    const W = 272;
    const x = u * a.side;
    const y = v * a.side;
    // right of the mark, else left; below it, else above: always inside the picture
    const left = x + 14 + W > a.side - 6 ? Math.max(6, x - 14 - W) : x + 14;
    const top = Math.max(50, Math.min(a.side - 190, y - 20));
    floats = (
      <div class="float markedit" style={{ left: `${left}px`, top: `${top}px`, width: `${W}px` }} data-testid="mark-editor">
        <div class="me-row">
          <input
            class="inp"
            value={cur.label}
            maxLength={60}
            aria-label="Label of the mark"
            spellcheck={false}
            onInput={(e) => updateMark(cur.id, { label: (e.target as HTMLInputElement).value })}
            onKeyDown={(e) => e.key === 'Enter' && setOpen(null)}
            data-testid="mark-label"
          />
          <button class="mini" title="Close" aria-label="Close" onClick={() => setOpen(null)}>
            ×
          </button>
        </div>
        <div class="seg sm me-levels" role="group" aria-label="Level">
          {MARK_LEVELS.map((l) => (
            <button
              key={l}
              class={cur.level === l ? 'on' : ''}
              aria-pressed={cur.level === l}
              onClick={() => updateMark(cur.id, { level: l })}
              data-testid={`mark-level-${l}`}
            >
              <i style={{ background: LEVEL_COLOUR[l] }} />
              {LEVEL_WORD[l]}
            </button>
          ))}
        </div>
        <input
          class="inp"
          value={cur.note}
          maxLength={400}
          placeholder="Note"
          aria-label="Note"
          onInput={(e) => updateMark(cur.id, { note: (e.target as HTMLInputElement).value })}
          data-testid="mark-note"
        />
        <div class="me-hint" data-testid="mark-hint">
          {hintOf(cur)}
        </div>
        <div class="me-row">
          <span class="me-meta">{cur.pass != null ? `pass ${cur.pass + 1}` : 'no pass'}</span>
          <button
            class="mini"
            onClick={() => {
              removeMark(cur.id);
              setOpen(null);
            }}
            data-testid="mark-remove"
          >
            Remove
          </button>
        </div>
      </div>
    );
  }

  const pick = (t: FlowTool) => {
    setTool((c) => (c === t ? null : t));
    if (t !== 'ruler') setRuler([]);
  };
  const tools = (
    <section class="psec flowtools" data-testid="flow-tools">
      <div class="psh">Tools</div>
      <div class="ft-row">
        <button
          class={`mini ${tool === 'mark' ? 'on' : ''}`}
          aria-pressed={tool === 'mark'}
          disabled={!a.single || !g}
          onClick={() => pick('mark')}
          title="Mark: click the picture to put a point; click a mark to change or remove it"
          data-testid="tool-mark"
        >
          Mark
        </button>
        <button
          class={`mini ${tool === 'ruler' ? 'on' : ''}`}
          aria-pressed={tool === 'ruler'}
          disabled={!a.single || !g}
          onClick={() => pick('ruler')}
          title="Ruler: click two places (or two marks, or the same source on two passes): the shift, the rate and the direction"
          data-testid="tool-ruler"
        >
          Ruler
        </button>
      </div>
      <div class="ft-row ft-blink" role="group" aria-label="Blink of two passes">
        <button
          class={`mini ${blink ? 'on' : ''}`}
          aria-pressed={!!blink}
          disabled={a.pass === null || idx.length < 2}
          onClick={() => setBlinkOn((v) => !v)}
          title="Blink: only the two chosen passes play, one after the other"
          data-testid="tool-blink"
        >
          Blink
        </button>
        {([0, 1] as const).map((k) => (
          <select
            key={k}
            class="inp"
            aria-label={k ? 'Second pass' : 'First pass'}
            disabled={a.pass === null || idx.length < 2}
            value={String(k ? pb : pa)}
            onChange={(e) => {
              const v = Number((e.target as HTMLSelectElement).value);
              setPair(k ? [pa, v] : [v, pb]);
            }}
            data-testid={`blink-${k ? 'b' : 'a'}`}
          >
            {a.passes.map((q) => (
              <option key={q.index} value={String(q.index)} disabled={q.index === (k ? pa : pb)}>
                {q.label}
              </option>
            ))}
          </select>
        ))}
      </div>
      <div class="ft-row">
        <input
          class="inp ft-note"
          value={invNote}
          maxLength={400}
          placeholder="Note"
          aria-label="Note for the investigation"
          onInput={(e) => setInvNote((e.target as HTMLInputElement).value)}
          data-testid="inv-note"
        />
        <button class="mini" onClick={() => void saveFrame()} title="Save to investigation: this frame and the note" data-testid="tool-save">
          Save to investigation
        </button>
      </div>
      <div class="ft-row">
        <button
          class="mini"
          disabled={checks.view.state === 'running'}
          onClick={() => void runChecks()}
          title="Check and submit: every check runs first; the submit opens when all have answered and none failed"
          data-testid="tool-check"
        >
          Check and submit
        </button>
      </div>
      <div class="ft-line" data-testid="tools-line">
        {invLine || (showChecks ? checksStatus(checks.view) : '')}
      </div>
      {showChecks && <ChecksList view={checks.view} />}
      {marksHere.length > 0 && (
        <div class="ft-marks" data-testid="marks-list">
          {marksHere.map((m) => (
            <button key={m.id} class={`ft-mark ${m.id === open ? 'on' : ''}`} onClick={() => setOpen(m.id === open ? null : m.id)}>
              <i style={{ background: LEVEL_COLOUR[m.level] }} />
              <b>{m.label || 'Mark'}</b>
              <span>{LEVEL_WORD[m.level]}</span>
            </button>
          ))}
        </div>
      )}
    </section>
  );

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

  // the flow's own status line, over the picture above the date (nothing else on the page moves when it appears)
  const all = (
    <>
      {floats}
    </>
  );

  return {
    draw,
    pointer: (p) => {
      ptr.current = p;
    },
    click,
    status: [status, awardNow].filter(Boolean).join(' · '),
    floats: all,
    tools,
    tool,
    marks,
    marksHere,
    ruler,
    setTool,
    blink,
    taskPanel,
  };
}
