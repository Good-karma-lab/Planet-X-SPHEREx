# ruff: noqa: E501
"""svs-v2 (HEALPix) and svs-eq (equirect) against the same sky places: Vega, Arcturus, beta Oph and Barnard's star (J2000)."""

import sys

import numpy as np
from astropy import units as u
from astropy_healpix import lonlat_to_healpix
from PIL import Image

root_hp, root_eq = sys.argv[1], sys.argv[2]
OFF = None


def compact(v):
    v = v & 0x55555555
    v = (v | (v >> 1)) & 0x33333333
    v = (v | (v >> 2)) & 0x0F0F0F0F
    v = (v | (v >> 4)) & 0x00FF00FF
    v = (v | (v >> 8)) & 0x0000FFFF
    return v


def hp_pixel(ra, dec, order=6):
    idx = int(lonlat_to_healpix(ra * u.deg, dec * u.deg, 2 ** (order + 9), order="nested"))
    tile, off = idx >> 18, idx & 0x3FFFF
    r, c = int(compact(np.int64(off))), int(compact(np.int64(off >> 1)))
    return tile, r, c


def hp_window(ra, dec, order=6, rad=6):
    tile, r, c = hp_pixel(ra, dec, order)
    p = f"{root_hp}/Norder{order}/Dir{tile // 10000 * 10000}/Npix{tile}.webp"
    a = np.asarray(Image.open(p).convert("RGB")).astype(np.float32).sum(-1)
    win = a[max(0, r - rad) : r + rad + 1, max(0, c - rad) : c + rad + 1]
    yy, xx = np.unravel_index(win.argmax(), win.shape)
    return win.max() / 3.0, xx - min(rad, c), yy - min(rad, r)


def eq_window(ra, dec, level=7, rad=3):
    width = 512 * 2**level
    height = width // 2
    x, y = ra / 360 * width, (90 - dec) / 180 * height
    tx, ty = int(x // 512), int(y // 512)
    a = np.asarray(Image.open(f"{root_eq}/L{level}/{tx}_{ty}.webp").convert("RGB")).astype(np.float32).sum(-1)
    px, py = int(x % 512), int(y % 512)
    win = a[max(0, py - rad) : py + rad + 1, max(0, px - rad) : px + rad + 1]
    yy, xx = np.unravel_index(win.argmax(), win.shape)
    return win.max() / 3.0, xx - min(rad, px), yy - min(rad, py)


for name, ra, dec in (
    ("Vega", 279.2347, 38.7837),
    ("Arcturus", 213.9153, 19.1824),
    ("beta Oph", 265.8680, 4.5673),
    ("Barnard J2000", 269.4520, 4.6934),
    ("Barnard 2025.9", 269.4520 - 0.0000 - 797.8 * 25.9 / 3.6e6 / 0.9967, 4.6934 + 10326.9 * 25.9 / 3.6e6),
):
    print(
        f"{name:15s} svs-v2 order 6: peak {hp_window(ra, dec)[0]:6.1f} offset {hp_window(ra, dec)[1:]} | svs-eq L7: peak {eq_window(ra, dec)[0]:6.1f} offset {eq_window(ra, dec)[1:]}"
    )
