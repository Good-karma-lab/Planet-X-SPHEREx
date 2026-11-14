"""The field grid: a north-up gnomonic (tangent-plane) grid centred on the field (docs/plan/02-methods/2.2-cleaning-and-stacks.md).

Orientation (one convention everywhere, docs/plan/05-architecture.md 5.7): row 0 is the NORTH edge, column 0 is the EAST edge as
displayed (east to the left); x grows to the west (right), y grows to the south (down).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

NATIVE_PIXEL_ARCSEC = 6.15
STEP_FINE_ARCSEC = NATIVE_PIXEL_ARCSEC / 4  # 1.5375″ up to 20′
STEP_WIDE_ARCSEC = NATIVE_PIXEL_ARCSEC / 2  # 3.075″ above
FINE_MAX_FOV_ARCMIN = 20.5


@dataclass(frozen=True)
class Grid:
    ra0: float  # field centre, degrees
    dec0: float
    step_arcsec: float
    n: int  # square grid of n × n

    @property
    def fov_arcmin(self) -> float:
        return self.n * self.step_arcsec / 60.0

    @property
    def centre_px(self) -> float:
        return (self.n - 1) / 2.0

    def sky_to_grid(self, ra: ArrayLike, dec: ArrayLike) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        """(RA, Dec) degrees -> (x, y) grid pixels (x west-ward, y south-ward, 0-based centres)."""
        a = np.radians(np.asarray(ra, float))
        d = np.radians(np.asarray(dec, float))
        a0, d0 = math.radians(self.ra0), math.radians(self.dec0)
        cosc = math.sin(d0) * np.sin(d) + math.cos(d0) * np.cos(d) * np.cos(a - a0)
        xi = np.cos(d) * np.sin(a - a0) / cosc  # east positive
        eta = (math.cos(d0) * np.sin(d) - math.sin(d0) * np.cos(d) * np.cos(a - a0)) / cosc  # north positive
        k = 3600.0 * 180.0 / math.pi / self.step_arcsec
        return self.centre_px - xi * k, self.centre_px - eta * k

    def grid_to_sky(self, x: ArrayLike, y: ArrayLike) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        k = math.pi / 180.0 / 3600.0 * self.step_arcsec
        xi = (self.centre_px - np.asarray(x, float)) * k
        eta = (self.centre_px - np.asarray(y, float)) * k
        a0, d0 = math.radians(self.ra0), math.radians(self.dec0)
        den = math.cos(d0) - eta * math.sin(d0)
        a = a0 + np.arctan2(xi, den)
        d = np.arctan2(math.sin(d0) + eta * math.cos(d0), np.hypot(xi, den))
        return np.degrees(a) % 360.0, np.degrees(d)


def grid_for_fov(ra0: float, dec0: float, fov_arcmin: float) -> Grid:
    """The grid rule: 1.5375″ up to 20′ (N up to ~800), 3.075″ above (60′: N ~1170); N a multiple of 4."""
    step = STEP_FINE_ARCSEC if fov_arcmin <= FINE_MAX_FOV_ARCMIN else STEP_WIDE_ARCSEC
    n = math.ceil(fov_arcmin * 60.0 / step / 4.0) * 4
    return Grid(ra0 % 360.0, dec0, step, n)
