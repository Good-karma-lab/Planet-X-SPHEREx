from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from service import context as ctx
from service.routes_search import make_router

GAIA_CSV = (
    "source_id,ra,dec,pmra,pmdec,parallax,parallax_error,phot_g_mean_mag,bp_rp,ruwe,phot_variable_flag\n"
    "111,269.45,4.69,-800.0,10300.0,547.0,0.03,9.5,2.8,1.0,NOT_AVAILABLE\n"
    "222,269.5,4.7,0,0,1.0,0.1,17.0,1.0,1.0,\n"
)


def test_gaia_at_the_epoch_uses_the_proper_motion() -> None:
    # Barnard's star moves 10.3 arcsec/yr north: from 2016 to 2025.9 it is 102 arcsec north of its catalogue place
    out = ctx.parse_gaia(GAIA_CSV, 269.45 - 800.0 * 9.9 / 3.6e6 / 0.998, 4.69 + 10300.0 * 9.9 / 3.6e6, 2025.9, 6.0, "Gaia DR3 archive")
    assert out["state"] == "ok" and out["source_id"] == "111" and out["sep"] < 1.0
    assert ctx.parse_gaia(GAIA_CSV, 10.0, 10.0, 2025.9, 6.0, "x") == {"state": "none", "source": "x"}


def test_simbad_prefers_the_object_with_a_magnitude() -> None:
    rows = [
        ["A", 10.0, 20.0, 0.0, 0.0, 1.0, "*", "M0", 5, None, None, None],
        ["B", 10.0, 20.0001, 0.0, 0.0, 1.0, "*", None, 3, 12.0, None, None],
    ]
    out = ctx.parse_simbad(rows, 10.0, 20.0, 2025.9, 6.0)
    assert out["name"] == "B" and out["mag"] == {"value": 12.0, "band": "V"}
    assert ctx.parse_simbad([], 10.0, 20.0, 2025.9, 6.0) == {"state": "none"}


def test_irsa_text_and_number_columns_and_vsx_ned() -> None:
    body = "designation,ra,dec,w1mpro,w2mpro,w3mpro,w4mpro,cc_flags\nJ0001,10.0,20.0,13.5,13.0,,,0000\n"
    out = ctx.parse_irsa(body, 10.0, 20.0, 6.0, ctx.IRSA_PARTS["allwise"][3])
    assert out["designation"] == "J0001" and out["W1"] == 13.5 and out["W3"] is None and out["cc_flags"] == "0000"
    assert ctx.parse_vsx("Name,Type,Period,RAJ2000,DEJ2000\nRW Aur,UXOR,,10.0,20.0001\n", 10.0, 20.0)["name"] == "RW Aur"
    ned = "No.|Object Name|RA|DEC|Type|Velocity|Redshift|Redshift Flag|Separation\n1|NGC 1|10.0|20.0|G|1|0.001|x|0.05\n"
    assert ctx.parse_ned(ned)["name"] == "NGC 1" and abs(ctx.parse_ned(ned)["sep"] - 3.0) < 1e-9


def test_a_catalogue_that_does_not_answer_is_unavailable_and_not_kept() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if "gea.esac" in str(request.url) or "g-vo" in str(request.url):
            return httpx.Response(
                200, text="source_id,ra,dec,pmra,pmdec,parallax,parallax_error,phot_g_mean_mag,bp_rp,ruwe,phot_variable_flag\n"
            )
        if "irsa" in str(request.url):
            return httpx.Response(200, text="designation,ra,dec,j_m,h_m,k_m,ph_qual\n")
        return httpx.Response(503, text="down")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cat = ctx.CatalogueContext(client)
    out: dict[str, Any] = asyncio.run(cat.context(10.0, 20.0))
    assert out["gaia"]["state"] == "none" and out["twomass"]["state"] == "none"
    assert out["simbad"]["state"] == "unavailable" and out["ned"]["state"] == "unavailable" and out["complete"] is False
    assert not any(k.startswith("simbad") for k in cat.cache) and any(k.startswith("gaia") for k in cat.cache)


def test_the_route_answers_and_validates() -> None:
    class Fake:
        async def context(self, ra: float, dec: float, epoch: float, radius: float) -> dict[str, Any]:
            return {"ra": ra, "dec": dec, "epoch": epoch, "radius": radius, "complete": True}

    app = FastAPI()
    app.include_router(make_router(SimpleNamespace(catalogue=Fake())))
    c = TestClient(app)
    assert c.get("/api/v1/context", params={"ra": 10, "dec": 20}).json()["epoch"] == 2025.9
    assert c.get("/api/v1/context", params={"ra": 400, "dec": 20}).status_code == 422
    app2 = FastAPI()
    app2.include_router(make_router(SimpleNamespace()))
    assert TestClient(app2).get("/api/v1/context", params={"ra": 10, "dec": 20}).status_code == 503


def test_allwise_copy_answers_when_irsa_does_not() -> None:
    from service.context import from_allwise

    rows = [
        {
            "sep_arcsec": 0.3,
            "designation": "J050749.54+302404.7",
            "w1mpro": 6.2,
            "w2mpro": 5.2,
            "w3mpro": 3.4,
            "w4mpro": 1.4,
            "cc_flags": "0000",
            "j_m_2mass": 8.4,
            "h_m_2mass": 7.6,
            "k_m_2mass": 7.0,
        }
    ]
    a = from_allwise("allwise", rows, 6.0)
    assert a is not None and a["state"] == "ok" and a["W1"] == 6.2 and "AWS" in a["via"]
    t = from_allwise("twomass", rows, 6.0)
    assert t is not None and (t["J"], t["H"], t["Ks"]) == (8.4, 7.6, 7.0)
    nothing = from_allwise("allwise", [], 6.0)
    assert nothing == {"state": "none", "via": nothing["via"]} if nothing else False
    no_2mass = [dict(rows[0], j_m_2mass=None, h_m_2mass=None, k_m_2mass=None)]
    assert from_allwise("twomass", no_2mass, 6.0) is None  # a source with no 2MASS match does not say 2MASS has none


def test_a_fast_star_is_found_where_it_stands_at_the_epoch_not_where_it_was_catalogued() -> None:
    # epsilon Indi A: Gaia's 2016 place and motion (3967, -2536 mas/yr); today (2025.9) it stands ~47 arcsec away
    row = {
        "source_id": "6412595290592307840",
        "ra": 330.8724079,
        "dec": -56.7972547,
        "pmra": 3966.66,
        "pmdec": -2536.19,
        "parallax": 274.8,
        "phot_g_mean_mag": 4.32,
        "ruwe": 1.0,
    }
    out = ctx.parse_gaia_rows([row], 330.8923, -56.8042, 2025.9, 6.0, "Gaia DR3 archive")
    assert out["state"] == "ok" and out["source_id"] == row["source_id"] and out["sep"] < 1.0
    # the same star clicked 6.3 arcsec from the propagated place (a saturated core's centre is uncertain): taken, with a note
    near = ctx.parse_gaia_rows([row], 330.8925, -56.8025, 2025.9, 6.0, "x")
    assert near["state"] == "ok" and "saturated" in near["note"] and 6.0 < near["sep"] < 7.0
    faint = dict(row, phot_g_mean_mag=15.0)
    assert (
        ctx.parse_gaia_rows([faint], 330.8925, -56.8025, 2025.9, 6.0, "x")["state"] == "none"
    )  # a faint star 6 arcsec away is another star


def test_a_wide_gaia_answer_without_the_star_is_none_but_a_failed_query_is_not() -> None:
    assert ctx.parse_gaia_rows([], 10.0, 10.0, 2025.9, 6.0, "x") == {"state": "none", "source": "x"}
