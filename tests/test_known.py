from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient

from indexer.irsa import Row
from indexer.store import Index
from service import known as kn
from service.routes_known import make_known_router

SOE = (
    "header\n$$SOE\n"
    "2461000.500000, , ,269.45000,4.69000,9.511,3.1,1.8,0.1\n"
    "2461001.500000, , ,269.46000,4.70000,n.a.,3.1,1.8,0.1\n"
    "$$EOE\nfooter"
)


def handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "sbdb_query" in url:
        return httpx.Response(
            200,
            json={
                "count": 2,
                "fields": ["spkid", "full_name", "pdes", "H"],
                "data": [[2000433, "  433 Eros (A898 PA)", "433", 11.2], [2000004, "4 Vesta", "4", 3.2]],
            },
        )
    if "sbdb.api" in url:
        if "Halley" in url:
            return httpx.Response(300, json={"list": [{"pdes": "1P", "name": "Halley"}, {"pdes": "2688", "name": "Halley"}]})
        return httpx.Response(
            200,
            json={
                "object": {
                    "spkid": "2000433",
                    "fullname": "433 Eros (A898 PA)",
                    "des": "433",
                    "kind": "an",
                    "orbit_class": {"code": "AMO"},
                },
                "phys_par": [{"name": "H", "value": "10.4"}],
                "orbit": {"elements": [{"name": "a", "value": "1.46"}]},
            },
        )
    if "horizons" in url:
        return httpx.Response(200, json={"result": SOE})
    return httpx.Response(404)


def make(tmp_path: Path, index: Index | None = None) -> kn.Known:
    return kn.Known(tmp_path, index, httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def app_with(k: kn.Known | None) -> TestClient:
    app = FastAPI()
    app.include_router(make_known_router(SimpleNamespace(known=k)))
    return TestClient(app)


def test_catalogue_page_and_one_body(tmp_path: Path) -> None:
    c = app_with(make(tmp_path))
    page = c.get("/api/v1/known", params={"kind": "a", "per": 10}).json()
    assert page["count"] == 2 and page["rows"][0]["full_name"] == "433 Eros (A898 PA)" and page["source"].startswith("JPL")
    one = c.get("/api/v1/known", params={"q": "(433) Eros"}).json()
    assert one["rows"][0]["pdes"] == "433" and one["rows"][0]["H"] == "10.4" and one["rows"][0]["class"] == "AMO"
    amb = c.get("/api/v1/known", params={"q": "Halley"}).json()
    assert amb["count"] == 0 and {x["kind"] for x in amb["candidates"]} == {"comet", "asteroid"}


def test_ephem_keeps_the_order_asked_and_validates(tmp_path: Path) -> None:
    c = app_with(make(tmp_path))
    r = c.get("/api/v1/ephem", params={"target": "433 Eros", "jd": "2461001.5,2461000.5"}).json()
    assert [round(x["ra"], 2) for x in r["ephem"]] == [269.46, 269.45] and r["ephem"][1]["V"] == 9.511 and r["ephem"][0]["V"] is None
    assert r["ephem"][0]["delta_au"] == 1.8 and r["ephem"][0]["mag_kind"].startswith("V")
    assert c.get("/api/v1/ephem", params={"target": "433", "jd": "5"}).status_code == 400
    assert c.get("/api/v1/ephem", params={"target": "", "jd": "2461000.5"}).status_code in (400, 422)


def test_a_jpl_that_does_not_answer_is_an_error_not_an_empty_list(tmp_path: Path) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    k = kn.Known(tmp_path, None, httpx.AsyncClient(transport=httpx.MockTransport(down)))
    c = app_with(k)
    assert c.get("/api/v1/known", params={"kind": "a"}).status_code == 502
    assert c.get("/api/v1/ephem", params={"target": "433", "jd": "2461000.5"}).status_code == 502
    assert app_with(None).get("/api/v1/known").status_code == 503


def test_horizons_command_and_names() -> None:
    assert kn.horizons_command("433 Eros") == "433;" and kn.horizons_command("2024 YR4") == "DES=2024 YR4;"
    assert kn.horizons_command("C/2025 N1") == "DES=C/2025 N1;CAP;NOFRAG;" and kn.horizons_command("1P/Halley") == "DES=1P;CAP;NOFRAG;"
    assert kn.query_clean("(433) Eros") == "433 Eros" and kn.query_clean("’Oumuamua") == "1I"


def test_visits_are_runs_of_exposures_less_than_0_6_days_apart() -> None:
    assert kn.group_crossings([(10.0, 10.01), (10.02, 10.03), (11.0, 11.01), (10.3, 10.31)]) == [(10.0, 10.31), (11.0, 11.01)]
    assert kn.group_crossings([]) == []


def square(ra: float, dec: float, half: float) -> tuple[tuple[float, ...], tuple[float, ...]]:
    return (ra - half, ra + half, ra + half, ra - half), (dec - half, dec - half, dec + half, dec + half)


def test_crossings_against_a_small_index_and_the_index_range(tmp_path: Path) -> None:
    idx = Index(tmp_path / "x.sqlite")
    rows = []
    for k, (m0, det) in enumerate([(100.0, 1), (100.002, 2), (100.004, 3), (200.0, 1)]):
        ra, dec = square(50.0, 10.0, 1.0)
        rows.append(
            Row(
                key=f"k{k}",
                release="dr1",
                period="2025W20_1A",
                pipeline="l2b-v27",
                seq=k,
                group=1,
                det=det,
                mjd0=m0,
                mjd1=m0 + 0.001,
                size=1,
                md5="x",
                ra=ra,
                dec=dec,
            )
        )
    idx.add(rows)
    idx.commit()
    assert kn.index_mjd_range(idx) == [100.0, 200.001]
    k = make(tmp_path, idx)
    m = np.arange(98.0, 102.0, 0.125)
    ra = np.full_like(m, 50.0) + (m - 100.0) * 0.1
    dec = np.full_like(m, 10.0)
    out = k._cross(m, ra, dec)
    assert len(out) == 1 and out[0]["n"] == 3 and abs(out[0]["ra"] - 50.0) < 0.05 and out[0]["mjd0"] == 100.0
    assert k._cross(m[:3], ra[:3], dec[:3]) == []
    json.dumps(out)
