"""Difference between passes: a per-pixel significance map, calibrated on empty sky (docs/plan/02-methods/2.3-views.md, 2.3.6)."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy.interpolate import RegularGridInterpolator
from scipy.ndimage import binary_dilation, median_filter

WINDOW_PX = 24
GRID_PX = 8
FLOOR = 0.05  # assumed relative floor for PSF, calibration and wavelength-sampling differences between passes
SOURCE_NSIG = 2.0
SOURCE_BOX = 15


def _window_stat(values: NDArray[np.float64], ok: NDArray[np.bool_], fn: str) -> NDArray[np.float64]:
    """A statistic of the valid pixels in WINDOW_PX windows on a GRID_PX grid, interpolated bilinearly to every pixel."""
    n = values.shape[0]
    centres = np.arange(GRID_PX // 2, n, GRID_PX)
    st = np.full((centres.size, centres.size), np.nan)
    h = WINDOW_PX // 2
    for a, cy in enumerate(centres):
        for b, cx in enumerate(centres):
            sl = (slice(max(0, cy - h), cy + h), slice(max(0, cx - h), cx + h))
            v = values[sl][ok[sl]]
            if v.size >= 20:
                st[a, b] = np.median(v) if fn == "median" else 1.4826 * np.median(np.abs(v - np.median(v)))
    fill = np.nanmedian(st) if np.isfinite(st).any() else (0.0 if fn == "median" else 1.0)
    st = np.where(np.isfinite(st), st, fill)
    f = RegularGridInterpolator((centres, centres), st, bounds_error=False, fill_value=None)
    jj, ii = np.mgrid[0:n, 0:n]
    return np.asarray(f(np.stack([jj.ravel(), ii.ravel()], -1)).reshape(n, n), float)


def sources(img: NDArray[np.float64], ok: NDArray[np.bool_], dilate_px: int) -> NDArray[np.bool_]:
    m = median_filter(np.where(ok, img, 0.0), size=SOURCE_BOX, mode="nearest")
    r = img - m
    w = 1.4826 * np.median(np.abs(r[ok] - np.median(r[ok]))) if ok.any() else 1.0
    return np.asarray(binary_dilation((r > SOURCE_NSIG * w) & ok, iterations=max(1, dilate_px)), bool)


def z_map(
    a: NDArray[np.floating],
    b: NDArray[np.floating],
    sig_a: NDArray[np.floating],
    sig_b: NDArray[np.floating],
    step_arcsec: float,
) -> NDArray[np.float64]:
    """z of pass b against pass a; NaN where either has no value."""
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    ok = np.isfinite(a) & np.isfinite(b) & np.isfinite(sig_a) & np.isfinite(sig_b)
    d = np.where(ok, b - a, 0.0)
    s = _window_stat(d, ok, "median")
    sig = np.sqrt(np.square(np.nan_to_num(sig_a)) + np.square(np.nan_to_num(sig_b)))
    dil = max(1, round(2 * 6.15 / step_arcsec))
    src = sources(np.where(ok, a, 0.0), ok, dil) | sources(np.where(ok, b, 0.0), ok, dil)
    with np.errstate(invalid="ignore", divide="ignore"):
        k = _window_stat(np.where(sig > 0, (d - s) / sig, 0.0), ok & ~src & (sig > 0), "mad")
        z = (d - s) / np.sqrt((k * sig) ** 2 + (FLOOR * np.maximum(np.abs(a), np.abs(b))) ** 2)
    return np.where(ok, z, np.nan)


def stouffer(zs: list[NDArray[np.float64]]) -> NDArray[np.float64]:
    st = np.stack(zs)
    m = np.isfinite(st).sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(m > 0, np.nansum(st, axis=0) / np.sqrt(np.maximum(m, 1)), np.nan)
