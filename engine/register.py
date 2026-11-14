"""Registration of passes on pass 0 by field stars (docs/plan/02-methods/2.2-cleaning-and-stacks.md, 2.2.8)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import gaussian_filter, map_coordinates, maximum_filter
from scipy.ndimage import shift as nd_shift

MATCH_ARCSEC = 2.0
MIN_STARS = 3


@dataclass(frozen=True)
class Registration:
    dx: float  # grid pixels to add to this pass's positions (x)
    dy: float
    n_stars: int
    scatter_arcsec: float  # robust scatter of the star offsets after the shift


STAR_FWHM_ARCSEC = 9.0  # stars in the white stacks (validation V10)


def centroid(img: NDArray[np.float64], x0: float, y0: float, s: float, iterations: int = 10) -> tuple[float, float]:
    """Iterative Gaussian-weighted centroid (converges to the centre of a symmetric profile, unlike a box centroid)."""
    r = int(np.ceil(3 * s))
    x, y = x0, y0
    for _ in range(iterations):
        jj, ii = np.mgrid[-r : r + 1, -r : r + 1]
        xs, ys = x + ii, y + jj
        v = map_coordinates(img, [ys.ravel(), xs.ravel()], order=1, mode="nearest").reshape(ii.shape)
        w = np.maximum(v - np.median(v), 0) * np.exp(-(ii**2 + jj**2) / (2 * s * s))
        if w.sum() <= 0:
            break
        nx, ny = (w * xs).sum() / w.sum(), (w * ys).sum() / w.sum()
        if abs(nx - x) < 1e-3 and abs(ny - y) < 1e-3:
            x, y = nx, ny
            break
        x, y = nx, ny
    return float(x), float(y)


def stars(stack: NDArray[np.floating], sat: NDArray[np.floating] | None, step_arcsec: float, nsig: float = 10.0) -> NDArray[np.float64]:
    """(k, 2) sub-pixel centroids (x, y) of isolated unsaturated stars."""
    img = np.nan_to_num(np.asarray(stack, float))
    sm = gaussian_filter(img, 1.0)
    noise = 1.4826 * np.median(np.abs(sm - np.median(sm))) or 1e-9
    s = STAR_FWHM_ARCSEC / step_arcsec / 2.3548
    r = int(np.ceil(3 * s))
    peaks = (sm == maximum_filter(sm, size=2 * r + 1)) & (sm > np.median(sm) + nsig * noise)
    ys, xs = np.nonzero(peaks)
    out = []
    n = img.shape[0]
    for x, y in zip(xs, ys, strict=True):
        if not (r <= x < n - r and r <= y < n - r):
            continue
        if sat is not None and np.asarray(sat)[y - r : y + r + 1, x - r : x + r + 1].max() > 0.05:
            continue
        out.append(centroid(img, float(x), float(y), s))
    return np.asarray(out, float).reshape(-1, 2)


def register(ref: NDArray[np.float64], cur: NDArray[np.float64], step_arcsec: float) -> Registration:
    """The shift that moves `cur` stars onto `ref` stars (median of matched offsets)."""
    if len(ref) < MIN_STARS or len(cur) < MIN_STARS:
        return Registration(0.0, 0.0, 0, float("nan"))
    tol = MATCH_ARCSEC / step_arcsec
    d = ref[None, :, :] - cur[:, None, :]
    dist = np.hypot(d[..., 0], d[..., 1])
    j = dist.argmin(axis=1)
    ok = dist[np.arange(len(cur)), j] < tol
    if ok.sum() < MIN_STARS:
        return Registration(0.0, 0.0, int(ok.sum()), float("nan"))
    off = d[np.arange(len(cur)), j][ok]
    dx, dy = np.median(off, axis=0)
    res = off - [dx, dy]
    scatter = float(1.4826 * np.median(np.hypot(res[:, 0], res[:, 1]))) * step_arcsec
    return Registration(float(dx), float(dy), int(ok.sum()), scatter)


def shift_sums(a: NDArray[np.floating], dx: float, dy: float) -> NDArray[np.float64]:
    """Shift smoothed sums by (dx, dy) grid pixels with a cubic spline (they are band-limited by the smoothing)."""
    return np.asarray(nd_shift(np.asarray(a, float), (dy, dx), order=3, mode="constant", cval=0.0), float)
