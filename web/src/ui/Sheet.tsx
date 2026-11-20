// A bottom sheet for touch windows (phones, upright tablets): the second pane of a screen lies over the bottom of the first and is
// pulled up by its grabber. The first pane keeps its size: the sheet covers, it never pushes.
//
// <SheetGrip> goes first inside the element that is the sheet. Whether that element IS a sheet is decided by the style sheet alone:
// responsive.css gives it `--sheet: 1` in the window classes where it is one, and there positions it by two variables this
// component sets on it: `--sheet-h` (the height at rest) and `--sheet-drag` (the shift while a finger moves it). Everywhere else
// the grabber is not shown and nothing is set, so a desktop is untouched.
import { useEffect, useRef, useState } from 'preact/hooks';

export type Detent = 'peek' | 'half' | 'full';
const ORDER: Detent[] = ['peek', 'half', 'full'];

/** The three heights of a sheet whose full height is `full`, whose header ends `peek` px down, in a window `win` px high. */
export function detentHeights(full: number, peek: number, win: number): Record<Detent, number> {
  const p = Math.max(0, Math.min(peek, full));
  const half = Math.max(p, Math.min(full, Math.round(win * 0.5)));
  return { peek: p, half, full: Math.max(half, full) };
}

/** The height of a sheet at rest given the room under the pane behind at its full size (`free`), the header's height (`head`) and
 *  what of it must be seen at rest (`least`): the room when the header fits in it; else the room or `least`, whichever is more. */
export function restHeight(head: number, free: number, least: number): number {
  return head <= free ? Math.floor(free) : Math.max(Math.floor(free), Math.min(head, least), 0);
}

/** Where a sheet let go at `height` (moving at `speed` px/ms, up is positive) comes to rest. */
export function settle(height: number, speed: number, h: Record<Detent, number>): Detent {
  // a flick goes one step in its direction; a slow release goes to the nearest height
  const at = height + speed * 180;
  let best: Detent = 'peek';
  for (const d of ORDER) if (Math.abs(h[d] - at) < Math.abs(h[best] - at)) best = d;
  return best;
}

/**
 * `peek`: the parts of the sheet that are its header (always on screen). `under`: the last thing of the pane behind; when the
 * window is tall, the sheet at rest reaches up to it, so that no empty band is left between the two.
 */
export function SheetGrip({
  peek,
  label,
  under,
  start = 'peek',
  resetKey,
  room,
  need,
}: {
  peek: string;
  label: string;
  under?: string;
  start?: Detent;
  /** the room the pane behind leaves under itself at its full size (px), worked out from its own size, not from where it is now: the
   *  sheet at rest takes that room, and no more than it unless `need` asks (the lab's picture keeps its full size: the sheet at rest
   *  was as high as the whole task, and the picture got what was left, 205 px of 360 on a phone, 20 Nov 2026). Replaces `under`. */
  room?: () => number;
  /** with `room`: the parts that are seen at rest even where the room is smaller (a task's question and its answers); the rest of
   *  the header is seen when the sheet is pulled up */
  need?: string;
  /** what the sheet shows (a field, a task): when it changes the sheet comes back to its height at rest */
  resetKey?: string;
}) {
  const grip = useRef<HTMLButtonElement>(null);
  const [detent, setDetent] = useState<Detent>(start);
  useEffect(() => setDetent(start), [resetKey]);
  const [active, setActive] = useState(false);
  const roomRef = useRef(room);
  roomRef.current = room;
  const needRef = useRef(need);
  needRef.current = need;
  const live = useRef({ detent, head: 0, heights: { peek: 0, half: 0, full: 0 } as Record<Detent, number> });
  live.current.detent = detent;

  useEffect(() => {
    const g = grip.current;
    const sheet = g?.parentElement;
    if (!g || !sheet) return;
    const root = document.documentElement;
    const measure = () => {
      const host = sheet.parentElement;
      const on = getComputedStyle(sheet).getPropertyValue('--sheet').trim() === '1';
      setActive(on);
      if (!on) {
        sheet.style.removeProperty('--sheet-h');
        sheet.style.removeProperty('--sheet-drag');
        host?.style.removeProperty('--sheet-h');
        if (host) delete host.dataset.sheet;
        root.style.removeProperty('--sheet-peek');
        delete sheet.dataset.detent;
        return;
      }
      // the full height is what the style sheet allows (its max-height); the header is everything named by `peek`, and the grabber
      const full = parseFloat(getComputedStyle(sheet).maxHeight) || window.innerHeight * 0.85;
      const top = sheet.getBoundingClientRect().top;
      let end = g.getBoundingClientRect().bottom - top;
      sheet.querySelectorAll<HTMLElement>(peek).forEach((e) => {
        const r = e.getBoundingClientRect();
        if (r.height > 0) end = Math.max(end, r.bottom - top);
      });
      // the header's height is taken while the sheet is at rest: a header that shows more when the sheet is up (a task's example
      // picture) must not change the room kept for it under the picture
      if (live.current.detent === 'peek' || !live.current.head) live.current.head = Math.ceil(end);
      const head = live.current.head;
      const box = sheet.getBoundingClientRect();
      let rest: number;
      const free = roomRef.current?.();
      if (free != null) {
        // the room under the pane at its full size; where the header is higher, at least what `need` names
        let least = head;
        if (needRef.current) {
          least = g.getBoundingClientRect().bottom - top;
          sheet.querySelectorAll<HTMLElement>(needRef.current).forEach((e) => {
            const r = e.getBoundingClientRect();
            if (r.height > 0) least = Math.max(least, r.bottom - top);
          });
          least = Math.ceil(least) + 8;
        }
        rest = restHeight(head, free, least);
      } else {
        const u = under ? document.querySelector<HTMLElement>(under) : null;
        const gap = u && u.getBoundingClientRect().height > 0 ? Math.floor(box.bottom - u.getBoundingClientRect().bottom) : 0;
        rest = Math.max(head, gap);
      }
      const h = detentHeights(full, rest, window.innerHeight);
      live.current.heights = h;
      sheet.style.setProperty('--sheet-h', `${h[live.current.detent]}px`);
      sheet.dataset.detent = live.current.detent;
      // the pane behind knows the sheet's height and place too (the lab keeps its playback bar on top of a sheet pulled up)
      host?.style.setProperty('--sheet-h', `${h[live.current.detent]}px`);
      if (host) host.dataset.sheet = live.current.detent;
      // the pane behind keeps the header's height of its bottom clear, so that nothing of it lies under the sheet at rest
      root.style.setProperty('--sheet-peek', `${roomRef.current ? h.peek : head}px`);
    };
    measure();
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    const watch = () => {
      ro?.disconnect();
      sheet.querySelectorAll<HTMLElement>(peek).forEach((e) => ro?.observe(e));
      const u = under ? document.querySelector<HTMLElement>(under) : null;
      if (u?.parentElement) ro?.observe(u.parentElement);
      if (u && roomRef.current) ro?.observe(u); // the room under the pane follows the thing under it (the lab's playback bar: one row or two)
    };
    watch();
    const mo = new MutationObserver(() => {
      watch();
      measure();
    });
    mo.observe(sheet, { childList: true });
    window.addEventListener('resize', measure);
    return () => {
      ro?.disconnect();
      mo.disconnect();
      window.removeEventListener('resize', measure);
      root.style.removeProperty('--sheet-peek');
    };
  }, [peek, under, detent]);

  // dragging the grabber: the sheet follows the finger and comes to rest on the nearest height when the finger lifts
  useEffect(() => {
    const g = grip.current;
    const sheet = g?.parentElement;
    if (!g || !sheet || !active) return;
    let from: { y: number; h: number; t: number; lastY: number; lastT: number; v: number; moved: boolean } | null = null;
    const down = (e: PointerEvent) => {
      if (e.button !== 0) return;
      try {
        g.setPointerCapture(e.pointerId);
      } catch {
        /* a pointer that cannot be captured still moves the sheet while it stays on the grabber */
      }
      from = { y: e.clientY, h: live.current.heights[live.current.detent], t: e.timeStamp, lastY: e.clientY, lastT: e.timeStamp, v: 0, moved: false };
    };
    const move = (e: PointerEvent) => {
      if (!from) return;
      const dy = from.y - e.clientY;
      if (!from.moved && Math.abs(dy) < 6) return;
      if (!from.moved) {
        from.moved = true;
        sheet.dataset.dragging = '1';
        // while it moves the sheet has its full height and is shifted down, so that the page is not laid out again on every move
        sheet.style.setProperty('--sheet-h', `${live.current.heights.full}px`);
        sheet.parentElement?.style.setProperty('--sheet-h', `${live.current.heights.full}px`);
        if (sheet.parentElement) sheet.parentElement.dataset.sheet = 'drag';
      }
      const dt = Math.max(1, e.timeStamp - from.lastT);
      from.v = 0.7 * from.v + 0.3 * ((from.lastY - e.clientY) / dt);
      from.lastY = e.clientY;
      from.lastT = e.timeStamp;
      const h = Math.max(live.current.heights.peek, Math.min(live.current.heights.full, from.h + dy));
      sheet.style.setProperty('--sheet-drag', `${live.current.heights.full - h}px`);
      sheet.parentElement?.style.setProperty('--sheet-drag', `${live.current.heights.full - h}px`);
    };
    const up = (e: PointerEvent) => {
      const f = from;
      from = null;
      if (!f) return;
      if (!f.moved) return; // a tap: the click below steps to the next height
      const H = live.current.heights;
      const h = Math.max(H.peek, Math.min(H.full, f.h + (f.y - e.clientY)));
      const to = settle(h, e.type === 'pointercancel' ? 0 : f.v, H);
      delete sheet.dataset.dragging;
      sheet.dataset.settling = '1';
      sheet.style.setProperty('--sheet-drag', `${H.full - H[to]}px`);
      sheet.parentElement?.style.setProperty('--sheet-drag', `${H.full - H[to]}px`);
      const done = () => {
        sheet.removeEventListener('transitionend', done);
        clearTimeout(timer);
        delete sheet.dataset.settling;
        sheet.style.setProperty('--sheet-h', `${H[to]}px`);
        sheet.style.removeProperty('--sheet-drag');
        sheet.parentElement?.style.setProperty('--sheet-h', `${H[to]}px`);
        sheet.parentElement?.style.removeProperty('--sheet-drag');
        if (sheet.parentElement) sheet.parentElement.dataset.sheet = to;
        sheet.dataset.detent = to;
        setDetent(to);
      };
      const timer = setTimeout(done, 320);
      sheet.addEventListener('transitionend', done);
      // the click that follows a drag is not a tap on the grabber
      g.dataset.dragged = '1';
      setTimeout(() => delete g.dataset.dragged, 0);
    };
    g.addEventListener('pointerdown', down);
    g.addEventListener('pointermove', move);
    g.addEventListener('pointerup', up);
    g.addEventListener('pointercancel', up);
    return () => {
      g.removeEventListener('pointerdown', down);
      g.removeEventListener('pointermove', move);
      g.removeEventListener('pointerup', up);
      g.removeEventListener('pointercancel', up);
    };
  }, [active]);

  const next = ORDER[(ORDER.indexOf(detent) + 1) % ORDER.length]!;
  return (
    <button
      ref={grip}
      type="button"
      class="sheet-grip"
      data-testid="sheet-grip"
      aria-label={`${label}: ${detent === 'full' ? 'make it small' : 'make it larger'}`}
      aria-expanded={detent !== 'peek'}
      onClick={(e) => {
        if ((e.currentTarget as HTMLElement).dataset.dragged) return;
        setDetent(next);
      }}
    >
      <i />
    </button>
  );
}
