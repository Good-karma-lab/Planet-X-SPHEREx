from __future__ import annotations

import numpy as np
import pytest

from engine.grid import STEP_FINE_ARCSEC, STEP_WIDE_ARCSEC, grid_for_fov
from engine.passes import common_detectors, detectors_per_pass, split_passes, split_visits


def test_passes_split_on_long_gaps_and_cut_at_90_days() -> None:
    t = [60900.0, 60901.0, 60930.0, 61100.0, 61105.0]  # a gap of 170 days
    p = split_passes(t)
    assert [list(x.members) for x in p] == [[0, 1, 2], [3, 4]]
    continuous = list(np.arange(61000.0, 61200.0, 2.0))  # observed without a break for 200 days
    q = split_passes(continuous)
    assert len(q) == 3 and all(x.mjd1 - x.mjd0 <= 90.0 for x in q)
    assert q[0].mjd_mid == pytest.approx(0.5 * (q[0].mjd0 + q[0].mjd1))


def test_visits_per_detector() -> None:
    t = [0.0, 0.001, 0.002, 1.0, 1.001, 0.0005]
    d = [1, 1, 1, 1, 1, 2]
    assert split_visits(t, d) == [(0, 1, 2), (5,), (3, 4)]


def test_common_detectors_need_four_in_every_pass() -> None:
    t = [0.0] * 10 + [200.0] * 6
    d = [1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 1, 1, 1, 1, 2, 2]
    counts = detectors_per_pass(split_passes(t), d)
    assert counts == [{1: 4, 2: 4, 3: 2}, {1: 4, 2: 2}]
    assert common_detectors(counts) == (1,)


@pytest.mark.parametrize(("fov", "step"), [(2, STEP_FINE_ARCSEC), (5, STEP_FINE_ARCSEC), (20, STEP_FINE_ARCSEC), (60, STEP_WIDE_ARCSEC)])
def test_grid_rule(fov: float, step: float) -> None:
    g = grid_for_fov(269.45, 4.77, fov)
    assert g.step_arcsec == step and g.n % 4 == 0 and g.fov_arcmin >= fov


def test_grid_orientation_and_round_trip() -> None:
    g = grid_for_fov(269.45, 4.77, 5)
    x, y = g.sky_to_grid([269.45], [4.77])
    assert x[0] == pytest.approx(g.centre_px) and y[0] == pytest.approx(g.centre_px)
    xn, yn = g.sky_to_grid([269.45], [4.77 + 10 / 3600])  # 10″ north: up (smaller y)
    assert yn[0] == pytest.approx(g.centre_px - 10 / g.step_arcsec, rel=1e-6) and xn[0] == pytest.approx(g.centre_px)
    xe, _ = g.sky_to_grid([269.45 + 10 / 3600 / np.cos(np.radians(4.77))], [4.77])  # 10″ east: left (smaller x)
    assert xe[0] < g.centre_px
    ra, dec = g.grid_to_sky([3.3, 150.7], [99.1, 7.2])
    x2, y2 = g.sky_to_grid(ra, dec)
    np.testing.assert_allclose(x2, [3.3, 150.7], atol=1e-8)
    np.testing.assert_allclose(y2, [99.1, 7.2], atol=1e-8)


def test_seasons_near_the_pole_keep_those_that_share_detectors() -> None:
    from engine.passes import Pass, common_detectors, detectors_per_pass, seasons_with_common_detectors

    # six seasons whose detectors rotate: D3 and D6 are shared by seasons 0, 2, 3 and 4
    dets = [[3, 6, 1], [1, 4], [3, 6], [3, 6], [1, 3, 6], [1, 4]]
    det: list[int] = []
    passes = []
    for k, ds in enumerate(dets):
        members = []
        for d in ds:
            for _ in range(5):
                members.append(len(det))
                det.append(d)
        passes.append(Pass(k, 100.0 * k, 100.0 * k + 30, tuple(members)))
    assert common_detectors(detectors_per_pass(passes, det)) == ()
    kept = seasons_with_common_detectors(passes, det)
    assert [p.mjd0 for p in kept] == [0.0, 200.0, 300.0, 400.0]
    assert [p.index for p in kept] == [0, 1, 2, 3]
    assert common_detectors(detectors_per_pass(kept, det)) == (3, 6)
