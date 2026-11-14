"""The R Draconis deep-sequence regression field: the committed snapshot, the SIA parser, the index comparison."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.extras import regression_field
from service.routes_extras import Extras, register


def _row(det: str, mjd: str, key: str) -> str:
    cloud = '{"aws": {"bucket_name": "b", "key":"' + key + '", "region": "us-east-1"}}' if key else ""
    return f"<TR><TD>2026W30_1B_0199_3</TD><TD>SPHEREx-{det}</TD><TD>{mjd}</TD><TD>{cloud}</TD></TR>"


VOT = (
    '<?xml version="1.0"?><VOTABLE version="1.3" xmlns="http://www.ivoa.net/xml/VOTable/v1.3"><RESOURCE type="results"><TABLE>'
    '<FIELD name="obs_id" datatype="char" arraysize="*"/><FIELD name="energy_bandpassname" datatype="char" arraysize="*"/>'
    '<FIELD name="t_min" datatype="double"/><FIELD name="cloud_access" datatype="char" arraysize="*"/><DATA><TABLEDATA>'
    + _row("D4", "61242.081127", "qr3/level2/p/v/4/a.fits")
    + _row("D1", "61242.0811", "qr3/level2/p/v/1/b.fits")
    + _row("D2", "61243.0", "")
    + "</TABLEDATA></DATA></TABLE></RESOURCE></VOTABLE>"
)


def test_the_committed_snapshot_is_the_deep_sequence_over_r_dra() -> None:
    snap = regression_field.load()
    assert snap["field"]["name"] == "R Draconis" and set(snap["collections"]) == set(regression_field.COLLECTIONS)
    n = sum(len(v) for v in snap["collections"].values())
    assert n > 1500  # the pole is observed over and over
    for rows in snap["collections"].values():
        keys = [r[0] for r in rows]
        assert len(set(keys)) == len(keys) and all(k.endswith(".fits") and "/level2/" in k for k in keys)
        assert {r[1] for r in rows} <= set(range(1, 7)) and [r[2] for r in rows] == sorted(r[2] for r in rows)
        assert all(60000 < r[2] < 62000 for r in rows)


def test_the_sia_answer_is_parsed_and_rows_without_a_file_are_dropped() -> None:
    rows = regression_field.parse_sia(VOT.encode())
    assert [(r["det"], r["key"]) for r in rows] == [(4, "qr3/level2/p/v/4/a.fits"), (1, "qr3/level2/p/v/1/b.fits")]
    assert rows[0]["mjd"] == pytest.approx(61242.08113, abs=1e-5)


def test_compare_names_the_files_the_index_lacks() -> None:
    snap = {"collections": {"c": [["a.fits", 1, 61000.0], ["b.fits", 2, 61001.0], ["c.fits", 2, 61002.0]]}, "taken": "t", "field": {}}
    r = regression_field.compare(snap, ["a.fits", "c.fits", "zzz.fits"])
    assert r["ok"] is False and r["missing"] == ["b.fits"] and r["collections"]["c"]["in_index"] == 2
    assert r["collections"]["c"]["detectors"]["2"] == 2 and r["collections"]["c"]["first_mjd"] == 61000.0
    assert regression_field.compare(snap, ["a.fits", "b.fits", "c.fits"])["ok"] is True


@dataclass
class Exp:
    key: str


class FakeIndex:
    def __init__(self, keys: list[str]) -> None:
        self.keys = keys

    def overlapping(self, ra: float, dec: float, radius: float) -> list[Exp]:
        assert abs(ra - 248.1676) < 1e-3 and abs(dec - 66.755) < 1e-3
        return [Exp(k) for k in self.keys]


def test_the_route_compares_the_index_with_the_snapshot() -> None:
    snap = regression_field.load()
    all_keys = [r[0] for rows in snap["collections"].values() for r in rows]
    app = FastAPI()

    class Sky:
        index = FakeIndex(all_keys[:-3])

    app.state.sky = Sky()
    register(app, Extras())
    body = TestClient(app).get("/api/v1/regression/r-dra").json()
    assert body["ok"] is False and len(body["missing"]) == 3 and body["field"]["name"] == "R Draconis"
    Sky.index = FakeIndex(all_keys)
    assert TestClient(app).get("/api/v1/regression/r-dra").json()["ok"] is True
    app2 = FastAPI()
    register(app2, Extras())
    assert TestClient(app2).get("/api/v1/regression/r-dra").status_code == 503


@pytest.mark.network
def test_live_irsa_still_lists_at_least_the_snapshot() -> None:
    async def go() -> dict[str, list[dict[str, object]]]:
        async with httpx.AsyncClient(timeout=180) as c:
            return await regression_field.fetch(c)

    live = asyncio.run(go())
    snap = regression_field.load()
    for coll, rows in snap["collections"].items():
        assert {r[0] for r in rows} <= {e["key"] for e in live[coll]}, json.dumps(coll)
