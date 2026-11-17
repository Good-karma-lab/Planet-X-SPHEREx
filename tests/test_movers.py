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


def test_a_track_between_two_static_stars_is_a_centroid_shift() -> None:
    import asyncio
    import math
    from types import SimpleNamespace

    from service.movers import Movers

    track: dict[str, object] = {
        "mjd": [60834.2446, 61017.9337, 61285.5887],
        "ra": [247.414524, 247.418455, 247.422781],
        "dec": [67.197779, 67.197028, 67.197011],
        "v_east": 9.218,
        "v_north": -2.192,
        "speed": 9.48,
    }
    star_a = {"source_id": 1, "ra": 247.4136074, "dec": 67.1979340, "pmra": 1.6, "pmdec": 5.7, "parallax": 1.0, "phot_g_mean_mag": 13.3}
    star_b = {"source_id": 2, "ra": 247.4252363, "dec": 67.1968966, "pmra": 0.1, "pmdec": -7.8, "parallax": 1.0, "phot_g_mean_mag": 13.4}

    async def near(ra: float, dec: float, radius: float) -> list[dict[str, object]]:
        return [s for s in (star_a, star_b) if math.hypot((s["ra"] - ra) * math.cos(math.radians(dec)), s["dec"] - dec) * 3600.0 < radius]  # type: ignore[operator]

    m = SimpleNamespace(_gaia_near=near, _static_near=None)
    m._static_near = lambda t, k, y: Movers._static_near(m, t, k, y)  # type: ignore[arg-type]
    t = dict(track)
    asyncio.run(Movers._check(m, t))  # type: ignore[arg-type]
    assert t["check"] == "still" and t["gaia"]["pair"] == ["1", "2"]  # type: ignore[index]
    only_one = dict(track)

    async def near_a(ra: float, dec: float, radius: float) -> list[dict[str, object]]:
        return [s for s in await near(ra, dec, radius) if s is star_a]

    m2 = SimpleNamespace(_gaia_near=near_a, _static_near=None)
    m2._static_near = lambda t, k, y: Movers._static_near(m2, t, k, y)  # type: ignore[arg-type]
    asyncio.run(Movers._check(m2, only_one))  # type: ignore[arg-type]
    assert only_one["check"] == "none"


def test_a_saturated_centroid_track_gives_way_to_the_saturated_zone_track_of_its_star() -> None:
    from service.movers import replace_saturated, stands_for

    star = {"source_id": 6412595290592307840, "ra": 330.8402, "dec": -56.7860, "pmra": 3966.7, "pmdec": -2536.2}
    mjds = [60834.2, 61017.9, 61285.6]
    from service.movers import _carried

    places = [_carried(star, m) for m in mjds]
    biased = {  # a track on the halo of the star: it stands within 6 arcsec of it at every pass, with the wrong speed
        "mjd": mjds,
        "ra": [a + 2.0 / 3600.0 for a, _ in places],
        "dec": [d for _, d in places],
        "speed": 6.92,
        "saturated": True,
    }
    other = {"mjd": mjds, "ra": [a + 0.1 for a, _ in places], "dec": [d for _, d in places], "speed": 4.68, "saturated": False}
    right = dict(biased, speed=4.75)  # a saturated track whose speed is the star's own (Barnard's star) is the star's track: it stays
    assert stands_for(biased, star) and not stands_for(other, star)
    extra = [{"gaia": {"source_id": str(star["source_id"])}, "saturated": True, "speed": 4.29}]
    kept = replace_saturated([biased, other], extra, [star])
    assert kept == [other]  # the biased halo track is replaced, the unrelated one stays
    assert replace_saturated([right], extra, [star]) == [right]
    assert replace_saturated([biased, other], [], [star]) == [biased, other]  # nothing replaces it when no saturated track was found


def test_a_saturated_star_is_named_by_simbad_in_a_wide_cone_else_by_gaia(tmp_path: object) -> None:
    """ε Indi A on ε Indi B's 20′ field was "Mover 2": a saturated track's centre is several arcseconds off the star, so SIMBAD is
    asked in a wider cone, and Gaia's name stands when SIMBAD gives nothing."""
    import asyncio
    from pathlib import Path

    from service.movers import SATURATED_NAME_ARCSEC, Movers

    asked: list[float] = []

    class Cat:
        def __init__(self, answer: dict[str, object]) -> None:
            self.answer = answer

        async def simbad_at(self, ra: float, dec: float, epoch: float, radius: float) -> dict[str, object]:
            asked.append(radius)
            return self.answer

    m = Movers(None, Path(str(tmp_path)))  # type: ignore[arg-type]
    t = {"ra_2025_9": 330.84, "dec_2025_9": -56.79, "saturated": True, "gaia": {"source_id": "6412595290592307840"}}
    m.catalogue = Cat({"state": "ok", "display_name": "eps Ind A"})
    assert asyncio.run(m._star_name(t)) == "eps Ind A" and asked == [SATURATED_NAME_ARCSEC]
    m.catalogue = Cat({"state": "none"})
    assert asyncio.run(m._star_name(t)) == "Gaia DR3 6412595290592307840"
    m.catalogue = None
    assert asyncio.run(m._star_name(t)) == "Gaia DR3 6412595290592307840"
