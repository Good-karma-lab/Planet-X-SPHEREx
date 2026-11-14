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


def test_a_saturated_fast_star_is_a_mover_named_by_gaia() -> None:
    """epsilon Indi A on synthetic stacks: Gaia's place and motion carry the star to every pass; the saturated zone there is its track."""
    from engine.grid import Grid
    from service.movers import saturated_movers, year_of
    from service.products.sxp import encode

    grid = Grid(330.8923, -56.8042, STEP, 200)
    mjds = [60900.0, 61100.0, 61290.0]
    star = {"source_id": 6412595290592307840, "ra": 330.8724079, "dec": -56.7972547, "pmra": 3966.66, "pmdec": -2536.19, "g": 4.32}
    raws = []
    for m in mjds:
        k = year_of(m) - 2016.0
        d = star["dec"] + star["pmdec"] * k / 3.6e6
        a = star["ra"] + star["pmra"] * k / 3.6e6 / np.cos(np.radians(d))
        gx, gy = grid.sky_to_grid([a], [d])
        white = np.full((200, 200), 0.1, np.float32)
        sat = np.zeros((200, 200), np.uint8)
        x0, y0 = round(float(gx[0])), round(float(gy[0]))
        sat[y0 - 3 : y0 + 4, x0 - 3 : x0 + 4] = 255
        raws.append(encode({"white": white, "sat": sat}))
    meta = {
        "grid": {"ra0": grid.ra0, "dec0": grid.dec0, "step_arcsec": STEP, "n": 200},
        "passes": [{"index": i, "mjd_mid": m} for i, m in enumerate(mjds)],
    }
    out = saturated_movers(meta, raws, [star], [])
    assert len(out) == 1
    t = out[0]
    assert t["source"] == "saturated" and t["check"] == "same" and t["gaia"]["source_id"] == "6412595290592307840"
    assert t["speed"] == pytest.approx(4.7, abs=0.4) and 115.0 < t["pa"] < 130.0 and t["passes"] == [0, 1, 2]
    assert saturated_movers(meta, raws, [star], [dict(t, mjd=t["mjd"])]) == []  # a track of the search stands there: not added twice
    still = dict(star, ra=star["ra"] + 0.05)  # a star whose catalogue place has no saturated zone in the stacks is not claimed
    assert saturated_movers(meta, raws, [still], []) == []


def test_a_motion_is_a_mover_only_at_5_sigma_and_a_pixel_over_the_baseline() -> None:
    from service.movers import Movers, Run, motion_grade

    one_year = [60900.0, 61100.0, 61265.0]
    slow = motion_grade(4.2, 1.0, one_year)  # R Dra 20′: 4.2″/yr, one-year baseline: under a pixel, ~3σ
    assert slow["grade"] == "slow" and slow["shift_arcsec"] < 6.15 and slow["sigma"] < 5
    fast = motion_grade(11.2, 0.35, [60926.7, 61139.4, 61287.1])  # Barnard's Star
    assert fast["grade"] == "mover" and fast["sigma"] > 5 and fast["shift_arcsec"] > 6.15
    # a track the catalogue confirms is shown whatever its size; an unexplained slow one goes to `slow_tracks`
    base = {"mjd": one_year, "check": "same", "gaia": None}
    tracks = [
        dict(base, speed=4.3, **slow, check="same"),
        dict(base, speed=4.2, **slow, check="none"),
        dict(base, speed=11.2, **fast, check="none"),
    ]
    mv = object.__new__(Movers)
    view = mv.view(Run(key="k", field_key="f", state="done", result={"tracks": tracks}))
    r = view["result"]
    assert [t["check"] for t in r["tracks"]] == ["same", "none"] and r["tracks"][1]["grade"] == "mover"
    assert len(r["slow_tracks"]) == 1 and r["slow"] == 1
