"""The search between passes on synthetic stacks: a star that moves is one track, still stars are none."""

from __future__ import annotations

import numpy as np
import pytest

from engine.movers import search

STEP = 1.5  # ″ per grid pixel


def frame(stars: list[tuple[float, float, float]], n: int = 200, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:n, 0:n]
    img = rng.normal(0.0, 0.02, (n, n))
    s = 8.8 / 2.3548 / STEP
    for x, y, a in stars:
        img += a * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * s * s))
    return img.astype(np.float32)


def test_one_mover_among_still_stars() -> None:
    still = [(40.0, 50.0, 3.0), (150.0, 60.0, 2.0), (70.0, 140.0, 4.0), (160.0, 160.0, 1.5)]
    mjds = [60900.0, 61100.0, 61290.0]
    years = [(m - mjds[0]) / 365.25 for m in mjds]
    v = 20.0 / STEP  # 20 ″/yr toward the north (−y)
    whites = [frame([*still, (100.0, 120.0 - v * t, 2.5)], seed=k) for k, t in enumerate(years)]
    sats = [np.zeros_like(w) for w in whites]
    r = search(whites, sats, mjds, STEP)
    assert len(r.tracks) == 1
    t = r.tracks[0]
    assert t.speed == pytest.approx(20.0, rel=0.05)
    assert t.pa == pytest.approx(0.0, abs=3.0) or t.pa == pytest.approx(360.0, abs=3.0)
    assert r.stationary >= 4


def test_two_passes_give_no_track() -> None:
    w = frame([(100.0, 100.0, 3.0)])
    assert search([w, w], [np.zeros_like(w)] * 2, [60900.0, 61100.0], STEP).tracks == []
