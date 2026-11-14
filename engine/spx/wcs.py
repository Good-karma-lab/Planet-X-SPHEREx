"""Sky position of detector pixels: the celestial WCS (TAN with SIP distortion) of the IMAGE header."""

from __future__ import annotations

import re
import warnings

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning
from numpy.typing import ArrayLike, NDArray

# Alternate WCS descriptions (key A: detector pixels; key W: the wavelength -TAB table) are not part of the celestial WCS; their cards
# are dropped before parsing (an incomplete -TAB description makes wcslib fail, and parsing it costs time).
_ALT = re.compile(
    r"^(WCSAXES|WCSNAME|CNAME\d|CRPIX\d|CDELT\d|CUNIT\d|CTYPE\d|CRVAL\d|PV\d_\d+|PS\d_\d+|PC\d_\d|CD\d_\d|LONPOLE|LATPOLE)[A-Z]$"
)


def celestial(image_header: fits.Header) -> WCS:
    """The celestial WCS (TAN-SIP) of a Level-2 IMAGE header; the wavelength is handled by engine.spx.wave."""
    h = image_header.copy()
    for k in [k for k in h if _ALT.match(k)]:
        del h[k]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FITSFixedWarning)
        return WCS(h, naxis=2, relax=True)


def pix2sky(w: WCS, x: ArrayLike, y: ArrayLike) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """0-based pixel coordinates -> (RA, Dec) in degrees, SIP included."""
    ra, dec = w.all_pix2world(np.asarray(x, float), np.asarray(y, float), 0)
    return np.asarray(ra, float), np.asarray(dec, float)


def sky2pix(w: WCS, ra: ArrayLike, dec: ArrayLike) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """(RA, Dec) in degrees -> 0-based pixel coordinates, SIP included."""
    x, y = w.all_world2pix(np.asarray(ra, float), np.asarray(dec, float), 0, quiet=True)
    return np.asarray(x, float), np.asarray(y, float)
