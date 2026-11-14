"""Wavelength of every pixel from the WCS-WAVE table (docs/plan/02-methods/2.1-data-and-reading.md, 2.1.4).

The table holds the wavelength and bandwidth at a coarse grid of nodes (9×9 in D1, 13×20 in D4, …), about 255 px apart. The linear
variable filter's wavelength is curved between nodes, so a piecewise-bilinear lookup misses by up to 0.02 µm; a bicubic spline through
the same nodes is used instead.

Spectral calibration: v27 files of D5 and D6 carry `spectral_wcs_N_v3.0`, whose node wavelengths are exactly 0.0064 µm above the older
table (v2.2) that QR2 files carry; bandwidths are equal (checked on real files of QR2, QR3 and DR1). A D5/D6 table recognised as the older
one by its first node is brought to v3.0.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from astropy.io import fits
from numpy.typing import ArrayLike, NDArray
from scipy.interpolate import RectBivariateSpline

# detector -> (first node wavelength of the older v2.2 table in µm, offset to v3.0 in µm)
CAL_V3_OFFSET: dict[int, tuple[float, float]] = {5: (4.43616, 0.0064), 6: (5.01821, 0.0064)}
FIRST_NODE_TOLERANCE_UM = 2e-4


@dataclass(frozen=True)
class WaveTable:
    x_nodes: NDArray[np.float64]  # 0-based detector x of the nodes
    y_nodes: NDArray[np.float64]
    wavelength: NDArray[np.float64]  # [iy, ix] µm, as stored (no calibration offset)
    bandwidth: NDArray[np.float64]
    calibration: str | None


def table_from_hdu_bytes(header: fits.Header, data: bytes) -> WaveTable:
    """Parse the WCS-WAVE binary table from its header and data bytes."""
    hl = fits.HDUList([fits.PrimaryHDU(), fits.BinTableHDU.fromstring(header.tostring().encode() + data)])
    d = hl[1].data
    xn = np.asarray(d["X"][0], float).ravel() - 1.0  # FITS 1-based -> 0-based
    yn = np.asarray(d["Y"][0], float).ravel() - 1.0
    v = np.asarray(d["VALUES"][0], float)
    if v.shape != (len(yn), len(xn), 2):
        raise ValueError(f"WCS-WAVE VALUES has shape {v.shape}, expected {(len(yn), len(xn), 2)}")
    cal = None
    for h in header.get("HISTORY", []) or []:
        if "Calibration source" in str(h):
            cal = str(h).split(":")[-1].strip()
    return WaveTable(xn, yn, v[..., 0], v[..., 1], cal)


def calibration_offset(table: WaveTable, detector: int) -> float:
    """µm to add so that the table is on the v3.0 calibration (0 for D1–D4 and for tables already on v3.0)."""
    old = CAL_V3_OFFSET.get(detector)
    if old and abs(float(table.wavelength[0, 0]) - old[0]) < FIRST_NODE_TOLERANCE_UM:
        return old[1]
    return 0.0


class WaveMap:
    """Wavelength and bandwidth at any 0-based detector pixel (outside the node grid: the edge value)."""

    def __init__(self, table: WaveTable, detector: int) -> None:
        self.table = table
        self.offset = calibration_offset(table, detector)
        k = min(3, len(table.x_nodes) - 1, len(table.y_nodes) - 1)
        # RectBivariateSpline takes values indexed [x, y]
        self._lam = RectBivariateSpline(table.x_nodes, table.y_nodes, table.wavelength.T, kx=k, ky=k)
        self._bw = RectBivariateSpline(table.x_nodes, table.y_nodes, table.bandwidth.T, kx=k, ky=k)

    def __call__(self, x: ArrayLike, y: ArrayLike) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        t = self.table
        xx = np.clip(np.asarray(x, float), t.x_nodes[0], t.x_nodes[-1])
        yy = np.clip(np.asarray(y, float), t.y_nodes[0], t.y_nodes[-1])
        lam = self._lam.ev(xx, yy) + self.offset
        bw = self._bw.ev(xx, yy)
        return np.asarray(lam, float), np.asarray(bw, float)
