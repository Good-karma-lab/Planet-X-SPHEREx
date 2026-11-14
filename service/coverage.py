"""Where SPHEREx exposures are: counts per HEALPix cell (nested, order 3) and per release, from the exposure index.

The globe draws these cells as its coverage layer. The map is computed once from the exposure centres (a cell is about 7.3° across,
an exposure 3.5°, so the centre decides the cell) and kept until the index changes.
"""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path

ORDER = 3


def _spread(v: int) -> int:
    """Interleave the bits of v with zeros: b2 b1 b0 -> b2 0 b1 0 b0."""
    out = 0
    for b in range(16):
        out |= ((v >> b) & 1) << (2 * b)
    return out


def ang2pix_nested(order: int, ra: float, dec: float) -> int:
    """Nested HEALPix pixel of a sky position (Górski et al. 2005, equations of the equatorial and polar zones)."""
    nside = 1 << order
    z = math.sin(math.radians(dec))
    za = abs(z)
    tt = (ra % 360.0) / 90.0
    if za <= 2.0 / 3.0:
        t1 = nside * (0.5 + tt)
        t2 = nside * z * 0.75
        jp = math.floor(t1 - t2)
        jm = math.floor(t1 + t2)
        ifp = jp >> order
        ifm = jm >> order
        face = (ifp | 4) if ifp == ifm else (ifp if ifp < ifm else ifm + 8)
        ix = jm & (nside - 1)
        iy = nside - (jp & (nside - 1)) - 1
    else:
        ntt = min(int(tt), 3)
        tp = tt - ntt
        tmp = nside * math.sqrt(3.0 * (1.0 - za))
        jp = min(int(tp * tmp), nside - 1)
        jm = min(int((1.0 - tp) * tmp), nside - 1)
        if z >= 0:
            face, ix, iy = ntt, nside - jm - 1, nside - jp - 1
        else:
            face, ix, iy = ntt + 8, jp, jm
    return face * nside * nside + _spread(ix) + 2 * _spread(iy)


def coverage_map(index_path: Path, order: int = ORDER) -> dict[str, object]:
    """{order, releases, cells: {pixel: [count per release]}} for every cell that holds an exposure centre."""
    db = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    try:
        releases = [r for (r,) in db.execute("SELECT DISTINCT release FROM files ORDER BY release")]
        pos = {r: i for i, r in enumerate(releases)}
        cells: dict[int, list[int]] = {}
        for rel, cra, cdec in db.execute("SELECT release, cra, cdec FROM files"):
            p = ang2pix_nested(order, cra, cdec)
            c = cells.setdefault(p, [0] * len(releases))
            c[pos[rel]] += 1
    finally:
        db.close()
    return {"order": order, "releases": releases, "cells": {str(k): v for k, v in sorted(cells.items())}}
