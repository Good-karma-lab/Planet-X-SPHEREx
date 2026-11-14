/**
 * Tracks found inside one pass (the visits of a film), their drawing on the film and the cut-outs of a track.
 * Coordinates are the film's grid pixels, the same indexing as its `sky` array: index = frame * n * n + y * n + x, row 0 north.
 */

export interface TrackPoint {
  /** the film frame (visit) the track was found in */
  f: number;
  x: number;
  y: number;
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
  trail: boolean;
  selected: number;
}

/** The found bodies on a frame: the dashed path of every track, a ring where it was seen in this frame, and (Trail) its last three places fading behind it. */
export function drawTracks(c: CanvasRenderingContext2D, tracks: FilmTrack[], d: TrackDraw, dpr = 1): void {
  const X = (x: number) => (x + 0.5) * d.scale;
  const Y = (y: number) => (y + 0.5) * d.scale;
  c.save();
  if (d.rings) {
    for (const tr of tracks) {
      if (!tr.pts.length) continue;
      c.strokeStyle = 'rgba(255,180,84,.85)';
      c.lineWidth = 1.5 * dpr;
      c.setLineDash([4 * dpr, 4 * dpr]);
      c.beginPath();
      tr.pts.forEach((q, i) => (i ? c.lineTo(X(q.x), Y(q.y)) : c.moveTo(X(q.x), Y(q.y))));
      c.stroke();
      c.setLineDash([]);
      const here = tr.pts.find((q) => q.f === d.k);
      if (here) {
        c.strokeStyle = '#ffb454';
        c.lineWidth = 1.8 * dpr;
        c.beginPath();
        c.arc(X(here.x), Y(here.y), 12 * dpr, 0, 7);
        c.stroke();
      }
    }
  }
  if (d.trail) {
    for (const tr of tracks) {
      const pts = tr.pts;
      if (!pts.length) continue;
      const last = pts[pts.length - 1]!.f;
      if (d.k > last || d.k <= pts[0]!.f) continue;
      const past = pts.filter((q) => q.f < d.k).slice(-3);
      if (!past.length) continue;
      const cur = pts.find((q) => q.f === d.k);
      const seq = cur ? past.concat([cur]) : past;
      c.strokeStyle = 'rgba(255,180,84,.35)';
      c.lineWidth = 1 * dpr;
      c.beginPath();
      seq.forEach((q, i) => (i ? c.lineTo(X(q.x), Y(q.y)) : c.moveTo(X(q.x), Y(q.y))));
      c.stroke();
      past.forEach((q, i) => {
        const a = [0.2, 0.38, 0.6][3 - past.length + i]!;
        c.fillStyle = `rgba(255,180,84,${a})`;
        c.beginPath();
        c.arc(X(q.x), Y(q.y), 3 * dpr, 0, 7);
        c.fill();
      });
    }
  }
  c.restore();
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
