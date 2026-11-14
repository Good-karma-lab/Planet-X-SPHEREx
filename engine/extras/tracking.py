"""Shift-and-add of film frames along a motion (synthetic tracking): a body too faint in any one frame adds up along its path.

The input is the film's motion maps: for every frame (frame - static sky) / robust scatter, so a pixel is a noise-normalised residual,
NaN where masked or without data. For a velocity (columns and rows per hour) every frame is shifted back to the reference time and the
frames are added; the sum over n valid frames divided by sqrt(n) has the noise of one frame, and a body of per-frame amplitude A that
moves with that velocity stands out at about A * sqrt(n). The velocity grid is spaced so that a body smears by at most `smear_px` over
the span of the frames; a longer span or a faster limit needs more cells (`max_cells` guards the cost: use a window of a day or less).

Orientation of the film: row 0 north, column 0 east, so east is toward smaller columns and north toward smaller rows.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

MIN_FRAMES = 4
MAX_CELLS = 4000


@dataclass(frozen=True)
class Peak:
    x: float  # column and row at the reference time
    y: float
    v_col: float  # pixels per hour
    v_row: float
    v_east: float  # arcsec per hour
    v_north: float
    snr: float
    n_frames: int  # frames that contribute at the peak
    t_ref_h: float


def _normalise(acc: NDArray[np.float32], cnt: NDArray[np.int16], min_n: int) -> NDArray[np.float32]:
    out = np.full(acc.shape, np.nan, np.float32)
    ok = cnt >= min_n
    out[ok] = acc[ok] / np.sqrt(cnt[ok].astype(np.float32))
    return out


def stack_along(
    z: NDArray[np.floating], t_h: NDArray[np.floating], v_col: float, v_row: float, t_ref_h: float | None = None, min_n: int = MIN_FRAMES
) -> tuple[NDArray[np.float32], NDArray[np.int16]]:
    """(sum / sqrt(n), n) of the frames shifted back to `t_ref_h` along (v_col, v_row) px/h; NaN where fewer than `min_n` frames."""
    f, h, w = z.shape
    tr = float(np.mean(t_h)) if t_ref_h is None else t_ref_h
    valid = np.isfinite(z)
    z0 = np.where(valid, z, 0.0).astype(np.float32)
    acc = np.zeros((h, w), np.float32)
    cnt = np.zeros((h, w), np.int16)
    for k in range(f):
        sx, sy = round(v_col * (t_h[k] - tr)), round(v_row * (t_h[k] - tr))
        y0, y1, x0, x1 = max(0, -sy), min(h, h - sy), max(0, -sx), min(w, w - sx)
        if y1 <= y0 or x1 <= x0:
            continue
        acc[y0:y1, x0:x1] += z0[k, y0 + sy : y1 + sy, x0 + sx : x1 + sx]
        cnt[y0:y1, x0:x1] += valid[k, y0 + sy : y1 + sy, x0 + sx : x1 + sx]
    return _normalise(acc, cnt, min_n), cnt


def _robust_sigma(a: NDArray[np.float32]) -> float:
    v = a[np.isfinite(a)]
    if v.size < 100:
        return 1.0
    return float(max(1.4826 * np.median(np.abs(v - np.median(v))), 1e-6))


def velocity_grid(span_h: float, step_arcsec: float, vmax_arcsec_h: float, smear_px: float = 1.0) -> list[tuple[float, float]]:
    """Velocities (columns, rows per hour) inside the circle of radius vmax, spaced so a body smears by at most `smear_px` over `span_h`."""
    if span_h <= 0:
        return [(0.0, 0.0)]
    dv = smear_px / span_h
    vmax = vmax_arcsec_h / step_arcsec
    side = math.floor(vmax / dv)
    out = [
        (i * dv, j * dv) for i in range(-side, side + 1) for j in range(-side, side + 1) if (i * dv) ** 2 + (j * dv) ** 2 <= vmax**2 + 1e-9
    ]
    return out


def search(
    z: NDArray[np.floating],
    t_h: NDArray[np.floating],
    step_arcsec: float,
    vmax_arcsec_h: float,
    smear_px: float = 1.0,
    min_n: int = MIN_FRAMES,
    top: int = 5,
    max_cells: int = MAX_CELLS,
    edge_px: int = 2,
) -> list[Peak]:
    """The strongest distinct peaks over the velocity grid, strongest first. ValueError when the grid has more than `max_cells` cells."""
    if z.shape[0] < min_n:
        return []
    span = float(np.max(t_h) - np.min(t_h))
    grid = velocity_grid(span, step_arcsec, vmax_arcsec_h, smear_px)
    if len(grid) > max_cells:
        raise ValueError(f"{len(grid)} velocity cells: shorten the window or lower the speed limit (at most {max_cells})")
    tr = float(np.mean(t_h))
    best: list[tuple[float, int, int, float, float, int]] = []
    for vc, vr in grid:
        s, cnt = stack_along(z, t_h, vc, vr, tr, min_n)
        if edge_px:
            s[:edge_px] = np.nan
            s[-edge_px:] = np.nan
            s[:, :edge_px] = np.nan
            s[:, -edge_px:] = np.nan
        if not np.isfinite(s).any():
            continue
        sig = _robust_sigma(s)
        idx = int(np.nanargmax(s))
        y, x = divmod(idx, s.shape[1])
        best.append((float(s[y, x]) / sig, x, y, vc, vr, int(cnt[y, x])))
    best.sort(reverse=True)
    dv = smear_px / span if span > 0 else 1.0
    kept: list[Peak] = []
    for snr, x, y, vc, vr, n in best:
        if any(abs(p.x - x) <= 3 and abs(p.y - y) <= 3 and abs(p.v_col - vc) <= 2 * dv and abs(p.v_row - vr) <= 2 * dv for p in kept):
            continue
        kept.append(Peak(float(x), float(y), vc, vr, -vc * step_arcsec, -vr * step_arcsec, snr, n, tr))
        if len(kept) >= top:
            break
    return kept


def windows(t_h: NDArray[np.floating], window_h: float) -> list[NDArray[np.intp]]:
    """Index sets of frames in sliding windows of `window_h` hours (step: half a window); too-small and repeated windows are dropped."""
    order = np.argsort(t_h)
    ts = np.asarray(t_h)[order]
    out: list[NDArray[np.intp]] = []
    start = float(ts[0])
    while start <= float(ts[-1]):
        idx = order[(ts >= start) & (ts < start + window_h)]
        cur = set(idx.tolist())
        if len(cur) >= MIN_FRAMES:
            last = set(out[-1].tolist()) if out else set()
            if cur <= last:
                pass
            elif last <= cur and out:
                out[-1] = idx
            else:
                out.append(idx)
        start += window_h / 2
    return out
