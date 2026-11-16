// The small picture of a row in the cabinet: the looping time-lapse clip of a finding (GET /api/findings/{id}/clip.webp of the
// findings stream), or of a place. Nothing is asked for until the row is on screen; a clip still being rendered is asked again.

import { useEffect, useRef, useState } from 'preact/hooks';

/** The clip of a place (ra, dec), once the findings stream serves one; null until then (the frame stays empty). */
export const PLACE_CLIP: string | null = null;

export function clipUrl(p: { findingId?: string | null; ra?: number | null; dec?: number | null }): string | null {
  if (p.findingId && !p.findingId.startsWith('user:')) return `/api/findings/${encodeURIComponent(p.findingId)}/clip.webp`;
  if (PLACE_CLIP && p.ra != null && p.dec != null) return `${PLACE_CLIP}?ra=${p.ra.toFixed(4)}&dec=${p.dec.toFixed(4)}`;
  return null;
}

const seen = new Map<string, 'ready' | 'none'>();

export function Pic({ findingId, ra, dec, size = 56, label }: { findingId?: string | null; ra?: number | null; dec?: number | null; size?: number; label: string }) {
  const url = clipUrl({ findingId, ra, dec });
  const box = useRef<HTMLSpanElement>(null);
  const [near, setNear] = useState(false);
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
  return (
    <span class="cb-pic" ref={box} style={{ width: `${size}px`, height: `${size}px` }} data-state={state}>
      {state === 'ready' && url && <img src={url} alt={`Time-lapse of ${label}`} width={size} height={size} decoding="async" />}
    </span>
  );
}
