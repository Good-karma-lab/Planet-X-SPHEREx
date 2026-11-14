"""Spectral colour (docs/plan/02-methods/2.3-views.md, 2.3.2): every wavelength gets its rainbow colour, 0.75 µm violet → 5 µm red."""

from __future__ import annotations

import colorsys

import numpy as np
from numpy.typing import NDArray

DETECTOR_RANGES_UM = {1: (0.75, 1.11), 2: (1.11, 1.64), 3: (1.64, 2.42), 4: (2.42, 3.82), 5: (3.82, 4.42), 6: (4.42, 5.00)}
STAR_THRESHOLD = 2.5  # MJy/sr: pixels of field stars for the white balance
STAR_TARGET_EXCLUSION_ARCSEC = 50.0


def lam_rgb(lam_um: float) -> tuple[float, float, float]:
    f_log = (np.log(lam_um) - np.log(0.74)) / (np.log(5.02) - np.log(0.74))
    f_lin = (lam_um - 0.74) / (5.02 - 0.74)
    h = 0.74 - 0.78 * (0.35 * f_log + 0.65 * f_lin)
    return colorsys.hsv_to_rgb(float(h % 1.0), 0.88, 1.0)


def colour_matrix(detectors: tuple[int, ...] = (1, 2, 3, 4, 5, 6), samples: int = 32) -> NDArray[np.float64]:
    """3 × 6 matrix: column d is the mean rainbow colour over detector d's range; detectors not listed get 0. Rows sum to 1 over the
    listed detectors so that a flat spectrum is grey."""
    m = np.zeros((3, 6))
    for d in detectors:
        lo, hi = DETECTOR_RANGES_UM[d]
        m[:, d - 1] = np.mean([lam_rgb(x) for x in np.linspace(lo, hi, samples)], axis=0)
    s = m.sum(axis=1, keepdims=True)
    return m / np.where(s > 0, s, 1.0)


def white_balance(
    r: NDArray[np.floating], g: NDArray[np.floating], b: NDArray[np.floating], star: NDArray[np.bool_]
) -> tuple[float, float]:
    """(kR, kB) so that a typical field star is grey: kR = median(G/R), kB = median(G/B) over star pixels; (1, 1) when there are none."""
    ok = star & (r > 0) & (g > 0) & (b > 0)
    if ok.sum() < 5:
        return 1.0, 1.0
    return float(np.median(g[ok] / r[ok])), float(np.median(g[ok] / b[ok]))
