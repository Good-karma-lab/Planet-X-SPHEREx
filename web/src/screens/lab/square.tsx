// A field that is a square of the sky grid (20′), or one quarter of it (10′): the lab says which, outlines the whole square, and
// counts a look only on purpose: a signed-in person, at least 60 s with the picture on show, every pass shown, and a button
// ("I have looked through this quarter") or a square task's closing answer. Opening a field never counts; a guest never counts.
import type { ComponentChildren } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import { useUser } from '../../lib/account';
import { clearDo, pendingDo, useSignIn } from './signin';

export type Quarter = 'NE' | 'NW' | 'SE' | 'SW';
/** the picture's reading order: north up, east left */
export const QUARTERS: Quarter[] = ['NE', 'NW', 'SE', 'SW'];
export const LOOK_SECONDS = 60;

export interface GridCell {
  id: string;
  ra: number;
  dec: number;
  corners: [number, number][];
  /** the service's quarters (named `id`, or `q` in an earlier shape), with how many people looked and whether I did */
  quarters?: { id?: string; q?: string; ra?: number; dec?: number; viewers?: number; mine?: boolean }[];
}

const wrap = (d: number) => ((d + 540) % 360) - 180;

/** A quarter's centre: the midpoint of the square's centre and its corner in that direction (north = +Dec, east = +RA). */
export function quarterCentre(c: GridCell, q: Quarter): [number, number] | null {
  const north = q[0] === 'N';
  const east = q[1] === 'E';
  const corner = c.corners.find(([r, d]) => d - c.dec > 0 === north && wrap(r - c.ra) > 0 === east);
  if (!corner) return null;
  return [(((c.ra + wrap(corner[0] - c.ra) / 2) % 360) + 360) % 360, (c.dec + corner[1]) / 2];
}

/** Which square (fov 20) or quarter (fov 10) a field is: its centre within 3″ of the square's or the quarter's centre. */
export function matchSquare(cells: GridCell[], ra: number, dec: number, fov: number): { cell: GridCell; quarter: Quarter | null } | null {
  const sep = (r: number, d: number) => Math.hypot(wrap(r - ra) * Math.cos((dec * Math.PI) / 180), d - dec) * 3600;
  for (const c of cells) {
    if (fov === 20 && sep(c.ra, c.dec) < 3) return { cell: c, quarter: null };
    if (fov === 10)
      for (const q of QUARTERS) {
        const p = quarterCentre(c, q);
        if (p && sep(p[0], p[1]) < 3) return { cell: c, quarter: q };
      }
  }
  return null;
}

export interface SquareArgs {
  params: Record<string, string>;
  ra: number;
  dec: number;
  fov: number;
  /** the pass on show (Months), null in Days or while nothing is shown */
  pass: number | null;
  passesTotal: number;
  /** the field is finished and on show: only then does the look time run */
  ready: boolean;
  open: (params: Record<string, string>) => void;
}

export interface SquareLook {
  cell: GridCell | null;
  quarter: Quarter | null;
  /** the whole square's corners, to outline on the picture */
  outline: [number, number][] | null;
  /** the status line words: "square 099-0543 · NE quarter · 1 of 4 looked through" */
  line: string;
  /** a look can be counted now (signed in, 60 s, every pass) */
  canCount: boolean;
  /** why it cannot ('' when it can, or while only time is needed and more than 20 s are left): "Sign in to count your look",
   *  "passes 2 of 4", "0:18" */
  why: string;
  /** how far the look is, 0..1 (the time and the passes shown): the thin bar of the task card */
  frac: number;
  /** count the look (POST …/viewed): the caller has checked canCount */
  confirm: (result?: 'nothing' | 'marks') => Promise<boolean>;
  /** the button of the free lab ("I have looked through this quarter"), null in a task or off a square */
  button: ComponentChildren;
}

export function useSquareLook(a: SquareArgs): SquareLook {
  const { user } = useUser();
  const [found, setFound] = useState<{ cell: GridCell; quarter: Quarter | null } | null>(null);
  const [tick, setTick] = useState(0);
  const [line2, setLine2] = useState('');
  const fieldKey = `${a.ra.toFixed(5)},${a.dec.toFixed(5)},${a.fov}`;
  const cellParam = a.params['cell'] ?? '';
  const qParam = (QUARTERS as string[]).includes(a.params['quarter'] ?? '') ? (a.params['quarter'] as Quarter) : null;
  // which square this field is: the task's, else the grid cell whose square (20′) or quarter (10′) it is
  useEffect(() => {
    setFound(null);
    setLine2('');
    if (a.fov !== 20 && a.fov !== 10) return;
    let stop = false;
    void (async () => {
      try {
        if (cellParam) {
          const r = await fetch(`/api/grid/cell/${encodeURIComponent(cellParam)}`);
          if (!r.ok) return;
          const c = (await r.json()) as GridCell;
          if (!stop) setFound({ cell: c, quarter: a.fov === 10 ? (qParam ?? matchSquare([c], a.ra, a.dec, 10)?.quarter ?? null) : null });
          return;
        }
        const r = await fetch(`/api/grid/cells?ra=${a.ra.toFixed(5)}&dec=${a.dec.toFixed(5)}&radius=0.3`);
        if (!r.ok) return;
        const cells = ((await r.json()) as { cells?: GridCell[] }).cells ?? [];
        const m = matchSquare(cells, a.ra, a.dec, a.fov);
        if (!m || stop) return;
        // the cell's own view, with how many of its quarters this person has looked through
        const one = await fetch(`/api/grid/cell/${encodeURIComponent(m.cell.id)}`);
        const c = one.ok ? ((await one.json()) as GridCell) : m.cell;
        if (!stop) setFound({ cell: c, quarter: m.quarter });
      } catch {
        /* not a square: nothing to say */
      }
    })();
    return () => {
      stop = true;
    };
  }, [fieldKey, cellParam, qParam, tick]);

  // the look: seconds with the finished picture on show (page visible), and the passes shown
  const look = useRef({ seconds: 0, passes: new Set<number>() });
  const [, beat] = useState(0);
  const lookKey = `spherex.look.${fieldKey}`;
  useEffect(() => {
    look.current = { seconds: 0, passes: new Set<number>() };
    try {
      const kept = JSON.parse(localStorage.getItem(lookKey) ?? 'null') as { seconds: number; passes: number[]; at: number } | null;
      if (kept && Date.now() - kept.at < 2 * 3600e3) look.current = { seconds: kept.seconds, passes: new Set(kept.passes) };
    } catch {
      /* no store: the look starts at 0 */
    }
  }, [fieldKey]);
  if (a.ready && a.pass != null) look.current.passes.add(a.pass);
  const readyRef = useRef(a.ready);
  readyRef.current = a.ready;
  useEffect(() => {
    // wall-clock time between ticks (a busy page fires the timer late), at most 2 s a tick so a sleeping tab never counts
    let last = Date.now();
    const t = setInterval(() => {
      const now = Date.now();
      const dt = Math.min(2, (now - last) / 1000);
      last = now;
      if (!readyRef.current || (typeof document !== 'undefined' && document.visibilityState !== 'visible')) return;
      const before = Math.floor(look.current.seconds);
      try {
        localStorage.setItem(lookKey, JSON.stringify({ seconds: look.current.seconds, passes: [...look.current.passes], at: Date.now() }));
      } catch {
        /* a full store: the look is kept for this page only */
      }
      look.current.seconds += dt;
      if (Math.floor(look.current.seconds) !== before && look.current.seconds <= LOOK_SECONDS + 2) beat((x) => x + 1);
    }, 1000);
    return () => clearInterval(t);
  }, []);

  const cell = found?.cell ?? null;
  const quarter = found?.quarter ?? null;
  const left = Math.max(0, Math.ceil(LOOK_SECONDS - look.current.seconds));
  const allPasses = a.passesTotal > 0 && look.current.passes.size >= a.passesTotal;
  const canCount = !!cell && !!user && left === 0 && allPasses;
  const why = !cell
    ? ''
    : !user
      ? 'Sign in to count your look'
      : left > 0 || !allPasses
        ? // quiet: the bar says how far; words only for the passes not yet shown and for the last 20 seconds
          [allPasses ? '' : `passes ${look.current.passes.size} of ${a.passesTotal}`, left > 0 && left <= 20 ? `0:${String(left).padStart(2, '0')}` : ''].filter(Boolean).join(' · ')
        : '';
  const frac = !cell ? 0 : Math.min(1, look.current.seconds / LOOK_SECONDS) * 0.7 + (a.passesTotal ? Math.min(1, look.current.passes.size / a.passesTotal) : 1) * 0.3;
  const mine = cell?.quarters?.filter((q) => q.mine).length;
  const line = cell
    ? [`square ${cell.id}`, quarter ? `${quarter} quarter` : '', quarter && mine != null ? `${mine} of 4 looked through` : '']
        .filter(Boolean)
        .join(' · ')
    : '';

  // a look is counted per quarter (the service takes no look at a whole square): a 20′ square looked through counts its four
  // quarters. Only from the person's own click, and `confirm: true` says so
  const confirm = async (result?: 'nothing' | 'marks'): Promise<boolean> => {
    if (!cell || !canCount) return false;
    let ok = true;
    for (const q of quarter ? [quarter] : QUARTERS) {
      try {
        const r = await fetch(`/api/grid/cell/${encodeURIComponent(cell.id)}/viewed`, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ quarter: q, confirm: true, ...(result ? { result } : {}) }),
        });
        if (r.status === 401) {
          setLine2('Sign in to count your look');
          return false;
        }
        ok = ok && r.ok;
      } catch {
        ok = false;
      }
    }
    setTick((x) => x + 1); // the square again, with my quarters
    return ok;
  };

  // the free lab's button; after a quarter, the next one of the square not yet looked through opens
  const inTask = !!a.params['task'];
  const si = useSignIn();
  const done = async () => {
    if (!user) return si.ask('look', 'look'); // a guest: the popup; the link comes back with do=look
    setLine2('saving…');
    const ok = await confirm();
    if (!ok) return setLine2((l) => l || 'Not counted: the service did not answer');
    if (!quarter || !cell) {
      setLine2('Counted');
      return setTick((x) => x + 1);
    }
    const seen = new Set((cell.quarters ?? []).filter((q) => q.mine).map((q) => q.id ?? q.q));
    seen.add(quarter);
    const next = QUARTERS.slice(QUARTERS.indexOf(quarter) + 1)
      .concat(QUARTERS)
      .find((q) => !seen.has(q));
    const at = next ? quarterCentre(cell, next) : null;
    if (!next || !at) {
      setLine2('Square looked through');
      return setTick((x) => x + 1);
    }
    a.open({ ...a.params, ra: at[0].toFixed(5), dec: at[1].toFixed(5), fov: '10', cell: cell.id, quarter: next });
  };
  useEffect(() => {
    if (pendingDo(a.params) === 'look' && !inTask && canCount) {
      clearDo();
      void done();
    }
  }, [canCount, !!user]);
  const button =
    cell && !inTask ? (
      <div class="sqlook" data-testid="square-look">
        <button
          class="btn sm primary"
          disabled={!!user && !canCount}
          title={canCount ? 'Count your look at this part of the sky' : why}
          onClick={() => void done()}
          data-testid="square-look-button"
        >
          {quarter ? 'I have looked through this quarter' : 'I have looked through this square'}
        </button>
        {!!user && !canCount && !line2 && (
          <i class="lookbar" aria-hidden="true">
            <b style={{ width: `${Math.round(frac * 100)}%` }} />
          </i>
        )}
        <span class="sq-line" data-testid="square-look-line">
          {line2 || why}
        </span>
        {si.popup}
      </div>
    ) : null;

  return { cell, quarter, outline: cell?.corners ?? null, line, canCount, why, frac, confirm, button };
}
