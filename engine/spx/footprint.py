"""The sky footprint of one exposure: 12 vertices (4 corners and 2 points on each edge) through the SIP WCS.

Used by the exposure index (docs/plan/02-methods/2.1-data-and-reading.md, 2.1.6) to find the exposures whose footprint overlaps a field.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from astropy.io import fits
from numpy.typing import NDArray

from engine.spx.wcs import celestial, pix2sky


@dataclass(frozen=True)
class Footprint:
    ra: NDArray[np.float64]  # 12 vertices, counter-clockwise on the sky as the detector edges run
    dec: NDArray[np.float64]
    centre: tuple[float, float]  # RA, Dec of the detector centre
    radius_deg: float  # smallest circle about the centre that holds every vertex

    def unit_vectors(self) -> NDArray[np.float64]:
        return radec_to_xyz(self.ra, self.dec)


def radec_to_xyz(ra: NDArray[np.float64] | float, dec: NDArray[np.float64] | float) -> NDArray[np.float64]:
    r, d = np.radians(ra), np.radians(dec)
    return np.stack([np.cos(d) * np.cos(r), np.cos(d) * np.sin(r), np.sin(d)], axis=-1)


def edge_points(nx: int, ny: int) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """12 points on the outer edge of the detector (0-based pixel centres, half a pixel outward), in order around it."""
    x0, x1, y0, y1 = -0.5, nx - 0.5, -0.5, ny - 0.5
    t = (1 / 3, 2 / 3)
    xs = [x0, x0 + (x1 - x0) * t[0], x0 + (x1 - x0) * t[1], x1, x1, x1, x1, x0 + (x1 - x0) * t[1], x0 + (x1 - x0) * t[0], x0, x0, x0]
    ys = [y0, y0, y0, y0, y0 + (y1 - y0) * t[0], y0 + (y1 - y0) * t[1], y1, y1, y1, y1, y0 + (y1 - y0) * t[1], y0 + (y1 - y0) * t[0]]
    return np.array(xs), np.array(ys)


def footprint(image_header: fits.Header) -> Footprint:
    nx, ny = int(image_header["NAXIS1"]), int(image_header["NAXIS2"])
    w = celestial(image_header)
    x, y = edge_points(nx, ny)
    ra, dec = pix2sky(w, x, y)
    cra, cdec = pix2sky(w, [(nx - 1) / 2], [(ny - 1) / 2])
    c = radec_to_xyz(cra[0], cdec[0])
    cosr = float(np.min(radec_to_xyz(ra, dec) @ c))
    return Footprint(ra % 360.0, dec, (float(cra[0] % 360.0), float(cdec[0])), float(np.degrees(np.arccos(np.clip(cosr, -1, 1)))))


def contains(fp: Footprint, ra: float, dec: float) -> bool:
    """Whether a sky point lies inside the footprint (a convex polygon on the sphere; the detector is convex)."""
    v = fp.unit_vectors()
    p = radec_to_xyz(ra, dec)
    n = np.cross(v, np.roll(v, -1, axis=0))  # normals of the great-circle edges
    s = n @ p
    return bool(np.all(s >= 0) or np.all(s <= 0))


def overlaps_circle(fp: Footprint, ra: float, dec: float, radius_deg: float) -> bool:
    """Whether the footprint and a circle on the sky overlap (the field of view is treated as its circumscribed circle)."""
    c = radec_to_xyz(*fp.centre)
    p = radec_to_xyz(ra, dec)
    sep = float(np.degrees(np.arccos(np.clip(c @ p, -1, 1))))
    if sep > fp.radius_deg + radius_deg:
        return False
    if contains(fp, ra, dec):
        return True
    v = fp.unit_vectors()
    if float(np.degrees(np.arccos(np.clip((v @ p).max(), -1, 1)))) <= radius_deg:
        return True
    # distance from the circle centre to each edge (great-circle arc)
    a, b = v, np.roll(v, -1, axis=0)
    n = np.cross(a, b)
    n /= np.linalg.norm(n, axis=1, keepdims=True)
    d = np.degrees(np.arcsin(np.clip(np.abs(n @ p), 0, 1)))
    proj = p - (n @ p)[:, None] * n
    inside_arc = (np.einsum("ij,ij->i", np.cross(a, proj), n) >= 0) & (np.einsum("ij,ij->i", np.cross(proj, b), n) >= 0)
    return bool(np.any(inside_arc & (d <= radius_deg)))
