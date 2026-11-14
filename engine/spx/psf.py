"""The PSF of a detector zone (docs/plan/02-methods/2.1-data-and-reading.md, 2.1.5).

QR2: a `PSF` image cube (n_zones × 101 × 101), the optical PSF oversampled ×OVERSAMP (10); zone centres in XCTR_i / YCTR_i. It must be
integrated over the detector pixel before fitting (taken as is, fluxes come out 8 % low).
v27 (QR3, DR1): an `EPSF` table of 441 zones (21 × 21), each 33 × 33 ×OVSMPX (5), already pixel-integrated; zone centres in XCENTER /
YCENTER.
The PSF data of two files of one detector and release are byte-identical in every case checked, so a PSF set is shared by checksum.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np
from astropy.io import fits
from numpy.typing import NDArray
from scipy.ndimage import convolve1d, map_coordinates


@dataclass(frozen=True)
class PsfSet:
    kind: str  # "cube" (QR2, integrated here) or "epsf" (v27)
    oversampling: int
    zones: NDArray[np.float64]  # (n, ny, nx) pixel-integrated, oversampled
    x_centres: NDArray[np.float64]  # 0-based detector coordinates of the zone centres
    y_centres: NDArray[np.float64]
    checksum: str

    def zone_index(self, x: float, y: float) -> int:
        return int(np.argmin((self.x_centres - x) ** 2 + (self.y_centres - y) ** 2))


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pixel_integrate(cube: NDArray[np.float64], osf: int) -> NDArray[np.float64]:
    """Convolve each oversampled zone with a box one detector pixel wide (trapezoid weights at the ends)."""
    k = np.ones(osf + 1)
    k[0] = k[-1] = 0.5
    k /= k.sum()
    return np.asarray(convolve1d(convolve1d(cube, k, axis=-1, mode="constant"), k, axis=-2, mode="constant"), float)


def from_cube(header: fits.Header, data: bytes) -> PsfSet:
    n, ny, nx = int(header["NAXIS3"]), int(header["NAXIS2"]), int(header["NAXIS1"])
    cube = np.frombuffer(data, dtype=">f4", count=n * ny * nx).reshape(n, ny, nx).astype(np.float64)
    osf = int(header["OVERSAMP"])
    xc = np.array([float(header[f"XCTR_{i + 1}"]) for i in range(n)]) - 1.0
    yc = np.array([float(header[f"YCTR_{i + 1}"]) for i in range(n)]) - 1.0
    return PsfSet("cube", osf, pixel_integrate(cube, osf), xc, yc, checksum(data))


def from_epsf(header: fits.Header, data: bytes) -> PsfSet:
    hl = fits.HDUList([fits.PrimaryHDU(), fits.BinTableHDU.fromstring(header.tostring().encode() + data)])
    d = hl[1].data
    zones = np.asarray(d["EPSF"], float).reshape(len(d), 33, 33)
    return PsfSet(
        "epsf",
        int(header["OVSMPX"]),
        zones,
        np.asarray(d["XCENTER"], float) - 1.0,
        np.asarray(d["YCENTER"], float) - 1.0,
        checksum(data),
    )


def stamp(psf: PsfSet, zone: int, fx: float, fy: float, half: int = 3) -> NDArray[np.float64]:
    """The PSF sampled at the pixel centres of a (2·half+1)² stamp whose central pixel is offset (fx, fy) px from the source."""
    p = psf.zones[zone]
    osf = psf.oversampling
    c = (p.shape[0] - 1) / 2.0
    j, i = np.mgrid[-half : half + 1, -half : half + 1]
    s = map_coordinates(p, [c + osf * (j - fy), c + osf * (i - fx)], order=3, mode="constant", cval=0.0)
    return np.asarray(s, float) * osf**2
