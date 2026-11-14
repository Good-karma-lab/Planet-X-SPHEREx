"""PSF photometry of one exposure (docs/plan/02-methods/2.4-spectra.md 2.4.1).

A 7 × 7 stamp around the object's position; model flux·PSF + sky fitted by weighted least squares (weights 1/VARIANCE); up to three
rounds rejecting pixels beyond 6σ (the allowance includes 10 % of the model for PSF mismatch); skipped when a core pixel (within 1.6 px)
is saturated or has no value, or when fewer than 20 pixels are usable; dropped when the PSF stands on less than 80 % good pixels.
Flux = amplitude × the pixel's solid angle; the spectrum later drops points beyond 5 robust σ of their 7 neighbours in wavelength.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

HALF = 3
CORE_PX = 1.6
MIN_PIXELS = 20
MIN_PSF_GOOD = 0.8
REJECT_NSIG = 6.0
PSF_MISMATCH = 0.1
ROUNDS = 3
NEIGHBOURS = 7
NEIGHBOUR_NSIG = 5.0


@dataclass(frozen=True)
class Point:
    flux_mjy: float | None
    err_mjy: float | None
    ok: bool
    why: str  # "" | "saturated core" | "no usable pixels" | "little of the PSF on good pixels"
    chi2: float | None = None
    amp: float | None = None  # the fitted amplitude (MJy/sr at the PSF's peak normalisation) and the constant sky of the window
    sky: float | None = None
    used: NDArray[np.bool_] | None = None  # the pixels the final fit used


def fit(
    stamp: NDArray[np.floating],
    var: NDArray[np.floating],
    usable: NDArray[np.bool_],
    saturated: NDArray[np.bool_],
    psf: NDArray[np.floating],
    fx: float,
    fy: float,
    omega_sr: float,
) -> Point:
    """Fit one stamp. `fx, fy`: offset of the object from the central pixel (px); `psf`: the PSF sampled on the stamp's pixels."""
    h = stamp.shape[0] // 2
    jj, ii = np.mgrid[-h : h + 1, -h : h + 1]
    core = np.hypot(ii - fx, jj - fy) < CORE_PX
    if (saturated & core).any():
        return Point(None, None, False, "saturated core")
    ok = usable & np.isfinite(stamp) & np.isfinite(var) & (var > 0)
    if ok.sum() < MIN_PIXELS:
        return Point(None, None, False, "no usable pixels")
    if psf[ok].sum() < MIN_PSF_GOOD * psf.sum():
        return Point(None, None, False, "little of the PSF on good pixels")
    a = b = 0.0
    err = float("nan")
    w = np.zeros_like(stamp, dtype=float)
    for _ in range(ROUNDS + 3):
        w = np.where(ok, 1.0 / np.maximum(var, 1e-12), 0.0)
        s11, s12, s22 = (w * psf * psf).sum(), (w * psf).sum(), w.sum()
        y = np.where(ok, stamp, 0.0)
        b1, b2 = (w * psf * y).sum(), (w * y).sum()
        det = s11 * s22 - s12 * s12
        if det <= 0:
            return Point(None, None, False, "no usable pixels")
        a = (b1 * s22 - b2 * s12) / det
        b = (s11 * b2 - s12 * b1) / det
        err = float(np.sqrt(s22 / det))
        resid = np.abs(stamp - a * psf - b)
        limit = REJECT_NSIG * np.sqrt(var + (PSF_MISMATCH * a * psf) ** 2)
        new = ok & (resid < limit)
        if new.sum() == ok.sum():
            break
        if new.sum() < MIN_PIXELS:
            # one strong outlier (a hit) biases the first fit so that every pixel looks deviant: reject only the worst one and refit
            score = np.where(ok, resid / limit, -np.inf)
            worst = np.unravel_index(int(np.argmax(score)), score.shape)
            if score[worst] <= 1.0 or ok.sum() - 1 < MIN_PIXELS:
                break
            new = ok.copy()
            new[worst] = False
        ok = new
    chi2 = float((w * (stamp - a * psf - b) ** 2)[ok].sum()) / max(1, int(ok.sum()) - 2)
    k = omega_sr * 1e9  # MJy/sr × sr -> mJy
    return Point(float(a * k), float(err * k), True, "", chi2, float(a), float(b), ok.copy())


def neighbour_outliers(lam: NDArray[np.floating], flux: NDArray[np.floating]) -> NDArray[np.bool_]:
    """Points more than 5 robust σ from the median of their 7 neighbours in wavelength."""
    n = lam.size
    bad = np.zeros(n, bool)
    if n < NEIGHBOURS + 1:
        return bad
    order = np.argsort(lam)
    f = flux[order]
    resid = np.zeros(n)
    half = NEIGHBOURS // 2
    for k in range(n):
        lo = max(0, min(k - half, n - NEIGHBOURS - 1))
        nb = np.concatenate([f[lo:k], f[k + 1 : lo + NEIGHBOURS + 1]])
        resid[k] = f[k] - np.median(nb)
    sig = 1.4826 * np.median(np.abs(resid - np.median(resid))) or 1e-12
    bad[order] = np.abs(resid) > NEIGHBOUR_NSIG * sig
    return bad
