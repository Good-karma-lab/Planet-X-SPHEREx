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


def test_the_archive_backoff_waits_for_given_up_reads_or_a_burst(tmp_path: Path) -> None:
    r, st, _ = make(tmp_path)
    stats = SimpleNamespace(failures=0, retries=0)
    st.files = SimpleNamespace(s3=SimpleNamespace(stats=stats))
    before = r._s3_now()
    stats.failures, stats.retries = 3, 3  # three reads failed once and then worked: normal
    assert not r._archive_troubled(before)
    stats.failures = 4  # one read gave up
    assert r._archive_troubled(before)
    stats.failures, stats.retries = 3 + sweep_runner.RETRY_BURST, 3 + sweep_runner.RETRY_BURST  # a burst of retries
    assert r._archive_troubled(before)


def test_a_top_finds_lab_field_is_built_before_the_next_square(tmp_path: Path) -> None:
    r, _st, asked = make(tmp_path)
    want = [{"ra": 60.71158, "dec": 8.617, "fov": 60.0}, {"ra": 10.0, "dec": 5.0, "fov": 10.0}]
    reads: list[int] = []

    def lab_fields() -> list[dict[str, Any]]:
        reads.append(1)
        return list(want)

    r.lab_fields = lab_fields
    first = r.next_lab_field()
    assert first == want[0]
    got = asyncio.run(r.lab_field(first))
    assert got["state"] == "warmed" and asked[-1] == (want[0], Priority.P2) and r.status["lab_fields_today"] == 1
    assert r.next_lab_field() == want[1]
    assert r.next_lab_field() is None and len(reads) == 1  # the list is read again only every LAB_STOCK_EVERY_S


def test_the_top_finds_fields_are_the_labs_and_built_ones_are_left(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from service import findings
    from service.products.store import product_key

    finds = [
        {"kind": "film", "ra": 60.6089, "dec": 8.3985, "data": {"field_params": {"ra": 60.71158333, "dec": 8.617, "fov": 60.0}}},
        {"kind": "film", "ra": 1.0, "dec": 1.0, "data": {}},  # an old film without its field: left
        {"kind": "mover", "ra": 120.0, "dec": -30.0, "data": {"field_params": {"ra": 120.1, "dec": -30.1, "fov": 20.0}}},
        {"kind": "browndwarf", "ra": 200.0, "dec": 10.0, "data": {}},
    ]
    asked: list[dict[str, Any]] = []

    def query(db: Any, kind: Any, status: Any, place: Any, limit: int, offset: int, **k: Any) -> dict[str, Any]:
        asked.append({"limit": limit, **k})
        return {"finds": finds}

    monkeypatch.setattr(findings, "query", query)
    monkeypatch.setattr(fieldjob, "exposure_signature", lambda index, params: "sig")
    store = Store(tmp_path / "store")
    st = SimpleNamespace(findings=SimpleNamespace(db=object()), findings_flow=object(), index=object(), store=store)
    got = sweep_runner.top_find_fields(st)
    film = fieldjob.canonical(60.71158333, 8.617, 60.0)
    assert got == [
        film,
        fieldjob.canonical(120.0, -30.0, 10.0),
        fieldjob.canonical(200.0, 10.0, 10.0),
    ]  # a film on its field; others 10′ on the object
    assert asked[0]["limit"] == sweep_runner.LAB_STOCK and asked[0]["sort"] == "promise"
    key = product_key(fieldjob.KIND, film, extra=("sig",))
    store.begin("field", key, film)
    store.write_meta("field", key, {"passes": [{}, {}]})
    store.complete(key)
    assert film not in sweep_runner.top_find_fields(st)  # built: left
    # a mover with a long track: its task opens at the field that holds the whole track (20′), so that one is kept built too
    long_track = {"kind": "mover", "ra": 120.0, "dec": -30.0, "id": "mover:long",
                  "data": {"track": {"ra": [120.0, 120.0, 120.0], "dec": [-30.1, -30.0, -29.9]}}}  # fmt: skip
    finds[:] = [long_track]
    got = sweep_runner.top_find_fields(st)
    assert fieldjob.canonical(120.0, -30.0, 10.0) in got and any(p["fov"] == 20.0 for p in got)


def test_the_step_said_is_this_squares(tmp_path: Path) -> None:
    r, _st, _ = make(tmp_path)
    r._say("searching", square="a", step="Find Planet X")
    r._say("idle", last="a", step=None)
    assert r.snapshot()["step"] is None
    ce = r.next_square()
    asyncio.run(r.square(ce))
    assert r.snapshot()["step"] in ("the searches", "Find Planet X")


def test_a_start_leaves_the_stripped_fields_complete(tmp_path: Path) -> None:
    """At start the black fields (nothing read) are reopened; the fields the sweep stripped are not (their finds would leave Findings),
    and one an earlier start reopened is complete again."""
    store = Store(tmp_path)

    def field(key: str, meta: dict[str, Any], complete: bool = True) -> None:
        store.begin("field", key, {"ra": 1.0, "dec": 2.0, "fov": 20.0})
        store.write_meta("field", key, meta)
        if complete:
            store.complete(key)

    good = {"passes": [{}, {}], "exposures": 10, "not_readable": 0}
    field("a" * 24, {**good, "stripped": True})  # stripped, complete: stays
    field("b" * 24, {**good, "stripped": True}, complete=False)  # stripped, reopened by an earlier start: complete again
    field("c" * 24, {"passes": [], "exposures": 10, "not_readable": 10})  # black: reopened
    field("d" * 24, good)  # a good field: stays
    assert fieldjob.unread(store.read_meta("field", "c" * 24))
    assert fieldjob.reopen_unread(store) == (1, 1)
    assert [store.is_complete(k * 24) for k in "abcd"] == [True, True, False, True]


def test_a_track_leads_field_and_film_are_warmed_where_its_task_opens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A track's task opens centred on its visits (routes_tasks.track_place); the sweep builds that field and the film of the track's
    pass on it, so its questions do not wait on a cold film; built, it is left."""
    from service import findings
    from service.products.store import product_key
    from service.routes_tasks import track_place

    visits = [{"ra": 50.0, "dec": 7.0, "mjd": 61100.0}, {"ra": 50.0, "dec": 7.0 + 7 / 60, "mjd": 61101.0}]
    track = {"kind": "film", "ra": 50.0, "dec": 7.05, "data": {"field_params": {"ra": 50.1, "dec": 7.2, "fov": 20.0}, "visits": visits,
                                                               "film": 61100.5}}  # fmt: skip
    monkeypatch.setattr(findings, "query", lambda *a, **k: {"finds": [track]})
    monkeypatch.setattr(fieldjob, "exposure_signature", lambda index, params: "sig")
    store = Store(tmp_path / "store")
    films: list[tuple[float, ...]] = []
    st = SimpleNamespace(findings=SimpleNamespace(db=object()), findings_flow=object(), index=object(), store=store,
                         lead_film_key=lambda ra, dec, fov, mjd: (films.append((ra, dec, fov, mjd)) or ("filmkey", {})))  # fmt: skip
    got = sweep_runner.top_find_fields(st)
    place = track_place({"data": track["data"]})
    assert got[0] == {**fieldjob.canonical(*place), "film_mjd": 61100.5} and films[0][3] == 61100.5
    store.begin("film", "filmkey", {})
    store.complete("filmkey")
    lead = fieldjob.canonical(*place)
    key = product_key(fieldjob.KIND, lead, extra=("sig",))
    store.begin("field", key, lead)
    store.write_meta("field", key, {"passes": [{}, {}]})
    store.complete(key)
    assert lead not in [{k: v for k, v in p.items() if k != "film_mjd"} for p in sweep_runner.top_find_fields(st)]


def test_the_days_squares_come_from_their_rows_and_a_restart_keeps_them(tmp_path: Path) -> None:
    """squares_today lived in the process: each restart said 0 (five restarts on 19 Nov 2026). It is read from the squares' rows."""
    r, st, _ = make(tmp_path, ids=["red:ccc333", "anomaly:ddd444"])
    ce = r.next_square()
    asyncio.run(r.square(ce))
    today = r.snapshot()
    assert today["squares_today"] == 1 and today["finds_today"] == 2
    # a node's square sent back to the main service is not the main service's own sweep; an old square is not today's
    st.grid.put("node-square", "searched", source="node:a1", searched_at="2026-11-19T10:00:00Z")
    st.grid.put("old-square", "searched", source="sweep", searched_at="2020-01-01T10:00:00Z")
    again = sweep_runner.SweepRunner(st, None, None)  # a restart: a new runner, the same rows
    assert again.snapshot()["squares_today"] == 1
