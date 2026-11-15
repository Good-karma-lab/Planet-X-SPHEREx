"""The gate on the own motion of a candidate and the stars whose own track must not be reported as a distant body."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from engine import distant as edist
from service.workers import distantjob as dj


def test_a_tuned_own_motion_must_stay_inside_what_a_bound_orbit_can_have() -> None:
    assert dj.inside_gate((0.1, -0.1), 707.1, 1.0)  # the limit at 707 au is 0.27 arcsec/day
    assert not dj.inside_gate((0.36, -2.33), 707.1, 1.0)  # Barnard 5': own motion 2.36 against the limit 0.28
    assert dj.inside_gate((2.3, 0.0), 100.0, 1.0)  # nearer bodies move faster: the limit at 100 au is 5.0


def test_bright_fast_stars_are_carried_to_the_pass_dates() -> None:
    star = {"ra": 269.45, "dec": 4.69, "pmra": -800.0, "pmdec": 10300.0}

    async def compute(fn: Any, *a: Any) -> list[dict[str, Any]]:
        return [star]

    ctx = SimpleNamespace(compute=compute)
    exps = [SimpleNamespace(mjd0=m) for m in (60834.2, 60900.0, 61000.0)]
    t = edist.Tangent(269.45, 4.69)
    places = asyncio.run(dj.bright_fast_stars(ctx, t, 100, exps))  # type: ignore[arg-type]
    assert len(places) == 3
    # Barnard's star moves 10.3 arcsec/yr north: the three places differ by the motion over the pass (about 0.5 arcsec north per 17 days)
    assert places[2][1] > places[0][1]


def test_no_gaia_answer_claims_no_star() -> None:
    async def compute(fn: Any, *a: Any) -> list[dict[str, Any]]:
        raise OSError("no network")

    t = edist.Tangent(269.45, 4.69)
    assert asyncio.run(dj.bright_fast_stars(SimpleNamespace(compute=compute), t, 100, [SimpleNamespace(mjd0=1.0)])) == []  # type: ignore[arg-type]
