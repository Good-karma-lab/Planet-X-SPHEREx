// The product-flow pieces of the lab (product spec §5 Lab): Known objects, marks, the ruler. One hook, `useLabFlow`, which lab.tsx
// calls; it returns what the lab draws on its overlay, what it puts over the picture and in the Controls panel, and its status line.
import type { ComponentChildren } from 'preact';
import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { addMark, allMarks, fieldKey, MARK_LEVELS, marksSyncNote, onMarks, removeMark, updateMark, type Mark, type MarkLevel } from '../../lib/marks';
import { drawKnown, followKnown, KNOWN_OFF, knownStatus, knownUrl, type KnownDrawEnv, type KnownState } from './known';
import { rulerOf, rulerWord, type RulerPoint } from './ruler';
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
  /** the field key and cell of the task the lab was opened with, when there is one */
  taskField?: { field_key: string; cell_id: string | null } | null;
}

export type FlowTool = 'mark' | 'ruler' | null;

export interface FlowDrawEnv extends Omit<KnownDrawEnv, 'pointer'> {
  /** a wall of tiles: rings and marks without names */
  wall: boolean;
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

  const draw = (env: FlowDrawEnv) => {
    const { ctx, rects, wall, uvOf: uv, placed } = env;
    drawKnown({ ...env, pointer: ptr.current }, knownRef.current);
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
        field_key: a.taskField?.field_key ?? fieldKey(a.ra, a.dec, a.fov),
        cell_id: a.taskField?.cell_id ?? null,
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

  const cur = open ? (marks.find((m) => m.id === open) ?? null) : null;
  let floats: ComponentChildren = null;
  if (cur && g && a.single && inFrame(cur)) {
    const [u, v] = uvOf(cur.ra, cur.dec);
    const W = 232;
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
  const status = [knownStatus(known), tool === 'mark' ? 'Mark: click a place' : '', rulerStatus, marksSyncNote()].filter(Boolean).join(' · ');

  // the flow's own status line, over the picture above the date (nothing else on the page moves when it appears)
  const all = (
    <>
      {status && (
        <div class="float fflow" data-testid="flow-status">
          {status}
        </div>
      )}
      {floats}
    </>
  );

  return {
    draw,
    pointer: (p) => {
      ptr.current = p;
    },
    click,
    status,
    floats: all,
    tools,
    tool,
    marks,
    marksHere,
    ruler,
    setTool,
  };
}
