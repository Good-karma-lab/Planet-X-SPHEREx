// The small picture of a row in the cabinet: the looping time-lapse clip of a finding (GET /api/findings/{id}/clip.webp of the
// findings stream), or of a place. Nothing is asked for until the row is on screen; a clip still being rendered is asked again.

import { useEffect, useRef, useState } from 'preact/hooks';
import { cutout, tiles } from '../../lib/hips';

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

/** How a place's clip is shown brighter: the place clips come out of the service with the sky near black (median 0, the 99th
 *  percentile of the pixels near 65 of 255), so a row's thumbnail read as a black square. The clip's own pixels are kept; only the
 *  display is stretched, as the lab stretches its frames: a square-root curve, scaled so the clip's 99th percentile sits at 80 %
 *  brightness, less a black point of 10 % so the faint compression blocks of the clip stay dark. A clip that is already bright (99th percentile over 55 %) is shown as it is. Measured once per address. */
const stretchOf = new Map<string, number>();
function measure(img: HTMLImageElement): number {
  try {
    const n = 48;
    const c = document.createElement('canvas');
    c.width = n;
    c.height = n;
    const g = c.getContext('2d', { willReadFrequently: true });
    if (!g) return 0;
    g.drawImage(img, 0, 0, n, n);
    const d = g.getImageData(0, 0, n, n).data;
    const lum: number[] = [];
    for (let i = 0; i < d.length; i += 4) lum.push((0.2126 * d[i]! + 0.7152 * d[i + 1]! + 0.0722 * d[i + 2]!) / 255);
    lum.sort((a, b) => a - b);
    const p99 = lum[Math.floor(lum.length * 0.99)] ?? 1;
    if (p99 >= 0.55) return 0;
    return Math.min(3, Math.max(1, 0.9 / Math.sqrt(Math.max(p99, 1e-3))));
  } catch {
    return 0; // a picture the page may not read: shown as it is
  }
}
let filterSeq = 0;

/** A place with no SPHEREx time-lapse yet: the 2MASS colour picture of the same 3′, from the survey's HiPS tiles (lib/hips.ts),
 *  drawn again as tiles arrive. Never an empty frame. */
function SurveyStamp({ ra, dec, size }: { ra: number; dec: number; size: number }) {
  const cv = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const px = Math.min(256, Math.round(size * 2));
    let done = false;
    const draw = () => {
      const c = cv.current;
      if (!c || done) return;
      const r = cutout('2mass', 9, ra, dec, 3 / 60, px);
      c.getContext('2d')?.putImageData(r.data, 0, 0);
      done = r.complete;
    };
    draw();
    const off = tiles.onTile(draw);
    return off;
  }, [ra, dec, size]);
  const px = Math.min(256, Math.round(size * 2));
  return (
    <>
      <canvas ref={cv} width={px} height={px} style={{ width: `${size}px`, height: `${size}px` }} aria-hidden="true" />
      <svg class="cb-ring" viewBox="0 0 100 100" aria-hidden="true">
        <circle cx="50" cy="50" r="9" />
      </svg>
      <span class="cb-src">2MASS</span>
    </>
  );
}

export function Pic({ findingId, ra, dec, size = 96, label, thumb }: { findingId?: string | null; ra?: number | null; dec?: number | null; size?: number; label: string; thumb?: string | null }) {
  const url = clipUrl({ findingId, ra, dec }, size >= 200 ? 320 : 170);
  const box = useRef<HTMLSpanElement>(null);
  const [near, setNear] = useState(false);
  const [big, setBig] = useState(false);
  const opened = useRef(0);
  const bigUrl = clipUrl({ findingId, ra, dec }, 320);
  const [state, setState] = useState<'idle' | 'ready' | 'none'>(() => (url ? (seen.get(url) ?? 'idle') : 'none'));
  // a place's clip (not a finding's own, which the service stretches itself) is shown with the display stretch above
  const place = !!url && url.startsWith(`${PLACE_CLIP ?? '-'}?`);
  const [amp, setAmp] = useState(() => (url && place ? (stretchOf.get(url) ?? 0) : 0));
  const fid = useRef(`cb-str-${++filterSeq}`).current;
  const onPicLoad = (e: Event) => {
    if (!place || !url) return;
    const k = stretchOf.get(url) ?? measure(e.currentTarget as HTMLImageElement);
    stretchOf.set(url, k);
    setAmp(k);
  };
  const look = amp ? { filter: `url(#${fid})` } : undefined;
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
      title={state === 'ready' ? 'Time-lapse of this place: one frame per SPHEREx pass' : state === 'none' && (thumb || ra != null) ? 'No SPHEREx time-lapse here yet' : undefined}
    >
      {state === 'none' && thumb && <img src={thumb} alt={label} width={size} height={size} />}
      {state === 'none' && !thumb && ra != null && dec != null && <SurveyStamp ra={ra} dec={dec} size={size} />}
      {state === 'ready' && url && <img src={url} alt={`Time-lapse of ${label}`} width={size} height={size} decoding="async" style={look} onLoad={onPicLoad} data-stretch={amp ? amp.toFixed(2) : undefined} />}
      {big && state === 'ready' && bigUrl && <img class="cb-pic-big" src={bigUrl} alt={`Larger time-lapse of ${label}`} width={320} height={320} style={look} data-testid="row-pic-big" />}
      {amp > 0 && (
        <svg class="cb-str" width="0" height="0" aria-hidden="true" focusable="false">
          <filter id={fid} color-interpolation-filters="sRGB">
            <feComponentTransfer>
              <feFuncR type="gamma" amplitude={amp} exponent={0.5} offset={-0.1} />
              <feFuncG type="gamma" amplitude={amp} exponent={0.5} offset={-0.1} />
              <feFuncB type="gamma" amplitude={amp} exponent={0.5} offset={-0.1} />
            </feComponentTransfer>
          </filter>
        </svg>
      )}
    </span>
  );
}
