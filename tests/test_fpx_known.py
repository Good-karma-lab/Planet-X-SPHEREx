"""A Find Planet X lead that is a known body says so: Sedna's real blind lead against a fake JPL."""

from __future__ import annotations

import asyncio
import json

import httpx

from service import fpx_known

# the blind lead of the live run: its places at each pass's own date and Sedna's JPL places there (0.8″, 0.5″, 0.8″ away)
PLACES = [
    {"ra": 61.77844, "dec": 8.83671, "mjd": 60909.848, "mjd0": 60905.56, "mjd1": 60933.48},
    {"ra": 60.71254, "dec": 8.60824, "mjd": 61082.27, "mjd0": 61065.72, "mjd1": 61092.54},
    {"ra": 62.38896, "dec": 8.9754, "mjd": 61272.847, "mjd0": 61267.78, "mjd1": 61284.42},
]
JPL = {60909.848: (61.77866, 8.83668), 61082.27: (60.71266, 8.60838), 61272.847: (62.38874, 8.97551)}


def fake_jpl(far: bool = False) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if "sb_ident" in str(request.url):
            fields = [
                "Object name",
                'Dist. from center RA (")',
                'Dist. from center Dec (")',
                "Visual magnitude (V)",
                'RA rate ("/h)',
                'Dec rate ("/h)',
            ]
            rows = [
                ["90377 Sedna (2003 VB12)", "3.0", "-2.0", "20.9", "0.20", "-0.05"],
                ["12345 Fast (1999 AB)", "10", "5", "17.0", "40", "10"],  # a main-belt body: not slow
            ]
            return httpx.Response(200, json={"fields_first": fields, "data_first_pass": rows})
        q = dict(request.url.params)
        jds = [float(x.strip("'")) for x in q["TLIST"].split()]
        lines = []
        for jd in jds:
            mjd = jd - 2400000.5
            key = min(JPL, key=lambda m: abs(m - mjd))
            ra = JPL[key][0] + (mjd - key) * 0.0017  # Sedna drifts about 6 arcsec a day in the search's frame
            dec = JPL[key][1]
            r = 40.0 if far else 83.0
            lines.append(f"2026-Feb-11 06:28, , , {ra}, {dec}, 20.9, n.a., {r}, 1.0, {r - 1.0}, 0.1,")
        return httpx.Response(200, json={"result": "x\n$$SOE\n" + "\n".join(lines) + "\n$$EOE\n"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_the_blind_sedna_lead_is_named() -> None:
    out = asyncio.run(fpx_known.identify(fake_jpl(), PLACES, 83.0, 83.0))
    assert out["state"] == "ok" and out["name"].startswith("90377 Sedna")
    assert max(out["seps"]) < 2.0 and out["r_au"] == 83.0
    assert out["words"].startswith("this is Sedna (JPL), found blind")


def test_a_body_at_another_distance_is_not_the_lead() -> None:
    out = asyncio.run(fpx_known.identify(fake_jpl(far=True), PLACES, 83.0, 83.0))  # same places, but JPL's body is at 40 au
    assert out["state"] == "none"


def test_a_lead_without_exact_dates_is_matched_over_the_days_of_each_pass() -> None:
    legacy = [{k: v for k, v in p.items() if k != "mjd"} for p in PLACES]  # a result made before the dates were kept
    out = asyncio.run(fpx_known.identify(fake_jpl(), legacy, 83.0, 83.0))
    assert out["state"] == "ok" and max(out["seps"]) < 6.0  # one-day steps: the nearest day is within a few arcseconds


def test_jpl_down_is_unavailable_not_none() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="Proxy Error")

    out = asyncio.run(fpx_known.identify(httpx.AsyncClient(transport=httpx.MockTransport(boom)), PLACES, 83.0, 83.0))
    assert out == {"state": "unavailable"}
    json.dumps(out)
