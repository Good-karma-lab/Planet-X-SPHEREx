// Touch behaviour added from outside the screens, so that their own code stays as it is.
//
// 1. Two-finger zoom of the globe. The globe zooms about the cursor on a wheel event and turns with one pointer. Two fingers on its
//    canvas are turned here into wheel events at the point between them; while they are down the globe's one-pointer turn is held.
// 2. The magnifier for marks in the lab (see installLoupe).
//
// Both are installed once, on the document, and act only on touch pointers: a mouse is never touched.

const GLOBE = 'canvas[data-testid="sky"]';

/** The wheel delta that zooms by the factor `k` in the globe's law (zoom × exp(−delta · 0.0022), one event limited to ×0.5…×2). */
export const wheelFor = (k: number): number => -Math.log(Math.max(0.5, Math.min(2, k))) / 0.0022;

export function installPinch(doc: Document = document): () => void {
  const pts = new Map<number, { x: number; y: number }>();
  let canvas: HTMLElement | null = null;
  let pinch: { d: number } | null = null;
  // after a pinch the fingers that remain do nothing until every finger is up: the turn would jump to the place of the first finger
  let hold = false;
  const two = () => [...pts.values()].slice(0, 2) as [{ x: number; y: number }, { x: number; y: number }];
  const dist = () => {
    const [a, b] = two();
    return Math.hypot(a.x - b.x, a.y - b.y);
  };
  const down = (e: PointerEvent) => {
    if (e.pointerType !== 'touch') return;
    const c = (e.target as Element | null)?.closest?.(GLOBE) as HTMLElement | null;
    if (!c) return;
    canvas = c;
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pts.size === 2) {
      pinch = { d: dist() };
      hold = true;
      // the second finger is not a turn of the globe
      e.stopImmediatePropagation();
    } else if (pts.size > 2) e.stopImmediatePropagation();
  };
  const move = (e: PointerEvent) => {
    if (!pts.has(e.pointerId)) return;
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (!hold) return;
    e.stopImmediatePropagation();
    if (!pinch || pts.size < 2 || !canvas) return;
    const d = dist();
    if (d < 8 || pinch.d < 8) return;
    const k = d / pinch.d;
    if (Math.abs(k - 1) < 0.01) return;
    pinch.d = d;
    const [a, b] = two();
    canvas.dispatchEvent(new WheelEvent('wheel', { deltaY: wheelFor(k), deltaMode: 0, clientX: (a.x + b.x) / 2, clientY: (a.y + b.y) / 2, bubbles: true, cancelable: true }));
  };
  const up = (e: PointerEvent) => {
    if (!pts.has(e.pointerId)) return;
    pts.delete(e.pointerId);
    if (hold) {
      // the globe never sees the end of a pinch as a tap; its own drag starts afresh with the next finger
      e.stopImmediatePropagation();
      if (pts.size < 2) pinch = null;
      if (pts.size === 0) {
        hold = false;
        canvas = null;
      }
    }
  };
  const opts = { capture: true } as const;
  doc.addEventListener('pointerdown', down, opts);
  doc.addEventListener('pointermove', move, opts);
  doc.addEventListener('pointerup', up, opts);
  doc.addEventListener('pointercancel', up, opts);
  return () => {
    doc.removeEventListener('pointerdown', down, opts);
    doc.removeEventListener('pointermove', move, opts);
    doc.removeEventListener('pointerup', up, opts);
    doc.removeEventListener('pointercancel', up, opts);
  };
}
