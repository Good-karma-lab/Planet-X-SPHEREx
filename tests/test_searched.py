from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.products.store import Store, product_key
from service.products.versions import VERSIONS
from service.routes_searched import make_searched_router
from service.searched import Searched


class Keyed:
    def __init__(self, root: Path, name: str) -> None:
        self.dir = root / name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.name = name
        self.runs: dict[str, Any] = {}

    def key_of(self, field_key: str) -> str:
        return f"{self.name}-{field_key}"

    def put(self, field_key: str, body: dict[str, Any]) -> None:
        (self.dir / f"{self.key_of(field_key)}.json").write_text(json.dumps(body))


def build(tmp_path: Path) -> tuple[Store, Keyed, Keyed, Keyed, Searched]:
    store = Store(tmp_path / "store")
    movers, pairs, dwarfs = Keyed(tmp_path, "movers"), Keyed(tmp_path, "pairs"), Keyed(tmp_path, "browndwarfs")
    sched = SimpleNamespace(jobs={})
    return store, movers, pairs, dwarfs, Searched(store, tmp_path, sched, movers, pairs, dwarfs)


def add_field(store: Store, ra: float, dec: float) -> str:
    params = {"ra": ra, "dec": dec, "fov": 5.0}
    key = product_key("field", params)
    store.begin("field", key, params)
    store.complete(key)
    return key


def test_a_searched_field_is_listed_with_its_best_lead(tmp_path: Path) -> None:
    store, movers, pairs, dwarfs, s = build(tmp_path)
    k = add_field(store, 133.79, -7.25)
    movers.put(k, {"tracks": [{"check": "none", "snr": [9.0, 11.5]}, {"check": "still", "snr": [30.0]}]})
    pairs.put(k, {"pairs": [{"x": 1}]})
    dwarfs.put(k, {"candidates": [{"ra": 1}], "known": [], "counts": {"known_brown_dwarfs": 0}})
    body = asyncio.run(s.get())
    assert body["count"] == 1
    f = body["fields"][0]
    assert f["state"] == "done" and f["kinds"] == ["passes"] and f["nothing"] is False
    assert f["found"] == 3  # one track (the still one is not drawn), one pair, one brown dwarf candidate
    assert f["best"]["label"] == "1 brown dwarf candidate"
    assert f["open"]["ra"] == 133.79 and f["open"]["pass"] is None


def test_a_field_with_nothing_found_says_so_and_an_unsearched_field_is_not_listed(tmp_path: Path) -> None:
    store, movers, _pairs, _dwarfs, s = build(tmp_path)
    k = add_field(store, 10.0, 20.0)
    add_field(store, 200.0, -30.0)  # looked at, never searched
    movers.put(k, {"tracks": []})
    body = asyncio.run(s.get())
    assert [f["nothing"] for f in body["fields"]] == [True]
    assert body["fields"][0]["best"] is None


def test_the_route_serves_the_list(tmp_path: Path) -> None:
    store, movers, _pairs, _dwarfs, s = build(tmp_path)
    k = add_field(store, 10.0, 20.0)
    movers.put(k, {"tracks": [{"check": "none", "snr": [8.0]}]})
    app = FastAPI()
    app.include_router(make_searched_router(SimpleNamespace(store=store, searched=s)))
    r = TestClient(app).get("/api/v1/searched")
    assert r.status_code == 200 and r.json()["count"] == 1
    assert "searched" in VERSIONS


def test_a_field_whose_only_brown_dwarf_is_a_known_one_is_not_empty(tmp_path: Path) -> None:
    store, movers, _pairs, dwarfs, s = build(tmp_path)
    k = add_field(store, 331.0, -56.8)
    movers.put(k, {"tracks": []})
    dwarfs.put(k, {"candidates": [], "known": [{"status": "known brown dwarf"}], "counts": {"known_brown_dwarfs": 1}})
    f = asyncio.run(s.get())["fields"][0]
    assert f["nothing"] is False and f["found"] == 1 and f["best"]["label"] == "1 known brown dwarf"


def test_a_smaller_field_made_later_at_the_same_place_does_not_shrink_it(tmp_path: Path) -> None:
    """The Findings header's area (the fields' sizes added) never goes down as fields are added: a 10′ quarter made after a 60′ field
    at the same place leaves the place 60′ (it went 124 fields / 14 sq deg -> 126 / 13)."""
    import time as _time

    store, movers, _pairs, _dwarfs, s = build(tmp_path)
    keys = []
    for fov in (60.0, 10.0):
        params = {"ra": 200.0, "dec": 10.0, "fov": fov}
        k = product_key("field", params)
        store.begin("field", k, params)
        store.complete(k)
        movers.put(k, {"tracks": []})
        keys.append(k)
        _time.sleep(0.01)  # the 10′ one is the newer
    body = asyncio.run(s.get())
    assert [f["fov"] for f in body["fields"]] == [60.0] and body["fields"][0]["open"]["fov"] == 60.0
