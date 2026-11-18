"""Find Planet X's PSF fits run in a worker process on a box around the places (service/workers/planetx.py fit_places): the box and
the shifted coordinates give the same fluxes as the fits on the whole rows did."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np

from engine import photometry
from engine.spx import psf as spxpsf
from service.workers import planetx
from service.workers.planetx import HALF, OMEGA_SR, fit_places


def _psf(osf: int = 3) -> Any:
    n = 33
    c = (n - 1) / 2.0
    j, i = np.mgrid[0:n, 0:n]
    g = np.exp(-(((i - c) / osf) ** 2 + ((j - c) / osf) ** 2) / (2 * 0.9**2))
    g /= g.sum() * osf**2
    return SimpleNamespace(zones=np.stack([g, g]), oversampling=osf)


def test_the_box_gives_the_same_fluxes_as_the_whole_rows() -> None:
    rng = np.random.default_rng(1)
    ps = _psf()
    ny, nx = 60, 400
    img = rng.normal(0.0, 0.05, (ny, nx))
    places = [(0, 120.3, 20.6), (1, 300.8, 41.2), (2, 2.0, 30.0)]  # the last too near the edge for a stamp
    for _, x, y in places[:2]:
        xi, yi = round(x), round(y)
        img[yi - HALF : yi + HALF + 1, xi - HALF : xi + HALF + 1] += 40.0 * spxpsf.stamp(ps, 0, x - xi, y - yi, HALF)
    good = np.ones_like(img, bool)
    none = np.zeros_like(img, bool)
    var = np.full((2 * HALF + 1, 2 * HALF + 1), 0.05**2)
    # the old way: every place fitted on the whole rows, at its own pixel
    want = {}
    for k, x, y in places:
        xi, yi = round(x), round(y)
        if xi - HALF < 0:
            continue
        sl = (slice(yi - HALF, yi + HALF + 1), slice(xi - HALF, xi + HALF + 1))
        pt = photometry.fit(img[sl], var, good[sl], none[sl], spxpsf.stamp(ps, 0, x - xi, y - yi, HALF), x - xi, y - yi, OMEGA_SR)
        if pt.ok:
            want[k] = pt.flux_mjy
    # the new way: a box of columns, the places shifted into it, one PSF zone sent
    x0, x1 = 100, 320
    box = (slice(None), slice(x0, x1))
    inside = [(k, x - x0, y, 0) for k, x, y in places if x0 <= x < x1]
    got = fit_places(img[box], good[box], none[box], none[box], var, inside, {0: ps.zones[0]}, 3)
    assert {k: f for k, f, _ in got} == want and set(want) == {0, 1}
    # a place whose stamp would leave the box is skipped, never read through the other side
    assert fit_places(img[box], good[box], none[box], none[box], var, [(9, 1.0, 30.0, 0)], {0: ps.zones[0]}, 3) == []
    assert planetx.cpu is not None  # the fits go through the process pool
