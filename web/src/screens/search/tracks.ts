/**
 * Tracks found inside one pass (the visits of a film), their drawing on the film and the cut-outs of a track.
 * Coordinates are the film's grid pixels, the same indexing as its `sky` array: index = frame * n * n + y * n + x, row 0 north.
 */

export interface TrackPoint {
  /** the film frame (visit) the track was found in */
  f: number;
  x: number;
  y: number;
  /** S/N of the visit's difference at the place, and whether a candidate was found there or the visit was only read at the orbit's place */
  z?: number;
  how?: 'found' | 'recovered';
}

export interface FilmTrack {
  id: number;
  /** visits the track runs through */
  n: number;
  /** rate on the sky, arcsec per hour */
  rate: number;
  pts: TrackPoint[];
  /** how the track was verified: an orbit fitted through its places, or a straight line only */
  method?: 'orbit' | 'line';
  verified?: boolean;
  /** orbit: on − off significance of the places together, distance, residual */
  z_together?: number;
  dist_au?: number;
  rms?: number;
  /** position angle of the motion on the sky, degrees (north through east) */
  pa?: number;
  /** the signal of the track, S/N */
  snr?: number;
  /** a known body the places match */
  known?: { name: string; V?: number | null } | null;
}

export interface FilmControl {
  /** the control runs: the same search on exposures with the times permuted */
  trials: number;
  /** tracks of at least this many visits are drawn */
  min_visits_drawn: number;
  /** chance: mean number of tracks per field by number of visits */
  chance: Record<string, number>;
}

export const trackName = (t: FilmTrack, i: number) => (t.known ? t.known.name : `Track ${i + 1}`);

export interface TrackDraw {
  /** canvas pixels per grid pixel */
  scale: number;
  /** the frame on screen */
  k: number;
  rings: boolean;
  /** a short trail: only the last three places behind the ring, fading (else every earlier place, as small dots) */
  trail?: boolean;
  /** the selected track (-1 or out of range: none) */
  selected: number;
  /** the time of every frame (MJD): the place between two measured visits is interpolated in time; without it, in frame number */
  mjd?: number[];
}

/** One colour per track (amber first), repeated when there are more tracks. */
export const TRACK_COLOURS = ['#ffb454', '#5fe3d6', '#ff8fc7', '#b8a2ff', '#9be564', '#6fb3ff'];
const colourOf = (i: number) => TRACK_COLOURS[i % TRACK_COLOURS.length]!;

/** Where a track is on frame k: its measured place there, or a place interpolated between the two visits around it; null outside the time span of the track. */
export function trackPlace(tr: FilmTrack, k: number, mjd?: number[]): { x: number; y: number; measured: boolean } | null {
  const pts = tr.pts;
  if (!pts.length) return null;
  const hit = pts.find((q) => q.f === k);
  if (hit) return { x: hit.x, y: hit.y, measured: true };
  if (k < pts[0]!.f || k > pts[pts.length - 1]!.f) return null;
  let i = 0;
  while (i < pts.length - 1 && pts[i + 1]!.f < k) i++;
  const a = pts[i]!;
  const b = pts[i + 1]!;
  if (!b) return null;
  const t0 = mjd?.[a.f];
  const t1 = mjd?.[b.f];
  const tk = mjd?.[k];
  const u = t0 != null && t1 != null && tk != null && t1 > t0 ? (tk - t0) / (t1 - t0) : (k - a.f) / (b.f - a.f);
  return { x: a.x + (b.x - a.x) * u, y: a.y + (b.y - a.y) * u, measured: false };
}

const RING = 12;
const BADGE = 17;
/** The badge sits up and to the right of the ring. */
const badgeAt = (x: number, y: number): [number, number] => [x + RING * 0.75 + BADGE / 2, y - RING * 0.75 - BADGE / 2];

/** The details of the selected track in one line. */
export function trackLabel(tr: FilmTrack, i: number): string {
  return `Track ${i + 1} · ${tr.known ? tr.known.name : 'asteroid candidate'} · ${tr.rate.toFixed(1)}″/h${tr.pa != null ? ` → PA ${tr.pa.toFixed(0)}°` : ''} · ${tr.n} visits${tr.snr != null ? ` · S/N ${tr.snr.toFixed(0)}` : ''}`;
}

/**
 * The found bodies on a frame. Each track is a small numbered badge at its ring (17 px, the track's colour, a dark number); only the selected track has its details
 * written out. The ring is solid on a measured visit and dashed at the time-interpolated place between visits (none outside the track's time span); earlier places are
 * small dots on a thin dashed path; while one track is selected the others dim to 35 %. `x`, `y` and the scale are in the canvas' own units (`dpr` scales line widths only).
 */
export function drawTracks(c: CanvasRenderingContext2D, tracks: FilmTrack[], d: TrackDraw, dpr = 1): void {
  if (!d.rings) return;
  const X = (x: number) => (x + 0.5) * d.scale;
  const Y = (y: number) => (y + 0.5) * d.scale;
  const sel = d.selected >= 0 && d.selected < tracks.length ? d.selected : -1;
  c.save();
  c.textBaseline = 'middle';
  c.textAlign = 'center';
  tracks.forEach((tr, i) => {
    if (!tr.pts.length) return;
    const col = colourOf(i);
    const here = trackPlace(tr, d.k, d.mjd);
    if (!here) return; // outside the time span: nothing of this track is drawn
    c.globalAlpha = sel >= 0 && sel !== i ? 0.35 : 1;
    // earlier places: small dots on a thin dashed path that ends at the ring (a short trail: the last three, fading)
    const all = tr.pts.filter((q) => q.f < d.k);
    const past = d.trail ? all.slice(-3) : all;
    c.strokeStyle = col;
    c.lineWidth = 1 * dpr;
    c.globalAlpha *= 0.7;
    c.setLineDash([3 * dpr, 3 * dpr]);
    c.beginPath();
    past.forEach((q, j) => (j ? c.lineTo(X(q.x), Y(q.y)) : c.moveTo(X(q.x), Y(q.y))));
    c.lineTo(X(here.x), Y(here.y));
    c.stroke();
    c.setLineDash([]);
    c.fillStyle = col;
    const a0 = c.globalAlpha;
    past.forEach((q, j) => {
      if (d.trail) c.globalAlpha = a0 * (0.35 + (0.65 * (j + 1)) / past.length);
      c.beginPath();
      c.arc(X(q.x), Y(q.y), (d.trail ? 2.8 : 2.2) * dpr, 0, 7);
      c.fill();
    });
    c.globalAlpha = a0;
    c.globalAlpha = sel >= 0 && sel !== i ? 0.35 : 1;
    // the ring: solid on a measured visit, dashed between visits
    const x = X(here.x);
    const y = Y(here.y);
    c.strokeStyle = 'rgba(0,0,0,.55)';
    c.lineWidth = 3.4 * dpr;
    c.beginPath();
    c.arc(x, y, RING, 0, 7);
    c.stroke();
    c.strokeStyle = col;
    c.lineWidth = (sel === i ? 2.2 : 1.6) * dpr;
    if (!here.measured) c.setLineDash([3 * dpr, 3 * dpr]);
    c.beginPath();
    c.arc(x, y, RING, 0, 7);
    c.stroke();
    c.setLineDash([]);
    // the numbered badge
    const [bx, by] = badgeAt(x, y);
    c.fillStyle = col;
    c.beginPath();
    c.arc(bx, by, BADGE / 2, 0, 7);
    c.fill();
    c.fillStyle = '#071218';
    c.font = `700 ${11 * dpr}px ui-sans-serif, system-ui, sans-serif`;
    c.fillText(String(i + 1), bx, by + 0.5);
    // the details of the selected track only
    if (sel === i) {
      const text = trackLabel(tr, i);
      c.font = `600 ${11.5 * dpr}px ui-sans-serif, system-ui, sans-serif`;
      c.textAlign = 'left';
      c.lineWidth = 3 * dpr;
      c.lineJoin = 'round';
      c.strokeStyle = 'rgba(3,8,15,.8)';
      c.strokeText(text, bx + BADGE / 2 + 5, by);
      c.fillStyle = '#ffffff';
      c.fillText(text, bx + BADGE / 2 + 5, by);
      c.textAlign = 'center';
    }
  });
  c.restore();
}

/** The track whose badge is under a point (canvas units, the same as drawTracks), or -1. `mjd` as in TrackDraw. */
export function hitTrack(tracks: FilmTrack[], k: number, scale: number, x: number, y: number, mjd?: number[]): number {
  let best = -1;
  let bd = BADGE / 2 + 3;
  tracks.forEach((tr, i) => {
    const here = trackPlace(tr, k, mjd);
    if (!here) return;
    const [bx, by] = badgeAt((here.x + 0.5) * scale, (here.y + 0.5) * scale);
    const dd = Math.hypot(x - bx, y - by);
    if (dd < bd) {
      bd = dd;
      best = i;
    }
  });
  return best;
}

/**
 * Blink: a strip of cut-outs of one track, the same piece of sky (the middle of the track, fixed) in up to five of the visits it was found in,
 * side by side and unmarked; the visit on screen is outlined. The pixels are the film's own sky frames; only the crop and the scale are chosen.
 * `box` is the square the film occupies on the canvas (x, y, side in canvas pixels).
 */
export function drawBlink(
  c: CanvasRenderingContext2D,
  tr: FilmTrack,
  k: number,
  sky: Uint8Array,
  n: number,
  box: { x: number; y: number; side: number },
  dpr = 1,
): void {
  const pts = tr.pts;
  if (!pts.length) return;
  const cx = pts.reduce((a, q) => a + q.x, 0) / pts.length;
  const cy = pts.reduce((a, q) => a + q.y, 0) / pts.length;
  const ext = Math.max(...pts.map((q) => Math.hypot(q.x - cx, q.y - cy)));
  const R = Math.min(24, Math.max(9, Math.ceil(ext) + 5));
  const m = 2 * R + 1;
  let use = pts;
  if (use.length > 5) {
    use = [];
    for (let i = 0; i < 5; i++) use.push(pts[Math.round((i * (pts.length - 1)) / 4)]!);
  }
  const gap = 6 * dpr;
  const T = Math.min(Math.round(92 * dpr), Math.floor((box.side - 24 * dpr) / use.length) - gap);
  const tw = use.length * (T + gap) - gap;
  const x0 = box.x + box.side - tw - 8 * dpr;
  const y0 = box.y + box.side - T - 34 * dpr;
  const tc = document.createElement('canvas');
  tc.width = tc.height = m;
  const tx = tc.getContext('2d')!;
  const id = tx.createImageData(m, m);
  c.save();
  c.fillStyle = 'rgba(4,9,13,.94)';
  c.fillRect(x0 - 5 * dpr, y0 - 5 * dpr, tw + 10 * dpr, T + 24 * dpr);
  const X = Math.round(cx);
  const Y = Math.round(cy);
  use.forEach((q, j) => {
    const base = q.f * n * n;
    for (let yy = 0; yy < m; yy++)
      for (let xx = 0; xx < m; xx++) {
        const gx = X - R + xx;
        const gy = Y - R + yy;
        const o4 = (yy * m + xx) * 4;
        const v = gx >= 0 && gx < n && gy >= 0 && gy < n ? sky[base + gy * n + gx]! : 0;
        if (v === 0) {
          id.data[o4] = 6;
          id.data[o4 + 1] = 12;
          id.data[o4 + 2] = 16;
        } else id.data[o4] = id.data[o4 + 1] = id.data[o4 + 2] = v;
        id.data[o4 + 3] = 255;
      }
    tx.putImageData(id, 0, 0);
    const px = x0 + j * (T + gap);
    c.imageSmoothingEnabled = true;
    c.drawImage(tc, px, y0, T, T);
    c.strokeStyle = 'rgba(255,255,255,.18)';
    c.lineWidth = dpr;
    c.beginPath();
    c.moveTo(px + T / 2, y0);
    c.lineTo(px + T / 2, y0 + 4 * dpr);
    c.moveTo(px + T / 2, y0 + T);
    c.lineTo(px + T / 2, y0 + T - 4 * dpr);
    c.moveTo(px, y0 + T / 2);
    c.lineTo(px + 4 * dpr, y0 + T / 2);
    c.moveTo(px + T, y0 + T / 2);
    c.lineTo(px + T - 4 * dpr, y0 + T / 2);
    c.stroke();
    c.strokeStyle = q.f === k ? '#ffb454' : 'rgba(180,210,220,.35)';
    c.lineWidth = (q.f === k ? 1.8 : 1) * dpr;
    c.strokeRect(px, y0, T, T);
    c.font = `${9.5 * dpr}px ui-monospace, Menlo, monospace`;
    c.fillStyle = q.f === k ? '#ffb454' : '#9fb4bb';
    c.textAlign = 'left';
    c.fillText(`v${q.f + 1}`, px, y0 + T + 11 * dpr);
  });
  c.restore();
}
