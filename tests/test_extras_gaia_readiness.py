"""The Gaia DR4 readiness probe."""

from __future__ import annotations

import asyncio

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.extras import gaia_readiness
from service.routes_extras import Extras, register


def client(schemas: str, columns: str, listing: str = '<a href="gaia_dr3/">') -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if "lsdb" in str(request.url):
            return httpx.Response(200, text=listing)
        q = request.content.decode()
        return httpx.Response(200, text=columns if "TAP_SCHEMA.columns" in q.replace("%2E", ".") else schemas)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def run(c: httpx.AsyncClient, today: str) -> dict[str, object]:
    async def go() -> dict[str, object]:
        async with c:
            return await gaia_readiness.probe(c, today)

    return asyncio.run(go())


def test_before_the_release_nothing_is_ready() -> None:
    r = run(client("schema_name\npublic\ngaiadr3\n", ""), "2026-11-14")
    assert r["released"] is False and r["schema"] is False and r["ready"] is False and r["hats"] == []
    assert r["settings"] == {"SKY_GAIA_TABLE": "gaiadr4.gaia_source", "SKY_GAIA_EPOCH": "2017.5", "SKY_GAIA_NAME": "Gaia DR4"}


def test_with_the_schema_and_all_columns_it_is_ready() -> None:
    cols = "column_name\n" + "\n".join(gaia_readiness.NEEDED) + "\n"
    r = run(client("schema_name\ngaiadr3\ngaiadr4\n", cols, '<a href="gaia_dr4/">'), "2026-12-03")
    assert r["released"] is True and r["ready"] is True and r["columns_missing"] == [] and r["hats"] == ["gaia_dr4"]


def test_a_missing_column_blocks_the_switch_and_names_it() -> None:
    cols = "column_name\n" + "\n".join(c for c in gaia_readiness.NEEDED if c != "ruwe") + "\n"
    r = run(client("schema_name\ngaiadr4\n", cols), "2026-12-03")
    assert r["ready"] is False and r["columns_missing"] == ["ruwe"]


def test_an_error_document_is_reported_not_swallowed() -> None:
    r = run(client("<VOTABLE>error</VOTABLE>", ""), "2026-11-14")
    assert r["schema"] is None and "tap_error" in r and r["ready"] is False


def test_the_route_answers() -> None:
    app = FastAPI()
    register(app, Extras(cadc_client=client("schema_name\ngaiadr3\n", "")))
    body = TestClient(app).get("/api/v1/gaia/readiness").json()
    assert body["ready"] is False and body["planned"] == "2026-12-02" and "settings" in body
