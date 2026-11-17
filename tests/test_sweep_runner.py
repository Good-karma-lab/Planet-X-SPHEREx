"""The background sweep inside the service (service/sweep_runner.py): people first, the stock first, the disk and the archive
respected; a square is built, searched, written to the grid and stripped of its pictures (its row stays for Findings)."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from service import grid, sweep_runner
from service.grid import GridDB
from service.jobs.scheduler import Priority
from service.products.store import Store
from service.workers import fieldjob


class _Sch:
    def __init__(self) -> None:
        self.waiting = False
        self.active = False

    def person_waiting(self) -> bool:
        return self.waiting

    def person_active(self) -> bool:
        return self.active


def make(
    tmp_path: Path, passes: int = 3, fail: bool = False, ids: list[str] | None = None
) -> tuple[sweep_runner.SweepRunner, Any, list[Any]]:
    store = Store(tmp_path / "store")
    db = GridDB(tmp_path / "product.sqlite")
    asked: list[Any] = []

    def submit(params: dict[str, Any], priority: Priority) -> tuple[str, Any]:
        key = "k" + str(len(asked)).zfill(23)
        asked.append((params, priority))
        if not fail:
            store.begin("field", key, params)
            store.write_part("field", key, "picture_p0.sxp", b"x" * 1000)
            store.write_meta("field", key, {"params": params, "passes": [{"index": i} for i in range(passes)]})
            store.complete(key)
        return key, SimpleNamespace(state="error" if fail else "done", error="no exposures" if fail else None)

    st = SimpleNamespace(
        scheduler=_Sch(), index=object(), submit_field=submit, store=store, grid=db, quarters=None,
        cfg=SimpleNamespace(store_dir=tmp_path), files=None,
    )  # fmt: skip
    searched: list[str] = []

    async def searches(key: str, ce: grid.Cell) -> dict[str, Any]:
        searched.append(key)
        got = ["mover:aaa111", "mover:aaa111", "browndwarf:bbb222"] if ids is None else ids
        return {"find_ids": got, "bar": {"anomalies": 2}}

    planetx_asked: list[str] = []

    async def planetx(ce: grid.Cell, key: str) -> dict[str, Any]:
        planetx_asked.append(ce.id)
        return {"30-300au D123": 1}

    r = sweep_runner.SweepRunner(st, searches, planetx)
    r.planetx_asked = planetx_asked  # type: ignore[attr-defined]
    r.searched = searched  # type: ignore[attr-defined]
    return r, st, asked


def test_people_and_the_stock_come_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r, st, _ = make(tmp_path)
    assert r.gate() is None
    st.scheduler.active = True
    assert r.gate() == "people are using the site"
    st.scheduler.active, st.scheduler.waiting = False, True
    assert r.gate() == "people are using the site"
    st.scheduler.waiting = False
    st.quarters = SimpleNamespace(building=lambda: 1)
    assert r.gate() == "the quarter stock goes first"
    st.quarters = None
    monkeypatch.setattr(sweep_runner, "disk_used_pct", lambda p: 85.0)
    assert r.gate() == "the disk is 80% full"
    monkeypatch.setattr(sweep_runner, "disk_used_pct", lambda p: 50.0)
    r._backoff_until = 1e18
    assert r.gate() == "the archive had errors: waiting"
    r._backoff_until, r.enabled = 0.0, False
    assert r.gate() == "turned off (SKY_SWEEP=0)"


def test_a_square_is_built_searched_written_and_stripped(tmp_path: Path) -> None:
    r, st, asked = make(tmp_path, ids=["red:ccc333", "anomaly:ddd444"])  # finds, but no mover or brown dwarf
    ce = r.next_square()
    assert ce is not None and ce.id == next(iter(grid.sweep_order())).id  # the Planet Nine band's centre first
    got = asyncio.run(r.square(ce))
    params, prio = asked[0]
    assert prio == Priority.P2 and params["fov"] == float(grid.FIELD_FOV_ARCMIN)
    row = st.grid.get(ce.id)
    assert row["state"] == "searched" and row["finds"] == 2 and json.loads(row["find_ids"]) == ["red:ccc333", "anomaly:ddd444"]
    bar = json.loads(row["bar"])
    assert bar["anomalies"] == 2 and row["source"] == "sweep"
    # a square of the Planet Nine band gets Find Planet X the same day, said in its bar
    assert r.planetx_asked == [ce.id] and bar["planetx"] == {"30-300au D123": 1}
    key = row["field_key"]
    # the pictures are gone; the row and the meta stay (Findings reads the searches' results through them) and say stripped
    assert got["freed"] == 1000 and st.store.is_complete(key) and not (st.store.path("field", key) / "picture_p0.sxp").exists()
    meta = st.store.read_meta("field", key)
    assert meta["stripped"] is True and fieldjob.unread(meta)  # asked for again, it is made again
    assert r.next_square().id != ce.id  # a searched square is never taken again


def test_a_square_with_a_mover_keeps_its_field_a_week(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r, st, _ = make(tmp_path)  # a mover and a brown dwarf
    ce = r.next_square()
    got = asyncio.run(r.square(ce))
    row = st.grid.get(ce.id)
    key = row["field_key"]
    assert got.get("kept") and row["finds"] == 2 and (st.store.path("field", key) / "picture_p0.sxp").exists()
    assert json.loads(row["bar"])["keep_until"] > 0
    assert r.strip_expired() == 0  # not yet
    now = time.time()
    monkeypatch.setattr(sweep_runner.time, "time", lambda: now + 8 * 86400)
    assert r.strip_expired() == 1000 and not (st.store.path("field", key) / "picture_p0.sxp").exists()
    assert "keep_until" not in json.loads(st.grid.get(ce.id)["bar"])


def test_a_square_with_fewer_than_two_passes_and_a_failed_field(tmp_path: Path) -> None:
    r, st, _ = make(tmp_path, passes=1)
    ce = r.next_square()
    asyncio.run(r.square(ce))
    row = st.grid.get(ce.id)
    assert row["state"] == "searched" and row["finds"] == 0 and "fewer than two passes" in json.loads(row["bar"])["note"]
    assert r.searched == []  # nothing to search
    r2, st2, _ = make(tmp_path / "b", fail=True)
    ce2 = r2.next_square()
    got = asyncio.run(r2.square(ce2))
    assert got["state"] == "failed" and st2.grid.get(ce2.id)["state"] == "failed"


def test_strip_keeps_a_pinned_product(tmp_path: Path) -> None:
    st = Store(tmp_path)
    st.begin("field", "p" * 24, {"ra": 1.0, "dec": 2.0, "fov": 20.0})
    st.write_part("field", "p" * 24, "picture_p0.sxp", b"x" * 10)
    st.write_meta("field", "p" * 24, {"passes": [{}]})
    st.complete("p" * 24)
    st.pin("p" * 24)
    assert st.strip("field", "p" * 24) == 0 and (st.path("field", "p" * 24) / "picture_p0.sxp").exists()
