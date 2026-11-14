"""Halos of bright stars and saturated zones (docs/plan/02-methods/2.2-cleaning-and-stacks.md, 2.2.6).

Diffraction spikes and rings turn with the roll angle between passes and imitate sources and motion. A source is accepted only where it
stands above the halo light; a saturated zone is one source at its centre, however wide its flat top.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import center_of_mass, gaussian_filter, label, minimum_filter

from engine.grid import NATIVE_PIXEL_ARCSEC

HALO_MIN_FILTER_ARCSEC = 25.0
HALO_SMOOTH_ARCSEC = 12.0
ACCEPT_NSIG = 8.0
ACCEPT_HALO_FACTOR = 3.0
SAT_SHARE = 0.5


def halo_light(stack: NDArray[np.floating], step_arcsec: float) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """(halo, hl): the stack without point sources, and the halo light above its median (≥ 0)."""
    img = np.nan_to_num(np.asarray(stack, float), nan=0.0)
    size = max(3, round(HALO_MIN_FILTER_ARCSEC / step_arcsec) | 1)
    h = gaussian_filter(minimum_filter(img, size=size, mode="nearest"), HALO_SMOOTH_ARCSEC / step_arcsec / 2.3548, mode="nearest")
    return h, np.maximum(h - np.median(h), 0.0)


def accept(sm: NDArray[np.floating], halo: NDArray[np.floating], hl: NDArray[np.floating], noise: float) -> NDArray[np.bool_]:
    excess = np.asarray(sm, float) - halo
    return np.asarray((excess > ACCEPT_NSIG * noise) & (excess > ACCEPT_HALO_FACTOR * hl), bool)


def saturated_zones(sat_share: NDArray[np.floating], step_arcsec: float) -> list[tuple[float, float, int]]:
    """Real saturated zones (at least one detector pixel in area): (x, y, area in grid pixels) of each zone's centre."""
    zones, n = label(np.asarray(sat_share) > SAT_SHARE)
    min_area = (NATIVE_PIXEL_ARCSEC / step_arcsec) ** 2
    out = []
    for k in range(1, n + 1):
        area = int((zones == k).sum())
        if area >= min_area:
            cy, cx = center_of_mass(zones == k)
            out.append((float(cx), float(cy), area))
    return out
