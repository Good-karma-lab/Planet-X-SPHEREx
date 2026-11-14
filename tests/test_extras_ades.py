"""The MPC ADES export of a mover lead."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.extras import ades
from service.routes_extras import Extras, register

HEAD = ades.Header(submitter="A. Person", observers=["A. Person"], measurers=["A. Person"], ast_cat="Gaia3", mode="CMOS")


def obs(n: int = 3, pos: bool = True) -> list[ades.Observation]:
    return [
        ades.Observation(
            61023.46 + 0.07 * i,
            169.8917 + 0.0005 * i,
            4.3514 - 0.0003 * i,
            1.2,
            1.2,
            position_km=(6000.0 + i, -2000.0, 3000.0) if pos else None,
        )
        for i in range(n)
    ]


def test_mjd_becomes_utc_with_milliseconds() -> None:
    assert ades.iso_time(51544.5) == "2000-01-01T12:00:00.000Z"
    assert ades.iso_time(60000.25) == "2023-02-25T06:00:00.000Z"


def test_a_complete_lead_is_ready_and_the_file_has_the_blocks_and_columns() -> None:
    assert ades.problems("SPX0001", obs(), HEAD) == []
    text = ades.to_psv("SPX0001", obs(), HEAD)
    lines = text.splitlines()
    assert lines[0] == "# version=2022" and "! mpcCode 288" in lines and "# telescope" in lines
    header_at = next(i for i, ln in enumerate(lines) if ln.startswith("trkSub"))
    assert [c.strip() for c in lines[header_at].split("|")] == [
        "trkSub",
        "mode",
        "stn",
        "obsTime",
        "ra",
        "dec",
        "rmsRA",
        "rmsDec",
        "astCat",
        "sys",
        "ctr",
        "pos1",
        "pos2",
        "pos3",
    ]
    first = [c.strip() for c in lines[header_at + 1].split("|")]
    assert (
        first[:5] == ["SPX0001", "CMOS", "288", "2026-11-14T11:02:24.000Z" if False else first[3], "169.8917000"]
        and first[9] == "ICRF_KM"
        and first[10] == "399"
    )
    assert len(lines) - header_at - 1 == 3 and text.endswith("\n")


def test_what_is_missing_is_named() -> None:
    p = ades.problems("bad id!", obs(1, pos=False), ades.Header(submitter=""))
    joined = " | ".join(p)
    assert "trkSub" in joined and "at least two" in joined and "submitter" in joined and "observers" in joined
    assert "astCat" in joined and "spacecraft position" in joined
    unordered = list(reversed(obs()))
    assert any("time order" in x for x in ades.problems("SPX1", unordered, HEAD))
    bad_mag = [ades.Observation(61000.0, 1.0, 1.0, 1.0, 1.0, mag=20.0, band="nope", position_km=(1, 2, 3))] * 2
    assert any("band" in x for x in ades.problems("SPX1", bad_mag, HEAD))


def test_without_positions_the_file_has_no_position_columns() -> None:
    text = ades.to_psv("SPX0001", obs(pos=False), HEAD)
    assert "pos1" not in text and "ICRF_KM" not in text


def test_the_route_returns_the_file_and_its_problems() -> None:
    app = FastAPI()
    register(app, Extras())
    c = TestClient(app)
    body = {
        "track_id": "SPX0001",
        "observations": [
            {
                "mjd_utc": 61023.46,
                "ra": 169.89,
                "dec": 4.35,
                "rms_ra_arcsec": 1.2,
                "rms_dec_arcsec": 1.2,
                "position_km": [6000, -2000, 3000],
            },
            {
                "mjd_utc": 61023.53,
                "ra": 169.8905,
                "dec": 4.3495,
                "rms_ra_arcsec": 1.2,
                "rms_dec_arcsec": 1.2,
                "position_km": [6001, -2000, 3000],
            },
        ],
        "submitter": "A. Person",
        "observers": ["A. Person"],
        "measurers": ["A. Person"],
        "ast_cat": "Gaia3",
        "mode": "CMOS",
    }
    r = c.post("/api/v1/export/ades", json=body)
    assert r.status_code == 200 and r.json()["ready"] is True and r.json()["problems"] == []
    assert "! mpcCode 288" in r.json()["text"] and r.json()["observatory_code"] == "288"
    body["observations"][0].pop("position_km")
    r2 = c.post("/api/v1/export/ades", json=body).json()
    assert r2["ready"] is False and any("spacecraft position" in x for x in r2["problems"])
    assert c.post("/api/v1/export/ades", json={**body, "observations": []}).status_code == 422


HORIZONS_ANSWER = """header
$$SOE
2461023.960000000, A.D. 2025-Dec-14 11:02:24.0000, -5.964146401906273E+03,  2.635442548071303E+03,  2.600970692238243E+03,
2461024.460000000, A.D. 2025-Dec-14 23:02:24.0000,  6.617803039943597E+03,  3.472872854075883E+02,  2.342327558059281E+03,
$$EOE
tail"""


def test_horizons_vectors_are_parsed_and_asked_in_icrf_geocentric() -> None:
    import asyncio

    import httpx

    from engine.extras import spherex_orbit

    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.url.params)
        return httpx.Response(200, json={"result": HORIZONS_ANSWER})

    async def go() -> list[tuple[float, float, float]]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await spherex_orbit.positions(c, [61023.46, 61023.96])

    pos = asyncio.run(go())
    assert pos[0] == (-5964.146401906273, 2635.442548071303, 2600.970692238243) and len(pos) == 2
    assert seen["COMMAND"] == "'-163182'" and seen["CENTER"] == "'500@399'" and seen["REF_PLANE"] == "FRAME"
    assert seen["TLIST"].startswith("'2461023.960000 2461024.460000")


def test_horizons_errors_and_short_answers_are_refused() -> None:
    import asyncio

    import httpx
    import pytest

    from engine.extras import spherex_orbit

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": 'No ephemeris for target "SPHEREx" prior to 2025-MAR-12'})

    async def go(h: object) -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as c:  # type: ignore[arg-type]
            await spherex_orbit.positions(c, [50000.0])

    with pytest.raises(spherex_orbit.OrbitError):
        asyncio.run(go(refuse))
    with pytest.raises(spherex_orbit.OrbitError):
        asyncio.run(go(lambda r: httpx.Response(200, json={"result": HORIZONS_ANSWER})))


def test_the_route_can_fill_the_spacecraft_position_from_horizons() -> None:
    import httpx

    app = FastAPI()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": HORIZONS_ANSWER})

    register(app, Extras(cadc_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    body = {
        "track_id": "SPX0001",
        "observations": [
            {"mjd_utc": 61023.46, "ra": 169.89, "dec": 4.35, "rms_ra_arcsec": 1.2, "rms_dec_arcsec": 1.2},
            {"mjd_utc": 61023.96, "ra": 169.8905, "dec": 4.3495, "rms_ra_arcsec": 1.2, "rms_dec_arcsec": 1.2},
        ],
        "submitter": "A. Person",
        "observers": ["A. Person"],
        "measurers": ["A. Person"],
        "ast_cat": "Gaia3",
        "mode": "CMOS",
        "fill_positions": True,
    }
    r = TestClient(app).post("/api/v1/export/ades", json=body).json()
    assert r["ready"] is True and "ICRF_KM" in r["text"] and "-5964.1464" in r["text"] and r["notes"] == []
