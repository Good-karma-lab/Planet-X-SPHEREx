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

/**
 * A strip of chips or tabs that slides sideways (phones) keeps its chosen item on screen: after a tap or a change of page the item
 * that is on is brought into the strip's window. On a desktop the strips wrap and nothing scrolls, so nothing happens.
 */
export function installStrips(doc: Document = document): () => void {
  const STRIPS = '.cb-tabs, .fkinds, .ipage .chips';
  let timer: ReturnType<typeof setTimeout> | undefined;
  const show = () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      doc.querySelectorAll<HTMLElement>(STRIPS).forEach((strip) => {
        if (strip.scrollWidth <= strip.clientWidth + 1) return;
        const on = strip.querySelector<HTMLElement>('.on, [aria-selected="true"]');
        if (!on) return;
        const s = strip.getBoundingClientRect();
        const r = on.getBoundingClientRect();
        if (r.left < s.left + 12 || r.right > s.right - 12) strip.scrollTo({ left: strip.scrollLeft + r.left - s.left - (s.width - r.width) / 2, behavior: 'smooth' });
      });
    }, 80);
  };
  doc.addEventListener('click', show, true);
  window.addEventListener('hashchange', show);
  show();
  return () => {
    clearTimeout(timer);
    doc.removeEventListener('click', show, true);
    window.removeEventListener('hashchange', show);
  };
}

/**
 * The magnifier for marks. In the lab, with the Mark tool (or the ruler) on, a finger on the picture hides the very point it
 * chooses. While the finger is down a round glass above it shows the picture under the finger three times larger, with a cross at
 * the exact point; sliding moves the point; lifting puts it there; sliding off the picture drops it. The lab itself is unchanged:
 * it puts a mark on a click, and a click is what it gets, at the place where the finger was lifted.
 */
export const LOUPE = { size: 132, zoom: 3, lift: 96, slop: 10 } as const;

/** Where the glass goes: above the finger; beside it when the finger is too near the top of the window. */
export function loupePlace(x: number, y: number, winW: number): { left: number; top: number } {
  const { size, lift } = LOUPE;
  let left = x - size / 2;
  let top = y - lift - size / 2;
  if (top < 4) {
    // no room above: to the side that has room, level with the finger
    top = Math.max(4, y - size / 2);
    left = x > winW / 2 ? x - size - 40 : x + 40;
  }
  return { left: Math.max(4, Math.min(winW - size - 4, left)), top };
}

export function installLoupe(doc: Document = document): () => void {
  let glass: HTMLCanvasElement | null = null;
  let on: { id: number; sq: HTMLElement; x0: number; y0: number; moved: boolean } | null = null;
  let swallowUntil = 0;
  const inside = (sq: HTMLElement, x: number, y: number) => {
    const r = sq.getBoundingClientRect();
    return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
  };
  const draw = (sq: HTMLElement, x: number, y: number) => {
    const { size, zoom } = LOUPE;
    if (!glass) {
      glass = doc.createElement('canvas');
      glass.className = 'loupe';
      glass.setAttribute('aria-hidden', 'true');
      doc.body.appendChild(glass);
    }
    const dpr = Math.min(3, window.devicePixelRatio || 1);
    if (glass.width !== size * dpr) glass.width = glass.height = size * dpr;
    const p = loupePlace(x, y, window.innerWidth);
    glass.style.left = `${p.left}px`;
    glass.style.top = `${p.top}px`;
    glass.style.display = 'block';
    const g = glass.getContext('2d');
    if (!g) return;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.fillStyle = '#02070a';
    g.fillRect(0, 0, size, size);
    g.imageSmoothingEnabled = false;
    const half = size / zoom / 2;
    // the picture, then what the lab draws over it (rings, marks, labels)
    sq.querySelectorAll<HTMLCanvasElement>('canvas.vid, canvas.ov').forEach((c) => {
      if (!c.width || !c.height || c.clientWidth === 0) return;
      const cr = c.getBoundingClientRect();
      const k = c.width / cr.width;
      try {
        g.drawImage(c, (x - half - cr.left) * k, (y - half - cr.top) * k, half * 2 * k, half * 2 * k, 0, 0, size, size);
      } catch {
        /* a canvas that cannot be read is left out of the glass */
      }
    });
    // the cross: the exact point, with a gap in the middle so that the point itself is seen
    const out = !inside(sq, x, y);
    g.strokeStyle = out ? 'rgba(255, 111, 111, 0.95)' : 'rgba(159, 243, 234, 0.95)';
    g.lineWidth = 1.5;
    g.beginPath();
    const c = size / 2;
    for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]] as const) {
      g.moveTo(c + dx * 7, c + dy * 7);
      g.lineTo(c + dx * 22, c + dy * 22);
    }
    g.stroke();
  };
  const hide = () => {
    if (glass) glass.style.display = 'none';
  };
  const down = (e: PointerEvent) => {
    if (e.pointerType !== 'touch' || on) return;
    const t = e.target as Element | null;
    const sq = t?.closest?.('.vsq[data-tool]') as HTMLElement | null;
    if (!sq || t?.closest('.float')) return;
    on = { id: e.pointerId, sq, x0: e.clientX, y0: e.clientY, moved: false };
    draw(sq, e.clientX, e.clientY);
  };
  const move = (e: PointerEvent) => {
    if (!on || e.pointerId !== on.id) return;
    if (Math.hypot(e.clientX - on.x0, e.clientY - on.y0) > LOUPE.slop) on.moved = true;
    draw(on.sq, e.clientX, e.clientY);
  };
  const up = (e: PointerEvent) => {
    if (!on || e.pointerId !== on.id) return;
    const o = on;
    on = null;
    hide();
    if (e.type === 'pointercancel' || !o.moved) return; // a plain tap: the browser's own click puts the mark
    // the finger slid: browsers send no click after a slide, so the lab gets one here, at the place of the lift
    swallowUntil = e.timeStamp + 500;
    if (!inside(o.sq, e.clientX, e.clientY)) return; // lifted off the picture: nothing is put
    const target = o.sq.querySelector('canvas.vid') ?? o.sq;
    target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, clientX: e.clientX, clientY: e.clientY, view: window }));
  };
  // should a browser send its own click after the slide all the same, it is not a second mark
  const click = (e: MouseEvent) => {
    if (e.isTrusted && e.timeStamp < swallowUntil && (e.target as Element | null)?.closest?.('.vsq[data-tool]')) {
      e.stopPropagation();
      e.preventDefault();
    }
  };
  const opts = { capture: true } as const;
  doc.addEventListener('pointerdown', down, opts);
  doc.addEventListener('pointermove', move, opts);
  doc.addEventListener('pointerup', up, opts);
  doc.addEventListener('pointercancel', up, opts);
  doc.addEventListener('click', click, opts);
  return () => {
    doc.removeEventListener('pointerdown', down, opts);
    doc.removeEventListener('pointermove', move, opts);
    doc.removeEventListener('pointerup', up, opts);
    doc.removeEventListener('pointercancel', up, opts);
    doc.removeEventListener('click', click, opts);
    glass?.remove();
  };
}
