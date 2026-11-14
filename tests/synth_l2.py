"""Synthetic SPHEREx Level-2 files for tests, in both layouts (QR2: plain FLAGS and a PSF cube; v27: RICE FLAGS and an EPSF table).

Small (default 256 × 256) but with the real structure: extension names and order, header cards the readers use, WCS with SIP, the
WCS-WAVE table, the flag-bit tables of each release.
"""

from __future__ import annotations

import io

import numpy as np
from astropy.io import fits

from engine.spx.flags import BUILTIN

QR2_BITS = {k: v for k, v in BUILTIN.items() if v <= 21 and k not in ("CROSSTALK",)}
V27_BITS = {k: v for k, v in BUILTIN.items() if k not in ("PHANTOM", "REFERENCE")}


def _wcs_cards(h: fits.Header, n: int, ra: float, dec: float) -> None:
    pix = 6.15 / 3600.0
    h.update(
        {
            "CTYPE1": "RA---TAN-SIP",
            "CTYPE2": "DEC--TAN-SIP",
            "CRPIX1": (n + 1) / 2,
            "CRPIX2": (n + 1) / 2,
            "CRVAL1": ra,
            "CRVAL2": dec,
            "CD1_1": -pix,
            "CD1_2": 0.0,
            "CD2_1": 0.0,
            "CD2_2": pix,
            "A_ORDER": 2,
            "B_ORDER": 2,
            "A_2_0": 1e-7,
            "B_0_2": -1e-7,
            "CRPIX1A": 1.0,
            "CRPIX2A": 1.0,
        }
    )


def gaussian_psf(n: int, osf: int, sigma_px: float) -> np.ndarray:
    c = (n - 1) / 2
    j, i = np.mgrid[0:n, 0:n]
    g = np.exp(-(((i - c) / osf) ** 2 + ((j - c) / osf) ** 2) / (2 * sigma_px**2))
    return g / g.sum()


def wave_table(n: int, detector: int, first: float | None = None) -> fits.BinTableHDU:
    nodes = np.linspace(1, n, 9).round().astype(np.int32)
    yy, xx = np.meshgrid(nodes, nodes, indexing="ij")
    lam = 1.0 + 0.4 * (xx - 1) / (n - 1) + 0.05 * ((yy - 1) / (n - 1)) ** 2
    if first is not None:
        lam = lam - lam[0, 0] + first
    vals = np.stack([lam, np.full_like(lam, 0.02)], axis=-1).astype(np.float32)  # [y, x, q]
    cols = [
        fits.Column(name="X", format="9J", dim="(9)", array=nodes[None, :]),
        fits.Column(name="Y", format="9J", dim="(9)", array=nodes[None, :]),
        fits.Column(name="VALUES", format="162E", dim="(2,9,9)", array=vals[None]),
    ]
    t = fits.BinTableHDU.from_columns(cols, name="WCS-WAVE")
    t.header["DETECTOR"] = str(detector)
    t.header.add_history(f"Calibration source file: spectral_wcs_{detector}_test.fits")
    return t


def make_file(
    layout: str = "v27",
    n: int = 256,
    detector: int = 1,
    ra: float = 269.45,
    dec: float = 4.69,
    mjd: float = 61000.25,
    seed: int = 0,
    wave_first: float | None = None,
) -> tuple[bytes, dict[str, np.ndarray]]:
    """(file bytes, the planes as written) for layout "qr2" or "v27"."""
    rng = np.random.default_rng(seed)
    image = rng.normal(0.0, 0.05, (n, n)).astype(np.float32)
    image[n // 2, n // 2] = np.nan  # a pixel with no value
    flags = np.zeros((n, n), np.int32)
    bits = QR2_BITS if layout == "qr2" else V27_BITS
    flags[rng.random((n, n)) < 0.01] |= 1 << bits["OUTLIER"]
    flags[n // 2 - 1 : n // 2 + 2, n // 2 - 1 : n // 2 + 2] |= 1 << bits["OVERFLOW"]
    variance = np.full((n, n), 0.0025, np.float32)
    zodi = np.full((n, n), 0.3, np.float32)

    hi = fits.Header()
    _wcs_cards(hi, n, ra, dec)
    hi.update({"BUNIT": "MJy / sr", "DETECTOR": detector, "MJD-AVG": mjd, "X_SC": 1939.7, "Y_SC": -6500.1, "Z_SC": 1200.3})
    hi["CTYPE1W"] = "WAVE-TAB"  # an incomplete alternate WCS: the celestial WCS must ignore it
    hdus: list[fits.hdu.base.ExtensionHDU] = [fits.ImageHDU(image, hi, name="IMAGE")]

    hf = fits.Header()
    if layout == "qr2":
        for k, b in bits.items():
            hf[f"HIERARCH MP_{k}"] = b
        hdus.append(fits.ImageHDU(flags, hf, name="FLAGS"))
    else:
        for i, (k, b) in enumerate(bits.items()):
            hf[f"MSKN{i:04d}"] = k
            hf[f"MSKM{i:04d}"] = 1 << b
        hdus.append(fits.CompImageHDU(flags, hf, name="FLAGS", compression_type="RICE_1", tile_shape=(1, n)))
    hdus.append(fits.ImageHDU(variance, name="VARIANCE"))
    hdus.append(fits.ImageHDU(zodi, name="ZODI"))
    if layout == "qr2":
        zones = np.stack([gaussian_psf(41, 10, 0.6 + 0.05 * k) for k in range(4)]).astype(np.float32)
        hp = fits.Header({"OVERSAMP": 10})
        for k, (x, y) in enumerate([(n * 0.25, n * 0.25), (n * 0.75, n * 0.25), (n * 0.25, n * 0.75), (n * 0.75, n * 0.75)]):
            hp[f"XCTR_{k + 1}"] = x
            hp[f"YCTR_{k + 1}"] = y
        hdus.append(fits.ImageHDU(zones, hp, name="PSF"))
    else:
        zones64 = np.stack([gaussian_psf(33, 5, 0.6 + 0.05 * k) for k in range(4)])
        cols = [
            fits.Column(name="XCENTER", format="E", array=np.array([0.25, 0.75, 0.25, 0.75]) * n),
            fits.Column(name="YCENTER", format="E", array=np.array([0.25, 0.25, 0.75, 0.75]) * n),
            fits.Column(name="EPSF", format="1089D", dim="(33,33)", array=zones64),
        ]
        t = fits.BinTableHDU.from_columns(cols, name="EPSF")
        t.header["OVSMPX"] = 5
        t.header["DETECTOR"] = str(detector)
        hdus.append(t)
    hdus.append(wave_table(n, detector, wave_first))
    buf = io.BytesIO()
    fits.HDUList([fits.PrimaryHDU(), *hdus]).writeto(buf)
    return buf.getvalue(), {"image": image, "flags": flags, "variance": variance, "zodi": zodi}
