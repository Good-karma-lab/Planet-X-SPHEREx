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
