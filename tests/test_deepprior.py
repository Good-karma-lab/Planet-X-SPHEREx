# mypy: disable-error-code="no-untyped-call"
from __future__ import annotations

import math

import numpy as np

from engine import deepsum as edeep
from engine import distant as edist
from service.workers import deepjob


def ecliptic_point(lon: float, lat: float) -> tuple[float, float]:
    eps = math.radians(23.4392911)
    lo, la = math.radians(lon), math.radians(lat)
    ra = math.atan2(math.sin(lo) * math.cos(eps) - math.tan(la) * math.sin(eps), math.cos(lo))
    dec = math.asin(math.sin(la) * math.cos(eps) + math.cos(la) * math.sin(eps) * math.sin(lo))
    return math.degrees(ra) % 360.0, math.degrees(dec)


def test_the_prior_keeps_only_motions_of_the_right_speed_and_direction() -> None:
    ra, dec = ecliptic_point(120.0, 5.0)
    pr = edeep.make_prior(edist.Tangent(ra, dec), 5.0, 30.0, 0.4, 0.9)
    assert not pr["none"]
    d, cs = 400.0, 0.02
    full, some = edeep.cells(d, cs), edeep.cells(d, cs, pr)
    assert 0 < len(some) < len(full) / 2
    r = np.hypot(some[:, 0], some[:, 1])
    mesc = 5016.0 / d**1.5
    assert r.min() >= 0.4 * mesc - 0.8 * cs and r.max() <= 0.9 * mesc + 0.8 * cs


def test_an_orbit_inclined_less_than_the_latitude_never_gets_there() -> None:
    ra, dec = ecliptic_point(120.0, 50.0)  # 50 degrees above the ecliptic; the prior reaches 30
    pr = edeep.make_prior(edist.Tangent(ra, dec), 5.0, 30.0, 0.4, 0.9)
    assert pr["none"]
    assert len(edeep.cells(300.0, 0.02, pr)) == 0


def test_the_prior_is_part_of_a_deep_search_only_when_given() -> None:
    fields = [[34.72, -49.79, 60900.0], [34.7, -49.8, 61080.0]]
    plain = deepjob.deep_params(fields, 60.0, 250.0, 2000.0, (4,), 6.0, 15)
    with_prior = deepjob.deep_params(fields, 60.0, 250.0, 2000.0, (4,), 6.0, 15, (5.0, 30.0, 0.4, 0.9))
    assert "prior" not in plain and with_prior["prior"] == [5.0, 30.0, 0.4, 0.9]
