from __future__ import annotations

import math
from typing import Any

from service import browndwarfs as bd

MJDS = [60800.0, 60980.0, 61160.0]


def red_row(pm_east: float, pm_north: float, moves: bool = True, colour: float = 6.0) -> dict[str, float | bool]:
    return {
        "ra_j2000": 133.0,
        "dec_j2000": -7.0,
        "pmra": pm_east,
        "pmdec": pm_north,
        "colour": colour,
        "lower_limit": False,
        "snr_long": 12.0,
        "snr_short": 6.0,
        "moves": moves,
        "red": True,
    }


def test_only_red_sources_that_move_fast_enough_are_candidates() -> None:
    red = {"red": [red_row(-8000.0, 700.0), red_row(100.0, 50.0), red_row(-8000.0, 700.0, moves=False)], "sources": 3}
    out = bd.red_movers(red, MJDS)
    assert len(out) == 1
    c = out[0]
    assert math.isclose(c["pm"], math.hypot(8000.0, 700.0), abs_tol=0.2)
    assert 270.0 < c["pa"] < 290.0
    assert c["ra"] < 133.0  # moving west from 2000 to 2025.9


def test_pseudo_track_follows_the_motion_over_the_passes() -> None:
    c = bd.red_movers({"red": [red_row(-8000.0, 0.0)]}, MJDS)[0]
    t = bd.pseudo_track(c, MJDS)
    assert t["ra"][0] > t["ra"][1] > t["ra"][2]
    assert math.isclose(t["speed"], 8.0, abs_tol=0.01)
    assert t["check"] == "pending"


def test_a_track_of_the_movers_search_that_stands_there_is_evidence() -> None:
    c = bd.red_movers({"red": [red_row(-8000.0, 0.0)]}, MJDS)[0]
    t = bd.pseudo_track(c, MJDS)
    track = {
        "mjd": MJDS,
        "ra": t["ra"],
        "dec": t["dec"],
        "passes": [0, 1, 2],
        "snr": [9.0, 8.0, 7.0],
        "rms_arcsec": 0.4,
        "speed": 8.0,
        "pa": 270.0,
    }
    m = bd.mover_match(c, [track])
    assert m is not None and m["offset_arcsec"] < 0.1
    far = dict(track, ra=[x + 0.01 for x in t["ra"]])
    assert bd.mover_match(c, [far]) is None


def test_the_catalogues_sort_moving_red_sources_into_candidates_and_known_ones() -> None:
    wise: dict[str, Any] = {"simbad": {"state": "ok", "otype": "BD*"}, "gaia": {"verdict": "none"}}
    star: dict[str, Any] = {"simbad": {"state": "ok", "otype": "Star"}, "gaia": {"verdict": "same"}}
    gaia_only: dict[str, Any] = {"simbad": {"state": "none"}, "gaia": {"verdict": "same"}}
    new: dict[str, Any] = {"simbad": {"state": "none"}, "gaia": {"verdict": "none"}}
    waiting: dict[str, Any] = {"simbad": {"state": "unavailable"}, "gaia": {"verdict": "none"}}
    for c in (wise, star, gaia_only, new, waiting):
        c["status"] = bd.status_of(c)
    assert [bd.group_of(c) for c in (wise, star, gaia_only, new, waiting)] == ["known", "known", "known", "candidate", "candidate"]
    assert wise["status"] == "known brown dwarf" and gaia_only["status"] == "star with Gaia motion" and new["status"] == "unidentified"
