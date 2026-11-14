"""The quick-look spectrum from the all-sky cube: the HEALPix tile of a place and the wavelength table."""

from __future__ import annotations

import io

import numpy as np
from astropy.io import fits

from service.quicklook import _wavelengths, ang2pix_nest


def test_ang2pix_nest_known_values() -> None:
    # values of the nested scheme (healpy.ang2pix(2**order, ra, dec, nest=True, lonlat=True))
    assert ang2pix_nest(0, 0.0, 0.0) == 4
    assert ang2pix_nest(0, 45.0, 60.0) == 0
    assert ang2pix_nest(0, 45.0, -60.0) == 8
    # the tile of the cube that holds Barnard's Star (its header says NPIX = 7945)
    assert ang2pix_nest(5, 269.44629, 4.76792) == 7945


def test_wavelength_column_from_row_layout() -> None:
    cols = [
        fits.Column(name="DETECTOR", format="I", array=np.array([1, 1, 2], dtype=np.int16)),
        fits.Column(name="SUBCHAN", format="I", array=np.array([1, 2, 1], dtype=np.int16)),
        fits.Column(name="WAVELENGTH", format="E", array=np.array([0.753, 0.771, 1.11], dtype=np.float32)),
    ]
    hdu = fits.BinTableHDU.from_columns(cols, name="SPECTRAL_CHANNELS")
    buf = io.BytesIO()
    fits.HDUList([fits.PrimaryHDU(), hdu]).writeto(buf)
    b = buf.getvalue()
    start = 2880 + len(hdu.header.tostring())  # the table's data as stored in a file (big-endian rows)
    lam = _wavelengths(hdu.header, b[start : start + hdu.header["NAXIS1"] * hdu.header["NAXIS2"]], 3)
    assert np.allclose(lam, [0.753, 0.771, 1.11], atol=1e-6)
