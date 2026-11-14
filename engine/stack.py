"""Super-resolution stacks and the streaming leave-one-out cosmic-ray test (docs/plan/02-methods/2.2-cleaning-and-stacks.md, 2.2.3–2.2.4).

Each native pixel deposits its value bilinearly on the 4 grid points around its position; the sums (numerator and weight) are smoothed
once with a Gaussian. The stack is NUM/DEN. The cosmic-ray test compares each exposure with the stack of the OTHER exposures (the sums
minus its own deposit, smoothed only inside its footprint); a rejected pixel leaves the sums at once (one streaming round, online).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import gaussian_filter, map_coordinates, maximum_filter

from engine.grid import NATIVE_PIXEL_ARCSEC

SIGMA_THICK_NATIVE = 0.35  # stacks with at least K_THICK exposures
SIGMA_THIN_NATIVE = 0.50  # thinner stacks (a weight lattice appears at 0.35 below ~15 exposures)
K_THICK = 16

# leave-one-out test
HIT_NSIG = 8.0
HIT_FACTOR = 5.0
PROTECT_NSIG = 5.0  # a pixel where the others see a source above 5σ of this exposure's noise ...
PROTECT_FACTOR = 20.0  # ... and that is below 20 × the others' peak is never rejected


def sigma_native(n_exposures: int) -> float:
    return SIGMA_THICK_NATIVE if n_exposures >= K_THICK else SIGMA_THIN_NATIVE


def sigma_grid(n_exposures: int, step_arcsec: float) -> float:
    return sigma_native(n_exposures) * NATIVE_PIXEL_ARCSEC / step_arcsec


def deposit(
    n: int, u: NDArray[np.floating], v: NDArray[np.floating], val: NDArray[np.floating], w: NDArray[np.floating]
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Bilinear deposit of values (weights w) at grid positions (u = column, v = row) into n × n sums (NUM, DEN), unsmoothed."""
    num = np.zeros(n * n)
    den = np.zeros(n * n)
    u = np.asarray(u, float)
    v = np.asarray(v, float)
    i0 = np.floor(u).astype(np.int64)
    j0 = np.floor(v).astype(np.int64)
    fu, fv = u - i0, v - j0
    vw = np.asarray(val, float) * w
    for di, dj, k in ((0, 0, (1 - fu) * (1 - fv)), (1, 0, fu * (1 - fv)), (0, 1, (1 - fu) * fv), (1, 1, fu * fv)):
        ii, jj = i0 + di, j0 + dj
        ok = (ii >= 0) & (ii < n) & (jj >= 0) & (jj < n)
        idx = jj[ok] * n + ii[ok]
        num += np.bincount(idx, weights=(vw * k)[ok], minlength=n * n)
        den += np.bincount(idx, weights=(w * k)[ok], minlength=n * n)
    return num.reshape(n, n), den.reshape(n, n)


def smooth(a: NDArray[np.floating], sigma: float) -> NDArray[np.float64]:
    return np.asarray(gaussian_filter(np.asarray(a, float), sigma, mode="constant", truncate=3.0), float)


def sample(a: NDArray[np.floating], u: NDArray[np.floating], v: NDArray[np.floating]) -> NDArray[np.float64]:
    return np.asarray(map_coordinates(np.asarray(a, float), [v, u], order=1, mode="constant", cval=0.0), float)


@dataclass
class Exposure:
    """One exposure inside a field: its usable native pixels, already background-subtracted, at grid positions."""

    u: NDArray[np.float64]
    v: NDArray[np.float64]
    val: NDArray[np.float32]
    weight: float = 1.0
    keep: NDArray[np.bool_] = field(default_factory=lambda: np.zeros(0, bool))  # set by the test (all True before)
    sigma: float = float("nan")  # robust single-pixel noise of the exposure

    def __post_init__(self) -> None:
        if self.keep.size != self.val.size:
            self.keep = np.ones(self.val.size, bool)
        if not np.isfinite(self.sigma):
            v = self.val[np.isfinite(self.val)]
            self.sigma = float(1.4826 * np.median(np.abs(v - np.median(v)))) if v.size else 1.0


@dataclass
class StackResult:
    stack: NDArray[np.float64]
    weight: NDArray[np.float64]
    rejected: int
    rejected_per_exposure: list[int]


def _box(e: Exposure, n: int, pad: int) -> tuple[int, int, int, int]:
    i0 = max(0, int(np.floor(e.u.min())) - pad)
    i1 = min(n, int(np.ceil(e.u.max())) + pad + 2)
    j0 = max(0, int(np.floor(e.v.min())) - pad)
    j1 = min(n, int(np.ceil(e.v.max())) + pad + 2)
    return i0, i1, j0, j1


def stack_with_rejection(exposures: list[Exposure], n: int, step_arcsec: float, test: bool = True) -> StackResult:
    """Stack of one (pass, detector) with the online leave-one-out test.

    Smoothing is linear, so the smoothed sums S_NUM, S_DEN are kept, and an exposure's own smoothed deposit is subtracted from them in
    its bounding box only. A rejected pixel's smoothed deposit leaves S_NUM, S_DEN at once, so the exposures tested after it see the
    stack without it.
    """
    sig = sigma_grid(len(exposures), step_arcsec)
    pad = int(np.ceil(3 * sig)) + 2
    peak_size = max(3, round(NATIVE_PIXEL_ARCSEC / step_arcsec) | 1)
    raw_num, raw_den = np.zeros((n, n)), np.zeros((n, n))
    for e in exposures:
        a, b = deposit(n, e.u, e.v, e.val, np.full(e.val.size, e.weight))
        raw_num += a
        raw_den += b
    s_num, s_den = smooth(raw_num, sig), smooth(raw_den, sig)
    per_exp: list[int] = []
    if test:
        for e in exposures:
            i0, i1, j0, j1 = _box(e, n, pad)
            m = i1 - i0
            h = j1 - j0
            size = max(m, h)
            lu, lv = e.u - i0, e.v - j0
            a, b = deposit(size, lu, lv, e.val * e.keep, np.where(e.keep, e.weight, 0.0))
            a_s, b_s = smooth(a, sig)[:h, :m], smooth(b, sig)[:h, :m]
            on, od = s_num[j0:j1, i0:i1] - a_s, s_den[j0:j1, i0:i1] - b_s
            with np.errstate(invalid="ignore", divide="ignore"):
                others = np.where(od > 1e-6, on / np.maximum(od, 1e-12), np.nan)
            peak = maximum_filter(np.nan_to_num(others, nan=0.0), size=peak_size, mode="nearest")
            o = sample(np.nan_to_num(others, nan=0.0), lu, lv)
            covered = sample((od > 1e-6).astype(float), lu, lv) > 0.5
            pk = sample(peak, lu, lv)
            val = e.val.astype(float)
            s = e.sigma
            hit = (val - np.maximum(o, 0) > HIT_NSIG * s) & (val > HIT_FACTOR * np.maximum(pk, 0))
            hit |= o - val > HIT_NSIG * s + HIT_FACTOR * np.abs(o)
            protect = (o > PROTECT_NSIG * s) & (val < PROTECT_FACTOR * pk)
            reject = hit & ~protect & covered & e.keep
            per_exp.append(int(reject.sum()))
            if reject.any():
                ra, rb = deposit(size, lu[reject], lv[reject], e.val[reject], np.full(int(reject.sum()), e.weight))
                s_num[j0:j1, i0:i1] -= smooth(ra, sig)[:h, :m]
                s_den[j0:j1, i0:i1] -= smooth(rb, sig)[:h, :m]
                e.keep = e.keep & ~reject
    with np.errstate(invalid="ignore", divide="ignore"):
        st = np.where(s_den > 1e-6, s_num / np.maximum(s_den, 1e-12), np.nan)
    return StackResult(st, s_den, int(sum(per_exp)), per_exp)


def resample_then_average(exposures: list[Exposure], n: int) -> NDArray[np.float64]:
    """The naive alternative (for tests only): each exposure interpolated to the grid (linearly), then averaged."""
    from scipy.interpolate import griddata

    acc = np.zeros((n, n))
    cnt = np.zeros((n, n))
    gj, gi = np.mgrid[0:n, 0:n]
    for e in exposures:
        r = np.nan_to_num(griddata((e.u, e.v), e.val.astype(float), (gi, gj), method="linear"))
        acc += r
        cnt += 1
    return acc / np.maximum(cnt, 1)
