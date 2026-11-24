// Where a task's track is on the frame on show, and what of the lab is blind while its questions are open.

export interface TrackPoint {
  i: number;
  mjd: number;
  ra: number;
  dec: number;
}

/** A frame of the film belongs to a track's visit when it is that visit (its own date within two hours). */
const SAME_VISIT_D = 2 / 24;

/** The track's place on frame `k` of the film (`mjds` its frames): its own visit (`exact`) when the frame is one, else the place of the
 *  visit nearest in time, which the caller must say is another visit's. `frame`: the film's frame of that visit. */
export function placeOnTrack(pts: TrackPoint[], mjds: number[], k: number): { point: TrackPoint; exact: boolean; frame: number } | null {
  const now = mjds[k];
  if (now == null || !pts.length) return null;
  const frameOf = (m: number) => {
    let b = 0;
    mjds.forEach((x, i) => Math.abs(x - m) < Math.abs(mjds[b]! - m) && (b = i));
    return Math.abs(mjds[b]! - m) < SAME_VISIT_D ? b : -1;
  };
  const own = pts.find((q) => frameOf(q.mjd) === k);
  if (own) return { point: own, exact: true, frame: k };
  const point = pts.reduce((b, q) => (Math.abs(q.mjd - now) < Math.abs(b.mjd - now) ? q : b));
  return { point, exact: false, frame: frameOf(point.mjd) };
}

const D2R = Math.PI / 180;

/** The distance, arcseconds, from a place to a path's nearest point (a path of one point: to it). */
export function offPath(ra: number, dec: number, path: { ra: number; dec: number }[]): number {
  const c = Math.cos(dec * D2R);
  const at = path.map((q) => [(((q.ra - ra + 540) % 360) - 180) * c * 3600, (q.dec - dec) * 3600] as const);
  let best = Infinity;
  at.forEach(([x, y], i) => {
    best = Math.min(best, Math.hypot(x, y));
    const n = at[i + 1];
    if (!n) return;
    const [dx, dy] = [n[0] - x, n[1] - y];
    const t = Math.max(0, Math.min(1, -(x * dx + y * dy) / (dx * dx + dy * dy || 1)));
    best = Math.min(best, Math.hypot(x + t * dx, y + t * dy));
  });
  return best;
}

/** How near the find in question a selected thing must be to be the find itself (a control's catalogue name stands within arcseconds). */
export const BLIND_ARCSEC = 20;

/** Whether a selected place is the find in question: near the find's own place, or on its track. */
export const isBlindPlace = (ra: number, dec: number, find: { ra: number; dec: number } | null, path: { ra: number; dec: number }[]): boolean =>
  (!!find && offPath(ra, dec, [find]) < BLIND_ARCSEC) || (path.length > 0 && offPath(ra, dec, path) < BLIND_ARCSEC);
