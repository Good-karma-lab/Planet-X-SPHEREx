import { formatDec, formatRA } from '../../lib/sky';

/** JD of the start of the SPHEREx survey (2025-04-01). */
export const MISSION_JD0 = 2460766.5;
/** MJD range the exposure index covers when the service does not say. */
export const DEFAULT_MJD_RANGE: [number, number] = [60786, 61318];

export interface SmallBody {
  spkid: number;
  full_name: string;
  pdes?: string;
  name?: string;
  kind: string;
  class?: string;
  H?: number | null;
  diameter?: string | null;
  albedo?: string | null;
  a?: string;
  e?: string;
  i?: string;
  q?: string | null;
}

export interface PathPoint {
  jd: number;
  ra: number;
  dec: number;
  V?: number;
  delta?: number;
  kind?: string;
}

export interface BodyPath {
  body: SmallBody;
  path: PathPoint[];
  today: PathPoint;
  /** MJD range the exposures cover: the path is green inside it */
  mjdRange: [number, number];
}

export const jdToDate = (jd: number) => new Date((jd - 2440587.5) * 864e5);

/** The geocentric path of a small body from the start of the survey to a month from now, and where it is today (through the service's ephemeris). null: the service has no such route. */
export async function fetchBodyPath(body: SmallBody, signal?: AbortSignal): Promise<BodyPath | null> {
  const now = Date.now() / 864e5 + 2440587.5;
  const j1 = now + 30;
  const n = 150;
  const jds = Array.from({ length: n }, (_, i) => MISSION_JD0 + ((j1 - MISSION_JD0) * i) / (n - 1));
  jds.push(now);
  jds.sort((x, y) => x - y);
  const q = new URLSearchParams({ target: body.pdes ?? String(body.spkid), jd: jds.map((x) => x.toFixed(3)).join(',') });
  const r = await fetch(`/api/v1/ephem?${q}`, { signal });
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`service answered ${r.status}`);
  const e = (await r.json()) as { ephem?: { ra: number; dec: number; V?: number; delta_au?: number; mag_kind?: string }[] };
  if (!e.ephem?.length) throw new Error('no ephemeris');
  const path = e.ephem.map((x, i) => ({ jd: jds[i]!, ra: x.ra, dec: x.dec, V: x.V, delta: x.delta_au, kind: x.mag_kind }));
  const today = path.reduce((b, p) => (Math.abs(p.jd - now) < Math.abs(b.jd - now) ? p : b));
  let mjdRange = DEFAULT_MJD_RANGE;
  try {
    const h = (await (await fetch('/api/v1/health', { signal })).json()) as { index?: { mjd_range?: [number, number] } | null };
    if (h.index?.mjd_range) mjdRange = h.index.mjd_range;
  } catch {
    // the default range stands
  }
  return { body, path, today, mjdRange };
}

/** Where `today` is, in words. */
export function todayText(p: BodyPath): string {
  const t = p.today;
  return `today ${formatRA(t.ra)} ${formatDec(t.dec)}${t.V != null ? ` · V ${t.V} (${t.kind ?? 'magnitude'})` : ''}${t.delta != null ? ` · ${(+t.delta).toFixed(2)} au from Earth` : ''}`;
}

/**
 * The path of the selected small body on the globe or map: dashed amber over the whole span, green where SPHEREx has observed, date
 * ticks about every two months, today marked. `project` maps a sky place to canvas pixels (and whether it is on the visible side);
 * `maxJump` is the screen distance above which two neighbouring points are not joined (the seam of a map).
 */
export function drawBodyPath(
  c: CanvasRenderingContext2D,
  project: (ra: number, dec: number) => [number, number, boolean],
  p: BodyPath,
  maxJump: number,
  font = 'ui-monospace, Menlo, monospace',
): void {
  c.save();
  const seg = (pred: (q: PathPoint) => boolean, style: string, w: number, dash: number[]) => {
    c.strokeStyle = style;
    c.lineWidth = w;
    c.setLineDash(dash);
    c.beginPath();
    let prev: [number, number] | null = null;
    for (const q of p.path) {
      if (!pred(q)) {
        prev = null;
        continue;
      }
      const s = project(q.ra, q.dec);
      if (!s[2]) {
        prev = null;
        continue;
      }
      if (!prev || Math.hypot(s[0] - prev[0], s[1] - prev[1]) > maxJump) c.moveTo(s[0], s[1]);
      else c.lineTo(s[0], s[1]);
      prev = [s[0], s[1]];
    }
    c.stroke();
    c.setLineDash([]);
  };
  seg(() => true, 'rgba(255,180,84,.85)', 2, [5, 4]);
  seg((q) => q.jd - 2400000.5 <= p.mjdRange[1], '#6aff9e', 3.5, []);
  c.font = `600 11px ${font}`;
  c.lineWidth = 3;
  p.path.forEach((q, i) => {
    if (i % 15) return;
    const s = project(q.ra, q.dec);
    if (!s[2]) return;
    c.fillStyle = '#ffb454';
    c.beginPath();
    c.arc(s[0], s[1], 3, 0, 7);
    c.fill();
    const label = jdToDate(q.jd).toLocaleString('en-US', { month: 'short', year: 'numeric', timeZone: 'UTC' });
    c.strokeStyle = '#000';
    c.strokeText(label, s[0] + 6, s[1] - 5);
    c.fillStyle = '#fff';
    c.fillText(label, s[0] + 6, s[1] - 5);
  });
  const t = project(p.today.ra, p.today.dec);
  if (t[2]) {
    const label = `${p.body.full_name} · today`;
    c.fillStyle = '#ffb454';
    c.strokeStyle = '#fff';
    c.lineWidth = 2;
    c.beginPath();
    c.arc(t[0], t[1], 6, 0, 7);
    c.fill();
    c.stroke();
    c.lineWidth = 3;
    c.strokeStyle = '#000';
    c.fillStyle = '#fff';
    c.strokeText(label, t[0] + 10, t[1] + 4);
    c.fillText(label, t[0] + 10, t[1] + 4);
  }
  c.restore();
}

/** The epoch (decimal year) of each all-sky backdrop of the globe: the place of a moving object is pinned at the DATE OF THE BACKDROP on show. */
export const BACKDROP_YEAR: Record<string, number> = { dss2: 1991, '2mass': 1999.5, wise: 2010.5 };

/** The SPHEREx survey as decimal years (2025-04-01 to the end of the index). */
export const SPHEREX_YEARS: [number, number] = [2025.25, 2026.9];

export interface MovingObject {
  name: string;
  /** J2000 place, degrees */
  ra: number;
  dec: number;
  /** proper motion, mas/yr (pmra includes cos dec) */
  pmra: number;
  pmdec: number;
}

/** Where a catalogue object is in a given year (linear proper motion from J2000). */
export function placeAt(o: MovingObject, year: number): [number, number] {
  const dt = (year - 2000) / 3.6e6;
  return [o.ra + (o.pmra * dt) / Math.max(1e-3, Math.cos((o.dec * Math.PI) / 180)), o.dec + o.pmdec * dt];
}

/**
 * The path of a moving catalogue object on the globe or map, as the lab draws it: the pin at the object's place on the date of the backdrop shown, the path from the earliest
 * archive image to 2050 with a dot and a year at every image date, the stretch of the SPHEREx survey highlighted, the dot of the backdrop on show highlighted, and an open
 * ring instead of a filled pin once the view is zoomed in (the star under the pin stays visible).
 *  - `imageYears`: the dates of the archive images (decimal years), `backdropYear`: the epoch of the backdrop on show (BACKDROP_YEAR).
 *  - `project`: sky → canvas pixels and visibility; `maxJump`: the screen distance above which neighbouring points are not joined.
 */
export function drawMovingPath(
  c: CanvasRenderingContext2D,
  project: (ra: number, dec: number) => [number, number, boolean],
  o: MovingObject,
  opts: { imageYears: number[]; backdropYear: number; zoomedIn: boolean; maxJump: number; font?: string },
): void {
  const y0 = Math.min(...opts.imageYears, opts.backdropYear);
  const y1 = 2050;
  const at = (y: number) => {
    const p = placeAt(o, y);
    return project(p[0], p[1]);
  };
  c.save();
  const line = (a: number, b: number, style: string, w: number, dash: number[]) => {
    c.strokeStyle = style;
    c.lineWidth = w;
    c.setLineDash(dash);
    c.beginPath();
    let prev: [number, number] | null = null;
    for (let i = 0; i <= 120; i++) {
      const s = at(a + ((b - a) * i) / 120);
      if (!s[2]) {
        prev = null;
        continue;
      }
      if (!prev || Math.hypot(s[0] - prev[0], s[1] - prev[1]) > opts.maxJump) c.moveTo(s[0], s[1]);
      else c.lineTo(s[0], s[1]);
      prev = [s[0], s[1]];
    }
    c.stroke();
    c.setLineDash([]);
  };
  line(y0, y1, 'rgba(255,180,84,.85)', 2, [5, 4]);
  line(SPHEREX_YEARS[0], SPHEREX_YEARS[1], '#6aff9e', 3.5, []);
  c.font = `600 11px ${opts.font ?? 'ui-monospace, Menlo, monospace'}`;
  c.lineWidth = 3;
  const label = (x: number, y: number, t: string, on: boolean) => {
    c.strokeStyle = '#000';
    c.strokeText(t, x + 7, y - 6);
    c.fillStyle = on ? '#fff' : '#d6e7ed';
    c.fillText(t, x + 7, y - 6);
  };
  for (const y of opts.imageYears) {
    const s = at(y);
    if (!s[2]) continue;
    const on = Math.abs(y - opts.backdropYear) < 0.6;
    c.fillStyle = on ? '#5fe3d6' : '#ffb454';
    c.strokeStyle = '#fff';
    c.lineWidth = on ? 2 : 1;
    c.beginPath();
    c.arc(s[0], s[1], on ? 5.5 : 3, 0, 7);
    c.fill();
    if (on) c.stroke();
    c.lineWidth = 3;
    label(s[0], s[1], String(Math.round(y)), on);
  }
  // 2050 and the survey's own dates at the ends of their stretches
  for (const y of [SPHEREX_YEARS[0], 2050]) {
    const s = at(y);
    if (!s[2]) continue;
    c.fillStyle = y === 2050 ? '#ffb454' : '#6aff9e';
    c.beginPath();
    c.arc(s[0], s[1], 3, 0, 7);
    c.fill();
    label(s[0], s[1], y === 2050 ? '2050' : 'SPHEREx', false);
  }
  // the pin: the place on the date of the backdrop on show
  const p = at(opts.backdropYear);
  if (p[2]) {
    c.lineWidth = 2.2;
    c.strokeStyle = '#fff';
    c.fillStyle = '#5fe3d6';
    c.beginPath();
    c.arc(p[0], p[1], opts.zoomedIn ? 11 : 6.5, 0, 7);
    if (!opts.zoomedIn) c.fill();
    c.stroke();
    c.lineWidth = 3;
    label(p[0] + 8, p[1] + 14, `${o.name} · ${Math.round(opts.backdropYear)}`, true);
  }
  c.restore();
}
