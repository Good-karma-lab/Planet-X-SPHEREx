// The small picture of a row in the cabinet: the looping time-lapse clip of a finding (GET /api/findings/{id}/clip.webp of the
// findings stream), or of a place. Nothing is asked for until the row is on screen; a clip still being rendered is asked again.

import { useEffect, useRef, useState } from 'preact/hooks';

/** The clip of a place (ra, dec) from the findings stream: 3′ around the place, one frame per pass, made only from a finished field
 *  (404 when none holds the place: the frame then stays empty). */
export const PLACE_CLIP: string | null = '/api/clips/place.webp';

/** The address of a row's clip: the finding's own clip (the object ringed), else the place with a ring on the point.
 *  `px` is the clip's own size (170 is sharp in a 96 px row on a 2x screen; 320 is the larger view). */
export function clipUrl(p: { findingId?: string | null; ra?: number | null; dec?: number | null }, px: 170 | 320 = 170): string | null {
  if (p.findingId && !p.findingId.startsWith('user:')) return `/api/findings/${encodeURIComponent(p.findingId)}/clip.webp`;
  if (PLACE_CLIP && p.ra != null && p.dec != null) return `${PLACE_CLIP}?ra=${p.ra.toFixed(4)}&dec=${p.dec.toFixed(4)}&size=${px}&ring=1`;
  return null;
}

const seen = new Map<string, 'ready' | 'none'>();

export function Pic({ findingId, ra, dec, size = 96, label }: { findingId?: string | null; ra?: number | null; dec?: number | null; size?: number; label: string }) {
  const url = clipUrl({ findingId, ra, dec });
  const box = useRef<HTMLSpanElement>(null);
  const [near, setNear] = useState(false);
  const [big, setBig] = useState(false);
  const opened = useRef(0);
  const bigUrl = clipUrl({ findingId, ra, dec }, 320);
  const [state, setState] = useState<'idle' | 'ready' | 'none'>(() => (url ? (seen.get(url) ?? 'idle') : 'none'));
  useEffect(() => {
    const el = box.current;
    if (!el || typeof IntersectionObserver === 'undefined') return setNear(true);
    const io = new IntersectionObserver((es) => es.some((e) => e.isIntersecting) && setNear(true), { rootMargin: '200px 0px' });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  useEffect(() => {
    if (!near || !url || state !== 'idle') return;
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let tries = 0;
    const ask = async () => {
      let code = 0;
      try {
        const r = await fetch(url);
        code = r.status;
        if (code === 200) await r.arrayBuffer(); // now in the browser's cache: the picture below reads it from there
      } catch {
        code = 0;
      }
      if (stop) return;
      if (code === 200) {
        seen.set(url, 'ready');
        setState('ready');
      } else if (code === 202 && ++tries < 20) {
        timer = setTimeout(ask, 6000);
      } else {
        if (code === 404) seen.set(url, 'none');
        setState('none');
      }
    };
    void ask();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [near, url, state]);
  // the larger view: on hover with a mouse, on a tap or a click otherwise (the row's own link is not followed by that click)
  const toggle = (e: Event) => {
    if (state !== 'ready') return;
    e.preventDefault();
    e.stopPropagation();
    // a tap first sends the mouse-enter that opens the view: the click right after it must not close it again
    if (big && Date.now() - opened.current < 500) return;
    opened.current = Date.now();
    setBig((b) => !b);
  };
  return (
    <span
      class="cb-pic"
      ref={box}
      style={{ width: `${size}px`, height: `${size}px` }}
      data-state={state}
      data-testid="row-pic"
      onMouseEnter={() => {
        if (state !== 'ready') return;
        opened.current = Date.now();
        setBig(true);
      }}
      onMouseLeave={() => setBig(false)}
      onClick={toggle}
      title={state === 'ready' ? 'Time-lapse of this place: one frame per SPHEREx pass' : undefined}
    >
      {state === 'ready' && url && <img src={url} alt={`Time-lapse of ${label}`} width={size} height={size} decoding="async" />}
      {big && state === 'ready' && bigUrl && <img class="cb-pic-big" src={bigUrl} alt={`Larger time-lapse of ${label}`} width={320} height={320} data-testid="row-pic-big" />}
    </span>
  );
}
