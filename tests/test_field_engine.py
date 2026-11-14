from __future__ import annotations

import numpy as np
import pytest

from engine.bands import load_bands
from engine.colour import DETECTOR_RANGES_UM
from engine.field import NativeExposure, build_pass, quick_subset
from engine.grid import Grid
from engine.passes import common_detectors, detectors_per_pass, split_passes
from engine.register import centroid, register, stars
from tests.synth_sky import render

STEP = 1.5375
N = 120


def make_pass(rng: np.random.Generator, mover: tuple[float, float], mjd0: float, per_det: int = 6) -> list[NativeExposure]:
    static = [(30.0, 30.0, 30.0), (90.0, 35.0, 25.0), (35.0, 92.0, 25.0), (95.0, 95.0, 30.0), (60.0, 20.0, 20.0)]
    out = []
    for det in range(1, 7):
        lo, hi = DETECTOR_RANGES_UM[det]
        for k in range(per_det):
            e = render([*static, (mover[0], mover[1], 40.0)], 34, STEP, N, rng)
            lam = np.full(e.val.size, lo + (hi - lo) * (k + 0.5) / per_det, np.float32)
            out.append(NativeExposure(f"f{det}{k}", det, mjd0 + 0.1 * k + 0.01 * det, k // 2, e.u, e.v, e.val, lam, e.sigma))
    return out


def test_three_passes_show_the_motion_and_static_stars_stand_still() -> None:
    rng = np.random.default_rng(7)
    grid = Grid(10.0, 0.0, STEP, N)
    movers = [(60.0, 60.0), (63.0, 60.0), (66.0, 61.0)]  # grid px
    all_exps = [make_pass(rng, m, 61000.0 + 180 * p) for p, m in enumerate(movers)]
    flat = [e for p in all_exps for e in p]
    passes = split_passes([e.mjd for e in flat])
    assert len(passes) == 3
    cdet = common_detectors(detectors_per_pass(passes, [e.det for e in flat]))
    assert cdet == (1, 2, 3, 4, 5, 6)
    bands = load_bands()
    pp = [build_pass(i, ex, grid, bands, cdet) for i, ex in enumerate(all_exps)]
    w0 = pp[0].white(cdet)
    ref = stars(w0, None, STEP)
    for p in pp[1:]:
        reg = register(ref, stars(p.white(cdet), None, STEP), STEP)
        assert abs(reg.dx) < 0.2 and abs(reg.dy) < 0.2  # static stars: no shift needed
        p.register(reg)
    s = 9.0 / STEP / 2.3548
    found = [centroid(np.nan_to_num(p.white(cdet)), 60.0 + 3 * k, 60.0 + 0.5 * k, s) for k, p in enumerate(pp)]
    for (x, y), (mx, my) in zip(found, movers, strict=True):
        assert x == pytest.approx(mx, abs=0.25) and y == pytest.approx(my, abs=0.25)
    rgb = pp[0].rgb(cdet)
    assert rgb.shape == (3, N, N) and np.isfinite(rgb[:, 60, 60]).all()
    assert "star" in pp[0].num and "co" in pp[0].num  # band stacks from the pixels whose wavelength lies in the windows
    assert pp[0].detectors == {d: 6 for d in range(1, 7)}


def test_quick_subset_takes_a_few_per_detector() -> None:
    rng = np.random.default_rng(1)
    ex = make_pass(rng, (60.0, 60.0), 61000.0, per_det=8)
    idx, sig = quick_subset(ex, 5.0)
    assert len(idx) == 4 * 6 and sig == 0.5
    idx, sig = quick_subset(ex, 60.0)
    assert len(idx) == 2 * 6 and sig == 1.0


def test_moffat_centre_from_the_wings_of_a_saturated_star() -> None:
    from engine.centroid import moffat_centre

    rng = np.random.default_rng(3)
    e = render([(60.3, 59.6, 400.0)], 34, STEP, N, rng)
    core = np.hypot(e.u - 60.3, e.v - 59.6) < 1.5 * 6.15 / STEP  # the saturated core has no values
    got = moffat_centre(e.u[~core], e.v[~core], e.val[~core], 61.0, 59.0, STEP)
    assert got is not None
    assert got[0] == pytest.approx(60.3, abs=0.3) and got[1] == pytest.approx(59.6, abs=0.3)


def test_parallax_sign_barnards_star() -> None:
    """In early March the Sun is near RA 345°, so a star at RA 269° appears displaced east of its proper-motion place by up to its
    parallax in RA (Δα·cosδ = ϖ(X sinα − Y cosα) > 0); half a year later the sign reverses."""
    from engine.astrometry import offset_arcsec, place

    ra0, dec0 = 269.4520769586187, 4.693364966576667
    pm = (-801.551, 10362.394)
    r1, d1 = place(ra0, dec0, 2000.0, pm[0], pm[1], 546.9759, 61105.0)  # 2026-03-05
    r0, d0 = place(ra0, dec0, 2000.0, pm[0], pm[1], 0.0, 61105.0)
    dx, _ = offset_arcsec(r1[0], d1[0], r0[0], d0[0])
    assert 0.3 < dx < 0.6
    r2, d2 = place(ra0, dec0, 2000.0, pm[0], pm[1], 546.9759, 61288.0)  # 2026-09-04
    r3, d3 = place(ra0, dec0, 2000.0, pm[0], pm[1], 0.0, 61288.0)
    assert offset_arcsec(r2[0], d2[0], r3[0], d3[0])[0] < -0.3
