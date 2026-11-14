"""Background and pixel masks of one exposure (docs/plan/02-methods/2.2-cleaning-and-stacks.md, 2.2.1–2.2.2)."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
from numpy.typing import NDArray

from engine.spx import flags as spxflags

CLIP_SIGMA = 3.0
CLIP_ITERATIONS = 3
LAMBDA_BINS = 40  # wavelength-dependent background from 10′ up (the 1.083 µm helium airglow stripe in D1)
SAT_CORE_RADIUS_PX = 4.5
BAD_FAR_RADIUS_PX = 6.0


def clipped_median(values: NDArray[np.floating], valid: NDArray[np.bool_]) -> float:
    v = values[valid & np.isfinite(values)]
    if v.size == 0:
        return 0.0
    med = float(np.median(v))
    for _ in range(CLIP_ITERATIONS):
        sig = 1.4826 * float(np.median(np.abs(v - med)))
        if sig <= 0:
            break
        v = v[np.abs(v - med) < CLIP_SIGMA * sig]
        if v.size == 0:
            break
        med = float(np.median(v))
    return med


def robust_sigma(values: NDArray[np.floating], valid: NDArray[np.bool_]) -> float:
    v = values[valid & np.isfinite(values)]
    if v.size == 0:
        return float("nan")
    return 1.4826 * float(np.median(np.abs(v - np.median(v))))


def subtract_background(
    values: NDArray[np.floating],
    valid: NDArray[np.bool_],
    wavelength: NDArray[np.floating] | None = None,
    lambda_bins: int = 0,
) -> NDArray[np.float32]:
    """Values minus the clipped median; with `lambda_bins` > 0 also minus the median of each wavelength bin (what depends on wavelength
    alone), measured on the valid pixels after the first subtraction."""
    out = np.asarray(values, np.float32) - np.float32(clipped_median(values, valid))
    if lambda_bins > 0 and wavelength is not None:
        lam = np.asarray(wavelength, float)
        ok = valid & np.isfinite(out) & np.isfinite(lam)
        if ok.sum() > 10 * lambda_bins:
            edges = np.quantile(lam[ok], np.linspace(0, 1, lambda_bins + 1))
            which = np.clip(np.searchsorted(edges, lam, side="right") - 1, 0, lambda_bins - 1)
            for b in range(lambda_bins):
                sel = ok & (which == b)
                if sel.sum() >= 10:
                    out[which == b] -= np.float32(clipped_median(out, sel))
    return out


def pixel_mask(
    flags: NDArray[np.integer],
    values: NDArray[np.floating],
    table: Mapping[str, int],
    r_target_px: NDArray[np.floating] | None = None,
    keep_outlier: bool = False,
    r_bright_px: NDArray[np.floating] | None = None,
) -> tuple[NDArray[np.bool_], NDArray[np.bool_]]:
    """(usable, saturated) for the stacks of the fixed sky.

    usable = finite & not saturated & no BAD_ALWAYS flag & no BAD_FAR flag farther than 6 px from a bright core.
    saturated = OVERFLOW, or no value where no bad-pixel flag explains it; within 4.5 px of the target when a target is given, anywhere
    otherwise (a saturated core goes into no stack; it feeds the saturated share instead).
    `r_bright_px`: distance to the nearest saturated pixel of the exposure (fields opened by coordinates: every bright star's core
    counts); `r_target_px`: distance to the named target. BAD_FAR flags are set almost always around a bright core, so they are masked
    only farther than 6 px from it (otherwise the stack has a hole there). `keep_outlier`: searches for moving objects keep OUTLIER.
    """
    f = np.asarray(flags).astype(np.int64)
    bad_always = spxflags.mask_of(table, [n for n in spxflags.BAD_ALWAYS if not (keep_outlier and n in spxflags.MOVER_KEEP)])
    bad_far = spxflags.mask_of(table, spxflags.BAD_FAR)
    overflow = spxflags.mask_of(table, spxflags.SATURATION)
    finite = np.isfinite(values)
    flagged_bad = (f & bad_always) != 0
    # saturated: the overflow flag, or no value where no bad-pixel flag explains it (a dead or missing pixel is not a star)
    sat = ((f & overflow) != 0) | (~finite & ~flagged_bad)
    if r_target_px is not None:
        sat &= r_target_px < SAT_CORE_RADIUS_PX
    near = np.full(f.shape, np.inf)
    if r_target_px is not None:
        near = np.minimum(near, r_target_px)
    if r_bright_px is not None:
        near = np.minimum(near, r_bright_px)
    far = near > BAD_FAR_RADIUS_PX
    usable = finite & ~sat & ~flagged_bad & ~(((f & bad_far) != 0) & far)
    return usable, sat
