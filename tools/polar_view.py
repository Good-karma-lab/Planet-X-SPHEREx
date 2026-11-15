"""A look from above the north pole (dec >= 90 - R) at a HEALPix tile layer: python polar_view.py ROOT ORDER R_DEG OUT.png"""

import sys
from pathlib import Path

import numpy as np
from astropy import units as u
from astropy_healpix import lonlat_to_healpix
from PIL import Image

root, order, R, out = Path(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
S = 1200
yy, xx = np.mgrid[0:S, 0:S]
dx, dy = (xx - S / 2 + 0.5) / (S / 2), (S / 2 - yy - 0.5) / (S / 2)
r = np.hypot(dx, dy)
az = np.degrees(np.arctan2(dy, dx)) % 360.0
dec = 90.0 - r * R
ok = r <= 1.0


def compact(v):
    v = v & 0x55555555
    v = (v | (v >> 1)) & 0x33333333
    v = (v | (v >> 2)) & 0x0F0F0F0F
    v = (v | (v >> 4)) & 0x00FF00FF
    v = (v | (v >> 8)) & 0x0000FFFF
    return v


idx = lonlat_to_healpix(az[ok] * u.deg, dec[ok] * u.deg, 2 ** (order + 9), order="nested").astype(np.int64)
tile, off = idx >> 18, idx & 0x3FFFF
rr, cc = compact(off), compact(off >> 1)
img = np.zeros((S * S, 3), np.uint8)
vals = np.zeros((int(ok.sum()), 3), np.uint8)
for t in np.unique(tile):
    p = root / f"Norder{order}" / f"Dir{t // 10000 * 10000}" / f"Npix{t}.webp"
    if not p.exists():
        continue
    a = np.asarray(Image.open(p).convert("RGB"))
    sel = tile == t
    vals[sel] = a[rr[sel], cc[sel]]
img[ok.ravel()] = vals
Image.fromarray(img.reshape(S, S, 3)).save(out)
