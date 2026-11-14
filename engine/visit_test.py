"""Streaks and blotches of a single visit (docs/plan/02-methods/2.2-cleaning-and-stacks.md, 2.2.5). Fields of 12′ or less.

Something present in one visit while most visits of the same pass and detector see that sky place empty, covering at least 16 detector
pixels, is dropped from that visit. Source cores are not checked; in a star's wing the difference must exceed twice the wing itself.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import binary_dilation, gaussian_filter, label

from engine.grid import NATIVE_PIXEL_ARCSEC
from engine.stack import Exposure, deposit

MIN_VISITS = 3
REF_PERCENTILE = 30
CORE_NSIG = 5.0
WING_FACTOR = 2.0
AREA_NSIG = 3.0
PEAK_NSIG = 5.0
MIN_AREA_NATIVE_PX = 16
MAX_FOV_ARCMIN = 12.0


def _noise(a: NDArray[np.float64], ok: NDArray[np.bool_]) -> float:
    v = a[ok]
    return float(1.4826 * np.median(np.abs(v - np.median(v)))) if v.size else 1.0


def visit_test(exposures: list[Exposure], visits: list[list[int]], n: int, step_arcsec: float) -> int:
    """Updates the `keep` masks of the exposures of the visits whose regions are dropped; returns the number of pixels dropped."""
    if len(visits) < MIN_VISITS:
        return 0
    px = NATIVE_PIXEL_ARCSEC / step_arcsec
    imgs, covs = [], []
    for members in visits:
        num, den = np.zeros((n, n)), np.zeros((n, n))
        for i in members:
            e = exposures[i]
            a, b = deposit(n, e.u[e.keep], e.v[e.keep], e.val[e.keep], np.ones(int(e.keep.sum())))
            num += a
            den += b
        num, den = gaussian_filter(num, px), gaussian_filter(den, px)
        with np.errstate(invalid="ignore", divide="ignore"):
            imgs.append(np.where(den > 0.05, num / np.maximum(den, 1e-12), np.nan))
        covs.append(den > 0.05)
    stack = np.stack(imgs)
    ref = np.nanpercentile(stack, REF_PERCENTILE, axis=0)
    ref_ok = np.isfinite(ref)
    r = gaussian_filter(np.nan_to_num(ref), px) - np.nanmedian(ref)
    core = binary_dilation(r > CORE_NSIG * _noise(r, ref_ok), iterations=max(1, round(px)))
    min_area = MIN_AREA_NATIVE_PX * px * px
    dropped = 0
    for members, img, cov in zip(visits, imgs, covs, strict=True):
        ok = cov & ref_ok
        if ok.sum() < 100:
            continue
        d = gaussian_filter(np.nan_to_num(img - ref), px)
        d -= np.median(d[ok])
        nd = _noise(d, ok)
        odd = ok & ~core & (np.abs(d) > WING_FACTOR * np.maximum(r, 0))
        regions, k = label(odd & (np.abs(d) > AREA_NSIG * nd))
        drop = np.zeros((n, n), bool)
        for j in range(1, k + 1):
            m = regions == j
            if m.sum() >= min_area and np.abs(d[m]).max() > PEAK_NSIG * nd:
                drop |= m
        if not drop.any():
            continue
        for i in members:
            e = exposures[i]
            ii = np.clip(np.round(e.u).astype(int), 0, n - 1)
            jj = np.clip(np.round(e.v).astype(int), 0, n - 1)
            hit = drop[jj, ii] & e.keep
            dropped += int(hit.sum())
            e.keep = e.keep & ~hit
    return dropped
