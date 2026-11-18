import { Buckets, NOMATCH_SIGMA, type Peak } from './detect';

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
  /** the SIMBAD name of a Gaia star that has one */
  simbad?: string | null;
  /** proper motion, mas/yr (the objects with a name carry it) */
  pmra?: number | null;
  pmdec?: number | null;
  /** Gaia's parallax, mas */
  parallax?: number | null;
  /** Gaia's BP−RP colour and RUWE */
  bp_rp?: number | null;
  ruwe?: number | null;
  /** a deeper-catalogue match: the catalogues and their ids merged into it */
  catalogues?: string[];
  ids?: string[];
  /** its place at each epoch asked for (decimal years) */
  positions: { epoch: number; ra: number; dec: number }[];
  /** the name and the speed are written at the ring; otherwise a thin ring with the name on hover */
  label: boolean;
}

export interface KnownReply {
  objects: KnownObject[];
  answered: Record<KnownSource, boolean>;
  /** catalogues the service is still asking (it answers them later: the lab asks again) */
  pending: KnownSource[];
  /** the deeper catalogues (Gaia to G 21, CatWISE2020, AllWISE, 2MASS), for matching only: never drawn */
  deep: KnownObject[];
  /** the deeper catalogues that did not answer, by name (empty when all did): "no match" means unknown only then */
  deepSilent: string[];
  /** of those, the ones the service is still asking (the lab asks again and they arrive) */
  deepPending: string[];
}

export interface KnownState {
  /** off: not asked · loading · ok: the reply is here · failed: the service did not answer at all */
  state: 'off' | 'loading' | 'ok' | 'failed';
  objects: KnownObject[];
  answered: Record<KnownSource, boolean>;
  pending: KnownSource[];
  deep: KnownObject[];
  deepSilent: string[];
  deepPending: string[];
  /** the lab stopped asking: what is still pending did not answer in time */
  gaveUp?: boolean;
}

/** the deeper catalogues of the `match` list, as the status line names them */
const DEEP: Record<string, string> = { gaia: 'Gaia G 21', catwise: 'CatWISE', allwise: 'AllWISE', twomass: '2MASS' };
const DEEP_NAMES = Object.values(DEEP);

export const KNOWN_OFF: KnownState = {
  state: 'off',
  objects: [],
  answered: { gaia: false, simbad: false, jpl: false },
  pending: [],
  deep: [],
  deepSilent: [...DEEP_NAMES],
  deepPending: [],
};

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
    epochs: mjds
      .slice(0, MAX_EPOCHS)
      .map((e) => e.toFixed(4))
      .join(','),
    compact: '1', // objects without a name carry only what a ring needs
  });
  return `/api/known?${q.toString()}`;
}

/** The reply of the service made safe: wrong rows dropped, a catalogue with no word about it counted as not answered. */
export function parseKnown(raw: unknown): KnownReply {
  const r = (raw ?? {}) as {
    objects?: unknown;
    answered?: Partial<Record<KnownSource, unknown>>;
    status?: Partial<Record<KnownSource, { state?: string }>> & { match?: Record<string, { state?: string }> };
    match?: unknown;
  };
  const deep: KnownObject[] = [];
  for (const m of Array.isArray(r.match) ? (r.match as Partial<KnownObject>[]) : []) {
    if (!m || !Array.isArray(m.positions)) continue;
    const positions = m.positions
      .filter((q) => q && Number.isFinite(q.epoch) && Number.isFinite(q.ra) && Number.isFinite(q.dec))
      .map((q) => ({ epoch: q.epoch > 3000 ? yearOfMjd(q.epoch) : q.epoch, ra: q.ra, dec: q.dec }))
      .sort((a, b) => a.epoch - b.epoch);
    if (positions.length)
      deep.push({
        id: String(m.id ?? `deep:${deep.length}`),
        source: 'gaia',
        name: '',
        mag: Number.isFinite(m.mag as number) ? (m.mag as number) : null,
        catalogues: Array.isArray(m.catalogues) ? m.catalogues.map(String) : [],
        ids: Array.isArray(m.ids) ? m.ids.map(String) : [],
        positions,
        label: false,
      });
  }
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
      simbad: typeof o.simbad === 'string' && o.simbad ? o.simbad : null,
      pmra: Number.isFinite(o.pmra as number) ? (o.pmra as number) : null,
      pmdec: Number.isFinite(o.pmdec as number) ? (o.pmdec as number) : null,
      parallax: Number.isFinite(o.parallax as number) ? (o.parallax as number) : null,
      bp_rp: Number.isFinite(o.bp_rp as number) ? (o.bp_rp as number) : null,
      ruwe: Number.isFinite(o.ruwe as number) ? (o.ruwe as number) : null,
      positions,
      label: o.label === true,
    });
  }
  return {
    objects,
    answered: { gaia: r.answered?.gaia === true, simbad: r.answered?.simbad === true, jpl: r.answered?.jpl === true },
    pending: KNOWN_SOURCES.filter((c) => r.answered?.[c] !== true && r.status?.[c]?.state === 'pending'),
    deep,
    deepSilent: Object.keys(DEEP)
      .filter((c) => r.status?.match?.[c]?.state !== 'ok')
      .map((c) => DEEP[c]!),
    deepPending: Object.keys(DEEP)
      .filter((c) => r.status?.match?.[c]?.state === 'pending')
      .map((c) => DEEP[c]!),
  };
}

/** The service answers within about a second with what it has; while a catalogue is still being asked the lab asks again
 *  this far apart, and what has arrived is drawn at once. After this long a catalogue still being asked counts as one that did not
 *  answer: the lookup ends, and the lab's retry (15 s, 30 s, 60 s, then every 2 min, with its countdown and "try now") asks again. */
const ASK_AGAIN_MS = 1500;
export const GIVE_UP_MS = 60000;

/** A reply whose catalogues still being asked are now counted as not answering (the lookup has waited long enough). */
export function giveUp(reply: KnownReply): KnownReply {
  return {
    ...reply,
    pending: [],
    deepPending: [],
    deepSilent: Array.from(new Set([...reply.deepSilent, ...reply.deepPending])),
  };
}

/** Ask the service for the known objects of a field; `on` gets every state: loading, then each partial reply as catalogues arrive. */
export function followKnown(url: string, on: (s: KnownState) => void): () => void {
  let stop = false;
  const ctl = new AbortController();
  on({ ...KNOWN_OFF, state: 'loading' });
  void (async () => {
    const t0 = Date.now();
    let had = false;
    for (let fails = 0; !stop;) {
      let reply: KnownReply | null = null;
      try {
        const r = await fetch(url, { signal: ctl.signal });
        if (r.ok) reply = parseKnown(await r.json());
      } catch {
        /* said below */
      }
      if (stop) return;
      const late = Date.now() - t0 > GIVE_UP_MS;
      if (reply) {
        had = true;
        fails = 0;
        const waiting = reply.pending.length > 0 || reply.deepPending.length > 0;
        // settled: every catalogue answered or said it could not. Those that could not are asked again by the lab's retry
        // (flow.tsx, useRetry), which shows when; this lookup ends here
        if (!waiting) return on({ state: 'ok', ...reply });
        if (late) return on({ state: 'ok', ...giveUp(reply), gaveUp: true });
        on({ state: 'ok', ...reply });
      } else if (++fails >= 3 || late) {
        if (!had) on({ ...KNOWN_OFF, state: 'failed' });
        return;
      }
      await new Promise((res) => setTimeout(res, ASK_AGAIN_MS));
    }
  })();
  return () => {
    stop = true;
    ctl.abort();
  };
}

/** The status line of the overlay: the counts on the picture so far, who is still being asked, and who did not answer. */
export function knownStatus(k: KnownState, counts?: { known: number; noMatch: number } | null, nextIn?: number | null): string {
  if (k.state === 'off') return '';
  if (k.state === 'loading') return 'asking Gaia, SIMBAD and JPL…';
  const parts: string[] = [];
  const ok = k.state === 'ok';
  // "unknown" is said only when every deeper catalogue answered; until then the sources are without a match so far
  const deepOk = ok && !k.deepSilent.length;
  if (counts && ok) parts.push(`known ${counts.known}`, `${deepOk ? 'unknown' : 'no match'} ${counts.noMatch}`);
  const waiting = ok ? [...k.pending.map((c) => SOURCE_WORD[c]), ...k.deepPending] : [];
  if (waiting.length) parts.push(`asking ${waiting.join(', ')}…`);
  const silent = [
    ...KNOWN_SOURCES.filter((c) => (!ok || !k.answered[c]) && !(ok && k.pending.includes(c))).map((c) => SOURCE_WORD[c]),
    ...(ok ? k.deepSilent.filter((c) => !k.deepPending.includes(c)) : []),
  ];
  if (silent.length) parts.push(`${silent.join(', ')}: ${nextIn != null ? `asking again in ${nextIn} s` : 'no answer'}`);
  return parts.join(' · ');
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
/** a source only a deeper catalogue has (2MASS, WISE, faint Gaia): its own quiet colour */
const DEEP_RING = 'rgba(150, 215, 205, 0.8)';
/** a known object's ring sits outside the star's own glow: the width of a SPHEREx point (6.2″) on the picture, more for a bright star */
export function ringRadius(mag: number | null | undefined, pxPerArcsec: number): number {
  const psf = 6.2 * pxPerArcsec;
  const bright = mag != null ? Math.max(0, 13 - mag) * 0.9 : 0;
  return Math.max(4.5, Math.min(16, 0.75 * psf + bright));
}
/** the ring of a detected source with no catalogue match (CSS px) */
const NOMATCH_R = 10;
/** a star faster than this gets its one-year arrow (″/yr) */
export const ARROW_MIN_RATE = 0.3;
/** at most this many rings are drawn on one picture (the brightest first) */
const MAX_RINGS = 4000;

/** What of the catalogues the picture of a pass shows, and what it shows that no catalogue has. */
export interface KnownMatch {
  /** ids of the catalogue objects with a detected source at their place */
  shown: Set<string>;
  /** ids of the deeper-catalogue entries (2MASS, WISE, faint Gaia) with a detected source at their place: known, ringed too */
  deep?: Set<string>;
  /** detected sources with no catalogue object at their place, as fractions of the picture (u right, v down) */
  noMatch: { u: number; v: number; snr: number }[];
}

/** a catalogue object within this of a detected peak is that source: 7″ (a SPHEREx pixel is 6.2″), at least 2 grid pixels */
export const MATCH_ARCSEC = 7;
/** a detection with no ring is a known source when a deeper-catalogue entry is this close at the date shown (their places are
 *  good to an arcsecond; the peak is a pixel centre): 3″, at least one grid pixel */
export const DEEP_ARCSEC = 3;
/** when there is no picture to compare with (Days, a field still loading): objects at least this bright are ringed */
export const SHOWN_MAG = 15;

/** Match the catalogue objects at the date shown to the peaks of the picture (grid pixels, n × n, `step` arcseconds each). */
export function matchKnown(
  objects: KnownObject[],
  peaks: Peak[],
  n: number,
  step: number,
  uvOf: (ra: number, dec: number) => [number, number],
  year: number,
  deep: KnownObject[] = [],
): KnownMatch {
  const r = Math.max(2, MATCH_ARCSEC / step);
  const rd = Math.max(1, DEEP_ARCSEC / step);
  const dpts: { x: number; y: number; o: KnownObject }[] = [];
  for (const o of deep) {
    const at = placeAt(o, year);
    if (!at) continue;
    const [u, v] = uvOf(at[0], at[1]);
    if (u >= -0.05 && v >= -0.05 && u <= 1.05 && v <= 1.05) dpts.push({ x: u * n, y: v * n, o });
  }
  const dcat = new Buckets(dpts, Math.max(4, rd));
  const pts: { x: number; y: number; o: KnownObject }[] = [];
  for (const o of objects) {
    const at = placeAt(o, year);
    if (!at) continue;
    const [u, v] = uvOf(at[0], at[1]);
    if (u < -0.05 || v < -0.05 || u > 1.05 || v > 1.05) continue;
    pts.push({ x: u * n, y: v * n, o });
  }
  const cat = new Buckets(pts, Math.max(4, r));
  // beside a bright star the picture shows its halo, spikes and ghosts: bumps there are not sources without a match
  const bright = pts
    .filter((q) => q.o.mag != null && q.o.mag <= 10)
    .map((q) => ({ x: q.x, y: q.y, r: (q.o.mag! <= 6 ? 120 : q.o.mag! <= 9 ? 75 : 30) / step }));
  const shown = new Set<string>();
  const deepShown = new Set<string>();
  const noMatch: KnownMatch['noMatch'] = [];
  for (const p of peaks) {
    const hit = cat.near(p.x, p.y, r);
    if (hit) {
      shown.add(hit.o.id);
      continue;
    }
    const dh = dcat.near(p.x, p.y, rd);
    // a source only a deeper catalogue has (an infrared star in 2MASS or WISE, a faint Gaia star): known, and ringed as such
    if (dh) deepShown.add(dh.o.id);
    // a bump in a diffuse glow is the halo of a bright star (which may lie outside the frame), not a source without a match
    else if (p.snr >= NOMATCH_SIGMA && !p.glow && !bright.some((b) => Math.hypot(b.x - p.x, b.y - p.y) < b.r))
      noMatch.push({ u: p.x / n, v: p.y / n, snr: p.snr });
  }
  return { shown, deep: deepShown, noMatch };
}

export interface KnownDrawEnv {
  /** what the picture of the pass shows (null when there is no picture to compare with) */
  match: KnownMatch | null;
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
  /** in how many of the passes seen so far a source stands at this place of the picture (fractions), and of how many */
  seenIn?: (u: number, v: number) => [number, number] | null;
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
  const { ctx, rects, wall, uvOf, year, fovArc, placed, pointer, hidden, match } = env;
  const objs =
    k.objects.length > MAX_RINGS
      ? k.objects
          .slice()
          .sort((a, b) => (a.mag ?? 99) - (b.mag ?? 99))
          .slice(0, MAX_RINGS)
      : k.objects;
  let hover: { o: KnownObject; x: number; y: number; r: number; box: (typeof rects)[number]; d: number } | null = null;
  const named: { o: KnownObject; x: number; y: number; r: number; box: (typeof rects)[number] }[] = [];
  let deepHover: { o: KnownObject; x: number; y: number; r: number; d: number } | null = null;
  // the faintest star ringed: every detected one up to 10′, G 17 at 20′, G 13 at 60′ (a star without a magnitude: up to 10′ only)
  const lim = fovArc <= 700 ? 99 : fovArc <= 1300 ? 17 : 13;
  const quiet = fovArc > 700 ? 0.4 : 0.6;
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
        // what the picture can show: a source detected at its place in any pass, a star bright enough to see, a named one, a
        // Solar System body
        // a wide field rings the brighter stars only (60′ has thousands): the limit grows with the field, never per pass
        if (!o.label && src !== 'jpl' && !((match?.shown.has(o.id) && (o.mag ?? 99) <= lim) || (o.mag ?? 99) <= Math.min(SHOWN_MAG, lim))) continue;
        const at = placeAt(o, year);
        if (!at) continue;
        const [u, v] = uvOf(at[0], at[1]);
        if (u < -0.02 || v < -0.02 || u > 1.02 || v > 1.02) continue;
        const x = box.x + u * box.w;
        const y = box.y + v * box.h;
        if (!wall && hidden(x, y)) continue;
        // a known object: one quiet ring, just outside its star
        const r = wall ? 3 : Math.max(o.label ? 6 : 0, ringRadius(o.mag, pxPerArcsec));
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
      ctx.globalAlpha = quiet;
      ctx.lineWidth = 0.9;
      ctx.stroke(thin);
      ctx.globalAlpha = 1;
      ctx.lineWidth = 1.1;
      ctx.stroke(bold);
      ctx.stroke(arrows);
    }
    // the sources only a deeper catalogue has: known too (an infrared star of 2MASS or WISE has no Gaia entry)
    if (match?.deep?.size && fovArc <= 700) {
      const ring = new Path2D();
      for (const o of k.deep) {
        if (!match.deep.has(o.id)) continue;
        const at = placeAt(o, year);
        if (!at) continue;
        const [u, v] = uvOf(at[0], at[1]);
        if (u < -0.02 || v < -0.02 || u > 1.02 || v > 1.02) continue;
        const x = box.x + u * box.w;
        const y = box.y + v * box.h;
        if (!wall && hidden(x, y)) continue;
        const r = wall ? 3 : ringRadius(o.mag, pxPerArcsec);
        ring.moveTo(x + r, y);
        ring.arc(x, y, r, 0, 2 * Math.PI);
        if (pointer && !wall) {
          const d = Math.hypot(pointer.x - x, pointer.y - y);
          if (d <= r + 4 && (!deepHover || d < deepHover.d)) deepHover = { o, x, y, r, d };
        }
      }
      ctx.strokeStyle = DEEP_RING;
      ctx.globalAlpha = 0.6;
      ctx.lineWidth = 0.9;
      ctx.stroke(ring);
      ctx.globalAlpha = 1;
    }
    ctx.restore();
  }
  // what the eye must land on: detected sources with no catalogue object at their place
  let lone: { x: number; y: number; u: number; v: number } | null = null;
  if (match && !wall) {
    const ring = new Path2D();
    for (const box of rects)
      for (const q of match.noMatch) {
        const x = box.x + q.u * box.w;
        const y = box.y + q.v * box.h;
        if (hidden(x, y)) continue;
        ring.moveTo(x + NOMATCH_R, y);
        ring.arc(x, y, NOMATCH_R, 0, 2 * Math.PI);
        placed.push({ x0: x - NOMATCH_R - 2, y0: y - NOMATCH_R - 2, x1: x + NOMATCH_R + 2, y1: y + NOMATCH_R + 2 });
        if (pointer && Math.hypot(pointer.x - x, pointer.y - y) <= NOMATCH_R + 3) lone = { x, y, u: q.u, v: q.v };
      }
    ctx.strokeStyle = 'rgba(0,0,0,0.6)';
    ctx.lineWidth = 3.4;
    ctx.stroke(ring);
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = 1.6;
    ctx.stroke(ring);
  }
  if (deepHover && !hover && !lone) {
    const h = deepHover;
    ctx.font = '500 11px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
    const cats = (h.o.catalogues ?? []).map((c) => DEEP[c] ?? c).join(', ');
    text(ctx, `known · ${cats || 'a deeper catalogue'}`, h.x + h.r + 5, h.y + 4, '#d8f0ec');
  }
  if (lone && !hover) {
    ctx.font = '600 11px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
    // in one pass only, it may be a cosmic ray or a hot pixel: the passes say so, the word stays the data's
    const seen = env.seenIn?.(lone.u, lone.v);
    const s = seen && seen[1] >= 2 ? `no catalogue match · in ${seen[0]} of ${seen[1]} passes` : 'no catalogue match';
    text(ctx, s, lone.x + NOMATCH_R + 5, lone.y + 4, seen && seen[1] >= 3 && seen[0] <= 1 ? '#ffd08a' : '#ffffff');
  }
  if (!wall) {
    ctx.font = '500 11px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
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
    // a wide field names its fastest and brightest dozen only (60′ named every bright star, and the names ran into each other)
    const many = fovArc > 1300 ? named.slice(0, 12) : named;
    for (const q of many) if (q.o !== hover?.o) put(q, false);
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

/** What the catalogues of `/api/known` have at a place on a date: the nearest drawn object within 8″, said as the picture's
 *  ring says it ("known · SIMBAD Barnard's star · 10.39″/yr"), else a deeper-catalogue entry
 *  within 3″, else that no catalogue has it (or which ones have not answered yet). One source of truth with the picture. */
export function catalogueAt(
  k: KnownState,
  ra: number,
  dec: number,
  year: number,
): { word: string; object: KnownObject | null; sep: number | null } {
  if (k.state === 'off' || k.state === 'loading') return { word: 'asking the catalogues…', object: null, sep: null };
  if (k.state === 'failed') return { word: 'the catalogues did not answer', object: null, sep: null };
  const cosd = Math.cos((dec * Math.PI) / 180);
  const away = (p: [number, number]) => Math.hypot((((p[0] - ra + 540) % 360) - 180) * cosd, p[1] - dec) * 3600;
  let best: { o: KnownObject; d: number } | null = null;
  for (const o of k.objects) {
    const p = placeAt(o, year);
    if (!p) continue;
    const d = away(p);
    if (d < 8 && (!best || d < best.d)) best = { o, d };
  }
  if (best) {
    const o = best.o;
    const gaia = o.source === 'gaia' ? `Gaia DR3 ${o.id.replace(/^gaia:/, '')}` : '';
    const name = o.source === 'gaia' ? (o.simbad ? `SIMBAD ${o.simbad}` : '') : `${SOURCE_WORD[o.source]} ${o.name}`.trim();
    const rate = o.source === 'jpl' ? speedWord(o) : o.pm_total != null && o.pm_total >= 50 ? `${(o.pm_total / 1000).toFixed(2)}″/yr` : '';
    // one short line (the passport below has the rows): the name it is known by, else its Gaia id; a fast one's rate
    return { word: `known · ${[name || gaia, rate].filter(Boolean).join(' · ')}`, object: o, sep: best.d };
  }
  for (const o of k.deep) {
    const p = placeAt(o, year);
    if (p && away(p) < DEEP_ARCSEC) {
      const cat = o.id.split(':')[0];
      return {
        word: `known · in ${cat === 'gaia' ? 'Gaia DR3 (fainter than G 19)' : cat === 'catwise' ? 'CatWISE2020' : cat === 'allwise' ? 'AllWISE' : '2MASS'}`,
        object: null,
        sep: null,
      };
    }
  }
  const silent = [...KNOWN_SOURCES.filter((c) => !k.answered[c]).map((c) => SOURCE_WORD[c]), ...k.deepSilent];
  const asked = k.pending.length > 0 || k.deepPending.length > 0;
  return {
    word: silent.length ? `no match so far · ${silent.join(', ')} ${asked ? 'being asked' : 'did not answer'}` : 'in no catalogue',
    object: null,
    sep: null,
  };
}

/** What the field's lookup already holds about a place: the identity of a ringed known source (or a deeper-catalogue match), taken
 *  as it is instead of asking the catalogues again. `covered`: the catalogues of /api/v1/context the field's lookup answered for it
 *  (the passport skips them); the others are still asked. */
export interface Held {
  /** the name to show: SIMBAD's when it has one, else the catalogue id (Gaia DR3 …, CatWISE …) */
  name: string;
  gaia: {
    source_id: string;
    g: number | null;
    bp_rp: number | null;
    ruwe: number | null;
    parallax: number | null;
    pmra: number | null;
    pmdec: number | null;
  } | null;
  /** SIMBAD's answer for it: a name (and type), no entry (SIMBAD answered for the field and has none here), or null (not covered) */
  simbad: { name: string; type: string | null } | 'none' | null;
  /** a JPL body: its name and type */
  jpl: { name: string; type: string | null } | null;
  /** a deeper-catalogue match only: the catalogues and ids */
  deep: { catalogues: string[]; ids: string[] } | null;
  covered: ('gaia' | 'simbad')[];
}

const DEEP_WORD: Record<string, string> = { gaia: 'Gaia DR3', catwise: 'CatWISE2020', allwise: 'AllWISE', twomass: '2MASS' };

export function heldAt(k: KnownState, ra: number, dec: number, year: number): Held | null {
  if (k.state !== 'ok') return null;
  const c = catalogueAt(k, ra, dec, year);
  const o = c.object;
  if (o) {
    if (o.source === 'jpl')
      return { name: o.name, gaia: null, simbad: null, jpl: { name: o.name, type: o.type ?? null }, deep: null, covered: [] };
    if (o.source === 'gaia') {
      const sid = o.id.replace(/^gaia:/, '');
      const simbad = o.simbad
        ? { name: o.simbad, type: o.type && o.type !== 'star' ? o.type : null }
        : k.answered.simbad
          ? ('none' as const)
          : null;
      return {
        name: o.simbad ?? `Gaia DR3 ${sid}`,
        gaia: {
          source_id: sid,
          g: o.mag ?? null,
          bp_rp: o.bp_rp ?? null,
          ruwe: o.ruwe ?? null,
          parallax: o.parallax ?? null,
          pmra: o.pmra ?? null,
          pmdec: o.pmdec ?? null,
        },
        simbad,
        jpl: null,
        deep: null,
        covered: simbad ? ['gaia', 'simbad'] : ['gaia'],
      };
    }
    // a SIMBAD object that Gaia does not have at its place (Gaia answered for the field: none there)
    return {
      name: o.name,
      gaia: null,
      simbad: { name: o.name, type: o.type ?? null },
      jpl: null,
      deep: null,
      covered: k.answered.gaia ? ['gaia', 'simbad'] : ['simbad'],
    };
  }
  const cosd = Math.cos((dec * Math.PI) / 180);
  for (const d of k.deep) {
    const p = placeAt(d, year);
    if (!p || Math.hypot((((p[0] - ra + 540) % 360) - 180) * cosd, p[1] - dec) * 3600 >= DEEP_ARCSEC) continue;
    const ids = d.ids?.length ? d.ids : [d.id];
    const first = ids[0] ?? d.id;
    const [cat, sid] = [first.split(':')[0] ?? '', first.slice(first.indexOf(':') + 1)];
    return {
      name: `${DEEP_WORD[cat] ?? cat} ${sid}`,
      gaia: null,
      simbad: k.answered.simbad ? 'none' : null,
      jpl: null,
      deep: { catalogues: d.catalogues ?? [], ids },
      covered: k.answered.simbad ? ['simbad'] : [],
    };
  }
  return null;
}

// ---------------------------------------------------------------------------------------------- the field's one set (GET /api/known/field)

/** A field's known objects as one set that stays from pass to pass: the catalogue sources the field shows (matched once, against the
 *  sources found in at least half of the passes), and the measured sources no catalogue has. */
export interface FieldKnown {
  state: 'asking' | 'ok' | 'no_field' | 'failed';
  complete: boolean;
  /** what is ringed: catalogue objects (deep ones marked by `source: 'gaia'` and a `catalogues` list) at the field's epoch */
  objects: KnownObject[];
  deep: KnownObject[];
  /** the measured sources with no catalogue source within 7″ */
  unmatched: { ra: number; dec: number; snr: number; passes: number }[];
  measured: number;
}
export const FIELD_ASKING: FieldKnown = { state: 'asking', complete: false, objects: [], deep: [], unmatched: [], measured: 0 };

/** The reply made safe (a saturated star's S/N came back as the word Infinity, which is not JSON) */
export function parseFieldKnown(text: string): FieldKnown {
  const r = JSON.parse(text.replace(/:\s*-?(Infinity|NaN)\b/g, ':null')) as {
    state?: string;
    complete?: boolean;
    epoch_mjd?: number;
    measured?: number;
    sources?: { id: string; source: string; ra: number; dec: number; mag?: number | null; seen?: boolean; name?: string; label?: boolean; catalogues?: string[] }[];
    unmatched?: { ra: number; dec: number; snr?: number | null; passes?: number }[];
  };
  if (r.state !== 'ok') return { ...FIELD_ASKING, state: r.state === 'no_field' ? 'no_field' : 'failed' };
  const epoch = yearOfMjd(Number(r.epoch_mjd ?? 61000));
  const objects: KnownObject[] = [];
  const deep: KnownObject[] = [];
  for (const s of r.sources ?? []) {
    if (!s || !s.seen || !Number.isFinite(s.ra) || !Number.isFinite(s.dec)) continue;
    const o: KnownObject = {
      id: String(s.id),
      source: s.source === 'simbad' ? 'simbad' : 'gaia',
      name: s.name ?? '',
      mag: Number.isFinite(s.mag as number) ? (s.mag as number) : null,
      positions: [{ epoch, ra: s.ra, dec: s.dec }],
      label: false, // the named and fast ones are drawn from /api/known, with their motion
      ...(s.source === 'deep' ? { catalogues: s.catalogues ?? [] } : {}),
    };
    (s.source === 'deep' ? deep : objects).push(o);
  }
  return {
    state: 'ok',
    complete: !!r.complete,
    objects,
    deep,
    unmatched: (r.unmatched ?? [])
      .filter((q) => Number.isFinite(q.ra) && Number.isFinite(q.dec))
      .map((q) => ({ ra: q.ra, dec: q.dec, snr: Number.isFinite(q.snr as number) ? (q.snr as number) : 99, passes: q.passes ?? 0 })),
    measured: Number(r.measured ?? 0),
  };
}

/** Ask for the field's set, and again every few seconds while a catalogue is still being asked (at most two minutes). */
export function followFieldKnown(ra: number, dec: number, fov: number, on: (f: FieldKnown) => void): () => void {
  let stop = false;
  let tries = 0;
  const ask = async () => {
    try {
      const r = await fetch(`/api/known/field?ra=${ra.toFixed(5)}&dec=${dec.toFixed(5)}&fov=${fov}&wait=2`, { cache: 'no-store' });
      if (stop) return;
      if (!r.ok) return on({ ...FIELD_ASKING, state: r.status === 404 ? 'no_field' : 'failed' });
      const f = parseFieldKnown(await r.text());
      if (stop) return;
      on(f);
      if (f.state === 'ok' && !f.complete && ++tries < 30) setTimeout(() => !stop && void ask(), 4000);
    } catch {
      if (!stop) on({ ...FIELD_ASKING, state: 'failed' });
    }
  };
  void ask();
  return () => {
    stop = true;
  };
}
