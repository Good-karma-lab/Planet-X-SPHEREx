"""A name the globe's snapshot lacks is asked of SIMBAD: the forms its identifier table holds, and the answer's shape."""

from __future__ import annotations

import asyncio
import json

import httpx

from service.context import CatalogueContext, name_variants


def test_greek_letters_and_prefixes_become_simbad_forms() -> None:
    v = name_variants("α Cen")
    assert "alf Cen" in v and "* alf Cen" in v and "NAME α Cen" in v
    assert "NAME Sirius" in name_variants("  Sirius ")
    assert "alf CMa" in name_variants("alpha CMa")
    assert name_variants("   ") == []


def test_resolve_answers_the_place_or_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        q = dict(request.url.params)["query"]
        rows = [["* alf CMa", 101.287155, -16.716116, -546.01, -1223.07, "SB*"]] if "'NAME Sirius'" in q else []
        return httpx.Response(200, text=json.dumps({"data": rows}))

    cc = CatalogueContext()
    cc.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    found = asyncio.run(cc.resolve("Sirius"))
    assert found is not None and found["name"] == "* alf CMa" and abs(found["ra"] - 101.287) < 1e-3
    assert asyncio.run(cc.resolve("No Such Star")) is None
