"""The position of a source in one exposure (docs/plan/02-methods/2.2-cleaning-and-stacks.md 2.2.11, 2.3-views.md 2.3.5).

A Moffat profile is fitted to the usable (unsaturated) native pixels within 8 detector pixels with a robust loss, in field-grid
coordinates; the pass position is the median over the exposures of the pass. A saturated core has no pixels: the centre is found from
the wings.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import least_squares

from engine.grid import NATIVE_PIXEL_ARCSEC

RADIUS_NATIVE_PX = 8.0
MIN_PIXELS = 12


def moffat_centre(
    u: NDArray[np.floating], v: NDArray[np.floating], val: NDArray[np.floating], x0: float, y0: float, step_arcsec: float
) -> tuple[float, float] | None:
    """(x, y) grid position of the source near (x0, y0), or None when the fit is not possible."""
    scale = NATIVE_PIXEL_ARCSEC / step_arcsec
    r = np.hypot(u - x0, v - y0)
    sel = (r < RADIUS_NATIVE_PX * scale) & np.isfinite(val)
    if sel.sum() < MIN_PIXELS:
        return None
    uu, vv, f = u[sel], v[sel], np.asarray(val, float)[sel]
    b0 = float(np.percentile(f, 10))
    a0 = max(float(f.max() - b0), 1e-6)

    def model(p: NDArray[np.float64]) -> NDArray[np.float64]:
        a, x, y, alpha, beta, b = p
        rr = ((uu - x) ** 2 + (vv - y) ** 2) / (alpha * alpha)
        return np.asarray(a * (1.0 + rr) ** (-beta) + b, float)

    p0 = np.array([a0 * 4, x0, y0, 1.2 * scale, 2.5, b0])
    lo = [0.0, x0 - 4 * scale, y0 - 4 * scale, 0.3 * scale, 1.0, -np.inf]
    hi = [np.inf, x0 + 4 * scale, y0 + 4 * scale, 6 * scale, 8.0, np.inf]
    try:
        res = least_squares(lambda p: (model(p) - f) / max(a0, 1e-6), p0, bounds=(lo, hi), loss="soft_l1", f_scale=0.05, max_nfev=200)
    except (ValueError, np.linalg.LinAlgError):
        return None
    if not res.success:
        return None
    return float(res.x[1]), float(res.x[2])
