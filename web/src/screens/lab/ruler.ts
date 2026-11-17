// The ruler of the lab: the shift between two places on the sky, and, when the two were taken on different dates, the rate.

export interface RulerPoint {
  ra: number;
  dec: number;
  /** the date of the frame on show when the point was taken, decimal year (null when unknown) */
  epoch: number | null;
  /** the pass on show then (Months), null in Days */
  pass: number | null;
}

export interface RulerResult {
  /** the shift, arcseconds */
  shift: number;
  /** position angle of the shift, degrees east of north (0 = north, 90 = east) */
  pa: number;
  /** arcseconds per year, when the two points are on different dates; else null */
  rate: number | null;
  /** years between the two points */
  dt: number | null;
}

export function rulerOf(a: RulerPoint, b: RulerPoint): RulerResult {
  const east = (((b.ra - a.ra + 540) % 360) - 180) * Math.cos((((a.dec + b.dec) / 2) * Math.PI) / 180) * 3600;
  const north = (b.dec - a.dec) * 3600;
  const shift = Math.hypot(east, north);
  const pa = shift > 0 ? ((Math.atan2(east, north) * 180) / Math.PI + 360) % 360 : 0;
  const dt = a.epoch != null && b.epoch != null ? b.epoch - a.epoch : null;
  // a rate needs two different dates: at least half an hour apart (two visits of a pass)
  const rate = dt != null && Math.abs(dt) > 0.5 / 8766 ? shift / Math.abs(dt) : null;
  return { shift, pa, rate, dt };
}

const COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];

/** "12.4″ · 10.3″/yr · toward 355° (N)"; a rate within a pass (days apart) is said per hour. */
export function rulerWord(r: RulerResult): string {
  const parts = [`${r.shift.toFixed(r.shift >= 100 ? 0 : 1)}″`];
  if (r.rate != null && r.dt != null) {
    if (Math.abs(r.dt) < 0.1) parts.push(`${(r.rate / 8766).toFixed(2)}″/h`);
    else parts.push(`${r.rate.toFixed(r.rate >= 100 ? 0 : r.rate >= 10 ? 1 : 2)}″/yr`);
  }
  if (r.shift > 0) parts.push(`toward ${r.pa.toFixed(0)}° (${COMPASS[Math.round(r.pa / 45) % 8]})`);
  return parts.join(' · ');
}
