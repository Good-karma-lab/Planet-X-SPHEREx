// Known objects of a lab field: every Gaia DR3 star, SIMBAD object and JPL body in the frame, ringed at its place on the date of the
// pass on show (product spec §5 Lab, contract `GET /api/known` in §8). What moves and has no ring is unknown.

export type KnownSource = 'gaia' | 'simbad' | 'jpl';
export const KNOWN_SOURCES: KnownSource[] = ['gaia', 'simbad', 'jpl'];
export const SOURCE_WORD: Record<KnownSource, string> = { gaia: 'Gaia', simbad: 'SIMBAD', jpl: 'JPL' };

export interface KnownObject {
  id: string;
  source: KnownSource;
  name: string;
  type?: string | null;
  mag?: number | null;
  /** total motion on the sky, mas/yr (a JPL body's rate is given in the same unit) */
  pm_total?: number | null;
  /** a Solar System body's rate, ″/h */
  rate_arcsec_h?: number | null;
  /** its place at each epoch asked for (decimal years) */
  positions: { epoch: number; ra: number; dec: number }[];
  /** the name and the speed are written at the ring; otherwise a thin ring with the name on hover */
  label: boolean;
}

export interface KnownReply {
  objects: KnownObject[];
  answered: Record<KnownSource, boolean>;
}

export interface KnownState {
  /** off: not asked · loading · ok: the reply is here · failed: the service did not answer at all */
  state: 'off' | 'loading' | 'ok' | 'failed';
  objects: KnownObject[];
  answered: Record<KnownSource, boolean>;
}

export const KNOWN_OFF: KnownState = { state: 'off', objects: [], answered: { gaia: false, simbad: false, jpl: false } };

/** the service takes at most this many dates in one question */
export const MAX_EPOCHS = 40;
const MJD_J2000 = 51544.5;
export const yearOfMjd = (mjd: number) => 2000 + (mjd - MJD_J2000) / 365.25;

/** The question for a frame; the dates are MJDs (a JPL body moves arcseconds in minutes: a year with five decimals is too coarse). */
export function knownUrl(ra: number, dec: number, sizeArcmin: number, mjds: number[]): string {
  const q = new URLSearchParams({
    ra: ra.toFixed(5),
    dec: dec.toFixed(5),
    size_arcmin: String(sizeArcmin),
    epochs: mjds.slice(0, MAX_EPOCHS).map((e) => e.toFixed(4)).join(','),
  });
  return `/api/known?${q.toString()}`;
}

/** The reply of the service made safe: wrong rows dropped, a catalogue with no word about it counted as not answered. */
export function parseKnown(raw: unknown): KnownReply {
  const r = (raw ?? {}) as { objects?: unknown; answered?: Partial<Record<KnownSource, unknown>> };
  const objects: KnownObject[] = [];
  for (const o of Array.isArray(r.objects) ? (r.objects as Partial<KnownObject>[]) : []) {
    if (!o || !KNOWN_SOURCES.includes(o.source as KnownSource) || !Array.isArray(o.positions)) continue;
    // a date comes back as it was asked: an MJD (above 3000) or a decimal year; here it is always a year
    const positions = o.positions
      .filter((p) => p && Number.isFinite(p.epoch) && Number.isFinite(p.ra) && Number.isFinite(p.dec))
      .map((p) => ({ epoch: p.epoch > 3000 ? yearOfMjd(p.epoch) : p.epoch, ra: p.ra, dec: p.dec }));
    if (!positions.length) continue;
    positions.sort((a, b) => a.epoch - b.epoch);
    objects.push({
      id: String(o.id ?? `${o.source}:${objects.length}`),
      source: o.source as KnownSource,
      name: String(o.name ?? ''),
      type: o.type ?? null,
      mag: Number.isFinite(o.mag as number) ? (o.mag as number) : null,
      pm_total: Number.isFinite(o.pm_total as number) ? (o.pm_total as number) : null,
      rate_arcsec_h: Number.isFinite(o.rate_arcsec_h as number) ? (o.rate_arcsec_h as number) : null,
      positions,
      label: o.label === true,
    });
  }
  return {
    objects,
    answered: { gaia: r.answered?.gaia === true, simbad: r.answered?.simbad === true, jpl: r.answered?.jpl === true },
  };
}

/** Asked again while a catalogue has not answered (the service keeps asking it): at most this many times, this far apart. */
const RETRIES = 8;
const RETRY_MS = 5000;

/** Ask the service for the known objects of a field; `on` gets every state (loading, then the reply, and again when a late catalogue answers). */
export function followKnown(url: string, on: (s: KnownState) => void): () => void {
  let stop = false;
  const ctl = new AbortController();
  on({ ...KNOWN_OFF, state: 'loading' });
  void (async () => {
    for (let n = 0; n <= RETRIES && !stop; n++) {
      let reply: KnownReply | null = null;
      try {
        const r = await fetch(url, { signal: ctl.signal });
        if (r.ok) reply = parseKnown(await r.json());
      } catch {
        /* said below */
      }
      if (stop) return;
      if (reply) on({ state: 'ok', ...reply });
      else if (n === RETRIES || n === 0) on({ ...KNOWN_OFF, state: 'failed' });
      if (reply && KNOWN_SOURCES.every((s) => reply.answered[s])) return;
      await new Promise((res) => setTimeout(res, RETRY_MS));
    }
  })();
  return () => {
    stop = true;
    ctl.abort();
  };
}

/** The status line of the overlay: which catalogue did not answer ('' when all did, or the overlay is off). */
export function knownStatus(k: KnownState): string {
  if (k.state === 'off') return '';
  if (k.state === 'loading') return 'asking Gaia, SIMBAD and JPL…';
  const silent = KNOWN_SOURCES.filter((s) => k.state === 'failed' || !k.answered[s]).map((s) => SOURCE_WORD[s]);
  if (!silent.length) return '';
  return `${silent.length === 3 ? 'Gaia, SIMBAD and JPL' : silent.join(' and ')} did not answer`;
}

/** a JPL place is used for a frame within this of its date (half a day, in years) */
const JPL_MATCH_YEARS = 0.5 / 365.25;

/** Where the object is at an epoch: between two of its given places, linear in time; outside them, the line through the nearest two. */
export function placeAt(o: KnownObject, epoch: number): [number, number] | null {
  const p = o.positions;
  if (o.source === 'jpl') {
    // a Solar System body is in the frame on some dates only, and its path between passes is not a line: it is ringed on the dates
    // the service gave a place for (within half a day), nowhere else
    let best: (typeof p)[number] | null = null;
    for (const q of p) if (!best || Math.abs(q.epoch - epoch) < Math.abs(best.epoch - epoch)) best = q;
    return best && Math.abs(best.epoch - epoch) < JPL_MATCH_YEARS ? [best.ra, best.dec] : null;
  }
  if (p.length === 1) return [p[0]!.ra, p[0]!.dec];
  let i = 0;
  while (i < p.length - 2 && p[i + 1]!.epoch < epoch) i++;
  const a = p[i]!;
  const b = p[i + 1]!;
  const span = b.epoch - a.epoch;
  if (!(span > 0)) return [a.ra, a.dec];
  const t = (epoch - a.epoch) / span;
  const dra = ((b.ra - a.ra + 540) % 360) - 180;
  return [(((a.ra + dra * t) % 360) + 360) % 360, a.dec + (b.dec - a.dec) * t];
}

/** Its motion on the sky in arcseconds per year, east and north, from its first and last given place (null with one place). */
export function motionOf(o: KnownObject): { east: number; north: number; rate: number; pa: number } | null {
  const p = o.positions;
  if (p.length < 2) return null;
  const a = p[0]!;
  const b = p[p.length - 1]!;
  const dt = b.epoch - a.epoch;
  if (!(dt > 0)) return null;
  const east = ((((b.ra - a.ra + 540) % 360) - 180) * Math.cos((((a.dec + b.dec) / 2) * Math.PI) / 180) * 3600) / dt;
  const north = ((b.dec - a.dec) * 3600) / dt;
  return { east, north, rate: Math.hypot(east, north), pa: ((Math.atan2(east, north) * 180) / Math.PI + 360) % 360 };
}

/** The speed as written at a ring: stars in ″/yr, Solar System bodies in ″/h. */
export function speedWord(o: KnownObject): string {
  if (o.source === 'jpl') {
    const h = o.rate_arcsec_h ?? (o.pm_total != null ? o.pm_total / 1000 / 8766 : null);
    return h != null ? `${h.toFixed(h >= 10 ? 0 : 1)}″/h` : '';
  }
  const rate = o.pm_total != null ? o.pm_total / 1000 : (motionOf(o)?.rate ?? null);
  return rate != null && rate >= 0.05 ? `${rate.toFixed(rate >= 10 ? 1 : 2)}″/yr` : '';
}

const COLOUR: Record<KnownSource, { ring: string; text: string }> = {
  gaia: { ring: 'rgba(140,190,255,0.85)', text: '#cfe2ff' },
  simbad: { ring: 'rgba(150,230,170,0.85)', text: '#d3f5dc' },
  jpl: { ring: 'rgba(245,225,150,0.9)', text: '#fbf0c4' },
};
/** a star faster than this gets its one-year arrow (″/yr) */
export const ARROW_MIN_RATE = 0.3;
/** at most this many rings are drawn on one picture (the brightest first) */
const MAX_RINGS = 4000;

export interface KnownDrawEnv {
  ctx: CanvasRenderingContext2D;
  /** the picture, or the tiles of a wall (CSS px) */
  rects: { x: number; y: number; w: number; h: number }[];
  wall: boolean;
  uvOf: (ra: number, dec: number) => [number, number];
  /** the date of what is shown, decimal year */
  year: number;
  /** the field's width in arcseconds */
  fovArc: number;
  /** boxes no label may cover (rings and names already drawn); labels drawn here are added */
  placed: { x0: number; y0: number; x1: number; y1: number }[];
  /** the pointer over the picture (CSS px), null when outside */
  pointer: { x: number; y: number } | null;
  /** true where nothing is drawn (under the zoom inset, the title row and the status line) */
  hidden: (x: number, y: number) => boolean;
}

function text(ctx: CanvasRenderingContext2D, s: string, x: number, y: number, fill: string): void {
  ctx.lineWidth = 3;
  ctx.strokeStyle = '#000';
  ctx.strokeText(s, x, y);
  ctx.fillStyle = fill;
  ctx.fillText(s, x, y);
}

/** Draw the rings of the known objects at their places on the date shown. Returns the object under the pointer, if any. */
export function drawKnown(env: KnownDrawEnv, k: KnownState): KnownObject | null {
  if (k.state !== 'ok' || !k.objects.length) return null;
  const { ctx, rects, wall, uvOf, year, fovArc, placed, pointer, hidden } = env;
  const objs = k.objects.length > MAX_RINGS ? k.objects.slice().sort((a, b) => (a.mag ?? 99) - (b.mag ?? 99)).slice(0, MAX_RINGS) : k.objects;
  let hover: { o: KnownObject; x: number; y: number; r: number; box: (typeof rects)[number]; d: number } | null = null;
  const named: { o: KnownObject; x: number; y: number; r: number; box: (typeof rects)[number] }[] = [];
  ctx.save();
  for (const box of rects) {
    const pxPerArcsec = box.w / fovArc;
    ctx.save();
    ctx.beginPath();
    ctx.rect(box.x, box.y, box.w, box.h);
    ctx.clip();
    for (const src of KNOWN_SOURCES) {
      // one path per catalogue and weight: thousands of rings cost two strokes, not thousands
      const thin = new Path2D();
      const bold = new Path2D();
      const arrows = new Path2D();
      for (const o of objs) {
        if (o.source !== src) continue;
        const at = placeAt(o, year);
        if (!at) continue;
        const [u, v] = uvOf(at[0], at[1]);
        if (u < -0.02 || v < -0.02 || u > 1.02 || v > 1.02) continue;
        const x = box.x + u * box.w;
        const y = box.y + v * box.h;
        if (!wall && hidden(x, y)) continue;
        const r = wall ? Math.max(3, Math.min(6, box.w / 40)) : o.label ? 9 : 6;
        const path = o.label ? bold : thin;
        path.moveTo(x + r, y);
        path.arc(x, y, r, 0, 2 * Math.PI);
        if (wall) continue;
        if (o.label) named.push({ o, x, y, r, box });
        if (pointer) {
          const d = Math.hypot(pointer.x - x, pointer.y - y);
          if (d <= r + 4 && (!hover || d < hover.d)) hover = { o, x, y, r, box, d };
        }
        // a fast star: an arrow of one year of its motion, from the ring outward (never shorter than 6 px, so the direction is seen)
        const m = src === 'jpl' ? null : motionOf(o);
        if (m && m.rate >= ARROW_MIN_RATE) {
          const len = Math.max(6, m.rate * pxPerArcsec);
          const ux = -m.east / m.rate;
          const uy = -m.north / m.rate;
          const x0 = x + ux * r;
          const y0 = y + uy * r;
          const x1 = x0 + ux * len;
          const y1 = y0 + uy * len;
          arrows.moveTo(x0, y0);
          arrows.lineTo(x1, y1);
          const h = Math.min(5, Math.max(3, len * 0.5));
          arrows.moveTo(x1 - ux * h - uy * h * 0.6, y1 - uy * h + ux * h * 0.6);
          arrows.lineTo(x1, y1);
          arrows.lineTo(x1 - ux * h + uy * h * 0.6, y1 - uy * h - ux * h * 0.6);
        }
      }
      const c = COLOUR[src];
      ctx.strokeStyle = 'rgba(0,0,0,0.45)';
      ctx.lineWidth = 2;
      ctx.stroke(bold);
      ctx.stroke(arrows);
      ctx.strokeStyle = c.ring;
      ctx.globalAlpha = 0.6;
      ctx.lineWidth = 0.75;
      ctx.stroke(thin);
      ctx.globalAlpha = 1;
      ctx.lineWidth = 1.1;
      ctx.stroke(bold);
      ctx.stroke(arrows);
    }
    ctx.restore();
  }
  if (!wall) {
    ctx.font = '500 10.5px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
    const put = (q: { o: KnownObject; x: number; y: number; r: number; box: (typeof rects)[number] }, force: boolean) => {
      const sp = speedWord(q.o);
      const s = [q.o.name || SOURCE_WORD[q.o.source], sp].filter(Boolean).join(' · ');
      const tw = ctx.measureText(s).width;
      let lx = q.x + q.r + 5;
      if (lx + tw > q.box.x + q.box.w - 4) lx = q.x - q.r - 5 - tw;
      lx = Math.max(q.box.x + 4, lx);
      const ly = Math.max(q.box.y + 2, Math.min(q.box.y + q.box.h - 14, q.y + q.r + 2));
      const b = { x0: lx - 1, y0: ly, x1: lx + tw + 1, y1: ly + 12 };
      if (!force && placed.some((p) => b.x0 < p.x1 && b.x1 > p.x0 && b.y0 < p.y1 && b.y1 > p.y0)) return;
      placed.push(b);
      text(ctx, s, lx, ly + 10, COLOUR[q.o.source].text);
    };
    // names where the service says so: the fastest and the brightest first, never over a name already there
    named.sort((a, b) => (motionOf(b.o)?.rate ?? 0) - (motionOf(a.o)?.rate ?? 0) || (a.o.mag ?? 99) - (b.o.mag ?? 99));
    for (const q of named) if (q.o !== hover?.o) put(q, false);
    if (hover) {
      const h = hover;
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.arc(h.x, h.y, h.r + 2, 0, 2 * Math.PI);
      ctx.stroke();
      put(h, true);
    }
  }
  ctx.restore();
  return hover?.o ?? null;
}
