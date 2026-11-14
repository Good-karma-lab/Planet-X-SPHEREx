"""Archival images of a known orbit from CADC's SSOIS."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.extras import ssois
from service.routes_extras import Extras, register

TSV = (
    "Image\tExt\tX\tY\tMJD\tFilter\tExptime\tObject_RA\tObject_Dec\tImage_target\tTelescope/Instrument\tDatalink\n"
    "2025.A.00004.S A001\t-9999\t-9999\t-9999\t61025.889265\tBand-7\t2873\t169.96\t4.41\tATLAS\tALMA\t\n"
    "2216a\t1\t1000\t2000\t60500.25\tr.MP9602\t480\t170.1\t4.5\tCFHTLS\tCFHT/MegaCam\thttps://x/d1\n"
    "1999b\t1\t10\t20\t51500.5\tV\t300\t170.0\t4.4\tFIELD\tPanSTARRS/GPC1\t\n"
)


def test_a_name_search_has_the_forms_fields() -> None:
    q = ssois.name_query("9078", "2025-12-14", "2025-12-20")
    assert q["search"] == "bynameCADC" and q["object"] == "9078" and q["epoch1"] == "2025 12 14" and q["epoch2"] == "2025 12 20"
    with pytest.raises(ValueError):
        ssois.name_query("9078;rm -rf", "2025-12-14", "2025-12-20")
    with pytest.raises(ValueError):
        ssois.name_query("9078", "2025-12-20", "2025-12-14")


def test_an_orbit_search_checks_the_elements() -> None:
    el = ssois.Elements("2025-12-01", 2.6, 0.1, 5.0, 100.0, 50.0, 10.0)
    q = ssois.orbit_query(el, "2025-12-01", "2025-12-10")
    assert (
        q["search"] == "orbel"
        and q["epoch"] == "2025-12-01"
        and q["a"] == "2.600000"
        and q["longi"] == "100.00000"
        and q["anom"] == "10.00000"
    )
    with pytest.raises(ValueError):
        ssois.orbit_query(ssois.Elements("2025-12-01", 2.6, 1.5, 5.0, 100.0, 50.0, 10.0), "2025-12-01", "2025-12-10")


def test_the_table_is_parsed_earliest_first_and_radio_footprints_are_left_out() -> None:
    rows = ssois.parse(TSV)
    assert [r["instrument"] for r in rows] == ["PanSTARRS/GPC1", "CFHT/MegaCam"] and rows[1]["datalink"] == "https://x/d1"
    assert len(ssois.parse(TSV, include_radio=True)) == 3
    with pytest.raises(ssois.SsoisError):
        ssois.parse("<html>error</html>")


def test_the_human_link_carries_the_same_search() -> None:
    link = ssois.human_link(ssois.name_query("9078", "2025-12-14", "2025-12-20"))
    assert (
        link.startswith("https://www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/en/ssois/results.html?")
        and "object=9078" in link
        and "epoch1=2025+12+14" in link
    )


def client() -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["format"] == "tsv"
        return httpx.Response(200, text=TSV)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_search_and_the_routes() -> None:
    async def go() -> int:
        async with client() as c:
            return len(await ssois.search(c, ssois.name_query("9078", "2025-12-14", "2025-12-20")))

    assert asyncio.run(go()) == 2
    app = FastAPI()
    register(app, Extras(cadc_client=client()))
    c = TestClient(app)
    r = c.get("/api/v1/ssois", params={"name": "9078", "start": "2025-12-14", "end": "2025-12-20"}).json()
    assert r["state"] == "ok" and r["n"] == 2 and r["link"].endswith("epoch2=2025+12+20&eellipse=&eunits=arcseconds&extres=yes&xyres=yes")
    o = c.get(
        "/api/v1/ssois/orbit",
        params={
            "epoch": "2025-12-01",
            "a": 2.6,
            "e": 0.1,
            "i": 5,
            "node": 100,
            "peri": 50,
            "anom": 10,
            "start": "2025-12-01",
            "end": "2025-12-10",
        },
    ).json()
    assert o["state"] == "ok" and o["rows"][0]["instrument"] == "PanSTARRS/GPC1"
    assert c.get("/api/v1/ssois", params={"name": "a;b", "start": "2025-12-14", "end": "2025-12-20"}).status_code == 422
    assert (
        c.get(
            "/api/v1/ssois/orbit",
            params={"epoch": "x", "a": 2, "e": 0.1, "i": 5, "node": 1, "peri": 1, "anom": 1, "start": "2025-12-01", "end": "2025-12-10"},
        ).status_code
        == 422
    )


@pytest.mark.network
def test_live_the_search_answers_with_a_table() -> None:
    async def go() -> None:
        async with httpx.AsyncClient(timeout=60, headers={"User-Agent": "spherex-sky-test"}) as c:
            rows = await ssois.search(c, ssois.name_query("9078", "2025-12-14", "2025-12-20"))
            assert isinstance(rows, list)

    asyncio.run(go())
