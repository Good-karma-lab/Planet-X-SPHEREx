"""Reads baked tiles back into an equirectangular picture (RA increasing to the left, north up) to look at a layer as a whole."""

import sys
from pathlib import Path

import numpy as np
from astropy import units as u
from astropy_healpix import lonlat_to_healpix
from PIL import Image

root, layer, order, out = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), sys.argv[4]
W, H = 1440, 720


def compact(v):
    v = v & 0x55555555
    v = (v | (v >> 1)) & 0x33333333
    v = (v | (v >> 2)) & 0x0F0F0F0F
    v = (v | (v >> 4)) & 0x00FF00FF
    v = (v | (v >> 8)) & 0x0000FFFF
    return v


ra = (np.arange(W) + 0.5) / W * 360.0
dec = 90.0 - (np.arange(H) + 0.5) / H * 180.0
RA, DEC = np.meshgrid(ra if len(sys.argv) > 5 else ra[::-1], dec)
idx = lonlat_to_healpix(RA.ravel() * u.deg, DEC.ravel() * u.deg, 2 ** (order + 9), order="nested").astype(np.int64)
tile, off = idx >> 18, idx & 0x3FFFF
r, c = compact(off), compact(off >> 1)
img = np.zeros((H * W, 3), np.uint8)
for t in np.unique(tile):
    p = root / layer / f"Norder{order}" / f"Dir{t // 10000 * 10000}" / f"Npix{t}.webp"
    if not p.exists():
        continue
    a = np.asarray(Image.open(p).convert("RGB"))
    sel = tile == t
    img[sel] = a[r[sel], c[sel]]
Image.fromarray(img.reshape(H, W, 3)).save(out)
