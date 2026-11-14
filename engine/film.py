"""The film of the visits of one pass (docs/plan/02-methods/2.3-views.md 2.3.10): bodies that cross the field in days.

One frame per visit: every exposure of the visit (detectors D1–D3, OUTLIER pixels kept: the pipeline sets that flag on asteroids) is put
on the film grid and smoothed to a point source; the frame is the median of the visit's exposures (a cosmic-ray hit is in one exposure
and drops out, a body is in all of them). Frames of one detector are aligned on their median; the static sky of a detector is the median
of its frames; "motion" is (frame − static) / robust scatter, with the places where the static sky shows a source above 5σ (grown by one
grid point) and the halos of saturated stars masked.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import binary_dilation, gaussian_filter, label
from scipy.ndimage import shift as nd_shift

from engine.field import NativeExposure
from engine.grid import Grid, grid_for_fov
from engine.stack import deposit

FILM_DETECTORS = (1, 2, 3)
POINT_SOURCE_FWHM_ARCSEC = 3.2
MIN_EXPOSURES_PER_VISIT = 3
MIN_COVERAGE = 0.5
MASK_NSIG = 5.0
MASK_GROW = 1
HALO_RADIUS_ARCSEC = 150.0
HALO_GROW = 0.4
MAX_ALIGN_PX = 2.0
FILM_MAX_PX = 600  # 60′ at 6″; smaller fields keep the 3.075″ floor
FILM_MIN_STEP_ARCSEC = 3.075


def film_grid(ra: float, dec: float, fov_arcmin: float) -> Grid:
    step = max(FILM_MIN_STEP_ARCSEC, fov_arcmin * 60.0 / FILM_MAX_PX)
    n = int(np.ceil(fov_arcmin * 60.0 / step / 4.0)) * 4
    g = grid_for_fov(ra, dec, fov_arcmin)
    return Grid(g.ra0, g.dec0, step, n)


@dataclass
class Frame:
    visit: int
    det: int
    mjd: float
    n_exposures: int
    sky: NDArray[np.float32]  # median of the visit's exposures (NaN = no data)
    motion: NDArray[np.float32] | None = None  # (frame − static) / scatter, NaN = masked or no data


def visit_frame(exps: Sequence[NativeExposure], grid: Grid) -> tuple[NDArray[np.float32], float]:
    """(median frame, coverage share) of one visit's exposures."""
    n = grid.n
    s = POINT_SOURCE_FWHM_ARCSEC / 2.3548 / grid.step_arcsec
    s = max(s, 0.6)
    layers = []
    for e in exps:
        num, den = deposit(n, e.u, e.v, e.val, np.ones(e.val.size))
        num, den = gaussian_filter(num, s), gaussian_filter(den, s)
        with np.errstate(invalid="ignore", divide="ignore"):
            layers.append(np.where(den > 0.05, num / np.maximum(den, 1e-12), np.nan).astype(np.float32))
    st = np.stack(layers)
    ok = np.isfinite(st)
    cnt = ok.sum(axis=0)
    med = np.full((n, n), np.nan, np.float32)
    good = cnt >= max(1, (len(exps) + 1) // 2)
    if good.any():
        with np.errstate(all="ignore"):
            med[good] = np.nanmedian(st[:, good], axis=0)
    return med, float(good.mean())


def _align(frame: NDArray[np.float32], ref: NDArray[np.float32]) -> tuple[float, float]:
    """Shift (dx, dy) that moves `frame` onto `ref` (cross-correlation of the bright parts, a parabola through the peak, ≤ 2 px)."""
    a = np.nan_to_num(frame - np.nanmedian(frame))
    b = np.nan_to_num(ref - np.nanmedian(ref))
    a = np.clip(a, 0, None)
    b = np.clip(b, 0, None)
    f = np.fft.irfft2(np.fft.rfft2(a) * np.conj(np.fft.rfft2(b)), s=a.shape)
    k = int(np.ceil(MAX_ALIGN_PX))
    win = np.roll(np.roll(f, k, 0), k, 1)[: 2 * k + 1, : 2 * k + 1]
    j, i = np.unravel_index(int(np.argmax(win)), win.shape)

    def parab(m1: float, m0: float, p1: float) -> float:
        d = m1 - 2 * m0 + p1
        return 0.0 if d == 0 else 0.5 * (m1 - p1) / d

    dy = j - k + (parab(win[j - 1, i], win[j, i], win[j + 1, i]) if 0 < j < 2 * k else 0.0)
    dx = i - k + (parab(win[j, i - 1], win[j, i], win[j, i + 1]) if 0 < i < 2 * k else 0.0)
    return -float(dx), -float(dy)


def static_mask(static: NDArray[np.float32], step_arcsec: float) -> NDArray[np.bool_]:
    ok = np.isfinite(static)
    v = static[ok]
    if v.size == 0:
        return ~ok
    med = float(np.median(v))
    sig = float(1.4826 * np.median(np.abs(v - med))) or 1e-9
    src = ok & (static > med + MASK_NSIG * sig)
    mask = binary_dilation(src, iterations=MASK_GROW) if MASK_GROW else src
    # halos of saturated stars: a static blob of equivalent radius ≥ 150″ is grown by 0.4 × its radius
    lab, nb = label(src)
    for k in range(1, nb + 1):
        m = lab == k
        r = np.sqrt(m.sum() / np.pi) * step_arcsec
        if r >= HALO_RADIUS_ARCSEC:
            mask |= binary_dilation(m, iterations=max(1, round(HALO_GROW * r / step_arcsec)))
    return np.asarray(mask | ~ok, bool)


def build_film(exposures: Sequence[NativeExposure], grid: Grid) -> list[Frame]:
    """Frames of the visits of one pass, in time order, with their motion maps."""
    by_visit: dict[int, list[NativeExposure]] = defaultdict(list)
    for e in exposures:
        if e.det in FILM_DETECTORS:
            by_visit[e.visit].append(e)
    if not by_visit:
        return []
    sizes = [len(v) for v in by_visit.values()]
    need = MIN_EXPOSURES_PER_VISIT if np.median(sizes) >= MIN_EXPOSURES_PER_VISIT else 1
    frames: list[Frame] = []
    for vid, exps in by_visit.items():
        if len(exps) < need:
            continue
        sky, cov = visit_frame(exps, grid)
        if cov < MIN_COVERAGE:
            continue
        frames.append(Frame(vid, exps[0].det, float(np.median([e.mjd for e in exps])), len(exps), sky))
    by_det: dict[int, list[Frame]] = defaultdict(list)
    for f in frames:
        by_det[f.det].append(f)
    for fs in by_det.values():
        with np.errstate(all="ignore"):
            ref = np.nanmedian(np.stack([f.sky for f in fs]), axis=0).astype(np.float32)
        for f in fs:
            dx, dy = _align(f.sky, ref)
            if dx or dy:
                ok = np.isfinite(f.sky)
                moved = nd_shift(np.nan_to_num(f.sky), (dy, dx), order=1, mode="constant", cval=0.0)
                okm = nd_shift(ok.astype(float), (dy, dx), order=1, mode="constant", cval=0.0) > 0.5
                f.sky = np.where(okm, moved, np.nan).astype(np.float32)
        with np.errstate(all="ignore"):
            static = np.nanmedian(np.stack([f.sky for f in fs]), axis=0).astype(np.float32)
        mask = static_mask(static, grid.step_arcsec)
        for f in fs:
            d = f.sky - static
            v = d[np.isfinite(d) & ~mask]
            sig = float(1.4826 * np.median(np.abs(v - np.median(v)))) if v.size > 100 else float("nan")
            f.motion = np.where(mask | ~np.isfinite(d), np.nan, d / sig if sig > 0 else np.nan).astype(np.float32)
    frames.sort(key=lambda f: f.mjd)
    return frames
