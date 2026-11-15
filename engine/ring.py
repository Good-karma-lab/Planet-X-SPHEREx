"""Brightness of a star whose core is saturated, from the ring of its light outside the core, in the stacks of the passes.

A saturated core carries no flux, but the wings of the star's image outside it do, and they scale with the star's total light. The same ring
(its radii in detector pixels, the same on every pass and detector) is summed in each pass's detector stack above a background taken from a
wider annulus; the change of that sum from pass to pass is the star's change of brightness at that wavelength, provided the shape of the
image does not change between passes. Its absolute value is not a flux: the earlier validation of wing photometry on ordinary stars found
the absolute scale uncertain by a third. Stars of the same field, measured the same way, say how well the ratio between passes holds
(`control_scatter`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import center_of_mass, label

SAT_LEVEL = 0.3
BG_FACTOR = (1.6, 2.6)  # the background annulus, in units of the outer radius of the ring


@dataclass(frozen=True)
class Ring:
    flux: float  # sum above the background, in the unit of the image times pixels
    err: float
    n_pix: int
    bg: float


def core_radius(sat: NDArray[np.floating], x: float, y: float, search: float) -> float:
    """Radius (grid pixels) that holds the saturated zone around (x, y): the farthest saturated pixel of the blob nearest the place."""
    n = sat.shape[0]
    r = math.ceil(search)
    y0, y1, x0, x1 = max(0, int(y) - r), min(n, int(y) + r + 1), max(0, int(x) - r), min(n, int(x) + r + 1)
    win = sat[y0:y1, x0:x1] > SAT_LEVEL
    if not win.any():
        return 0.0
    lab, nlab = label(win)
    best = None
    for k in range(1, nlab + 1):
        cy, cx = center_of_mass(lab == k)
        d = math.hypot(cx + x0 - x, cy + y0 - y)
        if best is None or d < best[0]:
            best = (d, k)
    if best is None or best[0] > search:
        return 0.0
    ys, xs = np.nonzero(lab == best[1])
    return float(np.hypot(xs + x0 - x, ys + y0 - y).max())


def ring_flux(img: NDArray[np.floating], x: float, y: float, r1: float, r2: float) -> Ring | None:
    """The light in the ring r1..r2 around (x, y) above the background of the annulus BG_FACTOR[0]·r2 .. BG_FACTOR[1]·r2 (median), with its
    error from the scatter of that annulus (robust) over the pixels of the ring; None when the ring or the annulus lacks valid pixels."""
    n = img.shape[0]
    rb = BG_FACTOR[1] * r2
    x0, x1, y0, y1 = max(0, int(x - rb) - 1), min(n, int(x + rb) + 2), max(0, int(y - rb) - 1), min(n, int(y + rb) + 2)
    cut = img[y0:y1, x0:x1].astype(np.float64)
    yy, xx = np.mgrid[y0:y1, x0:x1]
    d = np.hypot(xx - x, yy - y)
    ring = (d >= r1) & (d < r2) & np.isfinite(cut)
    ann = (d >= BG_FACTOR[0] * r2) & (d < rb) & np.isfinite(cut)
    if ring.sum() < 12 or ann.sum() < 30 or np.isfinite(cut[d < r2]).mean() < 0.9:
        return None
    bg = float(np.median(cut[ann]))
    sig = 1.4826 * float(np.median(np.abs(cut[ann] - bg)))
    n_ring = int(ring.sum())
    return Ring(float(np.sum(cut[ring] - bg)), float(sig * math.sqrt(n_ring)), n_ring, bg)


def ratio(a: Ring | None, b: Ring | None) -> tuple[float, float] | None:
    """a / b with its error (the errors of the two rings combined); None when either is missing or b is not positive."""
    if a is None or b is None or b.flux <= 0 or a.flux <= 0:
        return None
    r = a.flux / b.flux
    return r, r * math.hypot(a.err / a.flux, b.err / b.flux)
