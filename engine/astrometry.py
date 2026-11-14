"""Where a star is at a date: catalogue place moved by proper motion and parallax (Earth's barycentric position from ERFA via astropy).

Δα·cos δ = ϖ (X sin α − Y cos α),  Δδ = ϖ (X cos α sin δ + Y sin α sin δ − Z cos δ)   (X, Y, Z: Earth's barycentric position, au),
added to the place moved by proper motion. SPHEREx orbits the Earth at ~7000 km; that offset is below 0.01″ for any star.
"""

from __future__ import annotations

import math

import numpy as np
from astropy.coordinates import get_body_barycentric
from astropy.time import Time
from numpy.typing import ArrayLike, NDArray


def place(
    ra_deg: float, dec_deg: float, epoch: float, pmra_mas: float, pmdec_mas: float, plx_mas: float, mjd: ArrayLike
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    t = Time(np.atleast_1d(np.asarray(mjd, float)), format="mjd")
    dt = t.jyear - epoch
    dec = dec_deg + pmdec_mas * dt / 3.6e6
    ra = ra_deg + pmra_mas * dt / 3.6e6 / np.cos(np.radians(dec))
    e = get_body_barycentric("earth", t).xyz.to("au").value  # (3, n)
    a, d = np.radians(ra), np.radians(dec)
    p = plx_mas / 3.6e6  # degrees
    dra = p * (e[0] * np.sin(a) - e[1] * np.cos(a)) / np.cos(d)
    ddec = p * (e[0] * np.cos(a) * np.sin(d) + e[1] * np.sin(a) * np.sin(d) - e[2] * np.cos(d))
    return np.asarray((ra + dra) % 360.0, float), np.asarray(dec + ddec, float)


def offset_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> tuple[float, float]:
    """(ΔRA·cos δ, ΔDec) of point 1 relative to point 2, arcseconds."""
    dra = ((ra1 - ra2 + 180.0) % 360.0) - 180.0
    return dra * math.cos(math.radians(dec2)) * 3600.0, (dec1 - dec2) * 3600.0
