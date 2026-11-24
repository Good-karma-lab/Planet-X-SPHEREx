"""Square quarters without the cold wait (service/quarters.py): a built quarter is handed out first, the next one is built while a
person looks, and a small stock of ready quarters is kept ahead, never while a person's job waits."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI

from service.jobs.scheduler import Priority
from service.quarters import QUARTER_FOV, Quarters
from service.routes_tasks import open_quarters, pick_quarter
from tests.test_accounts import _client, _fake_grid, _sign_in, _State, site  # noqa: F401 (the fixture)


def fake(st: _State, ready: set[tuple[str, str]]) -> list[tuple[str, str, Any]]:
    built: list[tuple[str, str, Any]] = []
    st.quarters.ready = lambda c, q: (c, q) in ready  # type: ignore[attr-defined]
    st.quarters.build = lambda c, q, p: built.append((c, q, p)) or True  # type: ignore[attr-defined]
    return built


def test_pick_quarter_puts_a_built_quarter_first() -> None:
    assert pick_quarter({}, set()) == "NE"
    assert pick_quarter({}, set(), lambda q: q == "SE") == "SE"
    assert pick_quarter({}, {"SE"}, lambda q: q == "SE") == "NE"  # taken: not offered, even built
    assert open_quarters({"NE": 2}, {"NW"}) == ["SE", "SW", "NE"]


def test_a_built_quarter_is_given_before_a_cold_one(site: tuple[FastAPI, _State, Any]) -> None:  # noqa: F811
    app, st, _mail = site
    _fake_grid(st)
    built = fake(st, {("294-0150", "SE")})
    t = _client(app).get("/api/tasks/next", params={"kind": "cell"}).json()["task"]
    # 294-0148 NE has the fewest looks, but 294-0150 SE is built: the person does not wait
    assert (t["cell_id"], t["quarter"], t["fov"]) == ("294-0150", "SE", QUARTER_FOV)
    # and the quarter they would get next in that square is built meanwhile, as their background work
    assert built == [("294-0150", "NE", Priority.P1)]


def test_while_a_person_looks_the_next_quarter_is_built(site: tuple[FastAPI, _State, Any]) -> None:  # noqa: F811
    app, st, mail = site
    _fake_grid(st)
    built = fake(st, {("294-0148", "NW")})
    c = _sign_in(app, mail)
    t = c.get("/api/tasks/next", params={"kind": "cell"}).json()["task"]
    # NW is built: it is given first, and the quarter after it (NE) is built while the person looks
    assert (t["cell_id"], t["quarter"]) == ("294-0148", "NW") and built[-1] == ("294-0148", "NE", Priority.P1)
    # the open task comes back (the lab asks again): the next quarter is asked for again (a no-op once it is building)
    assert c.get("/api/tasks/next", params={"kind": "cell"}).json()["task"]["id"] == t["id"]
    assert built[-1] == ("294-0148", "NE", Priority.P1)
    c.post(f"/api/me/tasks/{t['id']}/done", json={"result": "nothing", "mark_ids": []})
    t2 = c.get("/api/tasks/next", params={"kind": "cell"}).json()["task"]
    assert (t2["cell_id"], t2["quarter"]) == ("294-0148", "NE") and built[-1] == ("294-0148", "SE", Priority.P1)


class _Sch:
    def __init__(self) -> None:
        self.jobs: dict[str, Any] = {}
        self.waiting = False

    def person_waiting(self) -> bool:
        return self.waiting


def test_the_stock_keeps_quarters_ready_two_builds_at_a_time() -> None:
    sch = _Sch()
    asked: list[dict[str, Any]] = []

    def submit(params: dict[str, Any], priority: Priority) -> tuple[str, Any]:
        asked.append({**params, "priority": priority})
        sch.jobs[f"j{len(asked)}"] = SimpleNamespace(kind="field", priority=priority, state="queued", params=params)
        return "k", object()

    st = SimpleNamespace(scheduler=sch, submit_field=submit, index=None, store=None)
    qs = Quarters(st, lambda c, q: {"ra": 10.0 + ord(c) * 0.01, "dec": {"NE": 1.0, "NW": 2.0, "SE": 3.0, "SW": 4.0}[q]})
    ready = {("a", "NE")}
    qs.ready = lambda c, q: (c, q) in ready  # type: ignore[method-assign]
    order = [("a", "NE"), ("a", "NW"), ("b", "NE"), ("b", "NW")]
    assert qs.stock_step(order, stock=4) == "a/NW" and asked[0]["priority"] == Priority.P2 and asked[0]["fov"] == QUARTER_FOV
    assert qs.stock_step(order, stock=4) == "b/NE"  # a second one beside it, not the same quarter again
    assert qs.stock_step(order, stock=4) is None  # two at a time
    sch.jobs["j1"].state = "done"
    ready.add(("a", "NW"))
    assert qs.stock_step(order, stock=2) is None  # two ready: enough
    sch.waiting = True
    assert qs.stock_step(order, stock=4) is None  # a person's job waits: the stock gives way
    sch.waiting = False
    assert qs.stock_step(order, stock=4) == "b/NW"  # b/NE is still building
    assert qs.stock_step(order, stock=0) is None


def test_a_build_asked_from_a_worker_thread_is_submitted_on_the_loop() -> None:
    on: list[int] = []

    def submit(params: dict[str, Any], priority: Priority) -> tuple[str, Any]:
        on.append(threading.get_ident())
        return "k", object()

    st = SimpleNamespace(scheduler=_Sch(), submit_field=submit, index=None, store=None)
    qs = Quarters(st, lambda c, q: {"ra": 1.0, "dec": 2.0})
    qs.ready = lambda c, q: False  # type: ignore[method-assign]

    async def go() -> None:
        qs.bind(asyncio.get_running_loop())
        assert await asyncio.to_thread(qs.prebuild, "a", "NE") is True
        await asyncio.sleep(0.05)

    loop_thread: list[int] = []

    async def main() -> None:
        loop_thread.append(threading.get_ident())
        await go()

    asyncio.run(main())
    assert on == loop_thread  # submitted on the loop's own thread, not the worker's


def test_people_are_spread_and_a_newcomer_never_gets_a_famous_square(
    site: tuple[FastAPI, _State, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two guests do not all get the same square: a random one of the first few (built ones first); and a newcomer's first square is
    never one a famous test target stands in."""
    import random

    from service import routes_tasks

    app, st, _mail = site
    _fake_grid(st)
    fake(st, set())
    monkeypatch.setattr(routes_tasks, "famous_cells", lambda: {"294-0148"})
    seen = set()
    for seed in range(12):
        monkeypatch.setattr(routes_tasks, "RNG", random.Random(seed))
        t = _client(app).get("/api/tasks/next", params={"kind": "cell"}).json()["task"]
        seen.add((t["cell_id"], t["quarter"]))
        assert t["cell_id"] != "294-0148"  # the famous square is not a newcomer's
    assert len(seen) > 1  # spread


def test_a_square_is_given_where_the_machine_finds_are_most_promising(
    site: tuple[FastAPI, _State, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "Give me a square": the quarters holding the best leads (Findings' Promising kinds) that nobody has looked through come first,
    the most promising first, never a famous target's square for a newcomer; the task says which find and why; red sources do not count."""
    import random

    from service import accounts, grid, routes_tasks

    app, st, _mail = site
    _fake_grid(st)
    fake(st, {("294-0150", "SW"), ("294-0148", "NW")})  # the promising quarters are built (M-3: only built ones)

    def at(cell: str, q: str) -> tuple[float, float]:
        qc = grid.quarter(grid.by_id(cell), q)
        return qc.ra, qc.dec

    def lead(fid: str, kind: str, cell: str, q: str, promise: int, status: str = "unexplained") -> dict[str, Any]:
        ra, dec = at(cell, q)
        return {"finding_id": fid, "kind": kind, "ra": ra, "dec": dec, "status": status, "promise": promise}

    finds = [
        lead("red:1", "red", "294-0150", "NE", 35),  # a red source: not a reason for a square
        lead("browndwarf:1", "browndwarf", "294-0150", "SW", 77),
        lead("film:1", "film", "294-0148", "NW", 60),  # the famous square's
        lead("mover:1", "mover", "294-0149", "SE", 40, status="known"),  # known: not a lead
    ]
    monkeypatch.setattr(routes_tasks, "all_rows", lambda st: [dict(f, _listed=True) for f in finds])
    # each guest here is the first in half an hour (the busy quarters: test_a_quarter_somebody_just_took_is_not_given_to_another)
    monkeypatch.setattr(routes_tasks, "BUSY_QUARTER_S", 0.0)
    monkeypatch.setitem(routes_tasks._promising, "at", 0.0)
    monkeypatch.setattr(routes_tasks, "famous_cells", lambda: {"294-0148"})
    got = set()
    for seed in range(8):
        monkeypatch.setattr(routes_tasks, "RNG", random.Random(seed))
        t = _client(app).get("/api/tasks/next", params={"kind": "cell"}).json()["task"]
        got.add((t["cell_id"], t["quarter"], (t.get("why") or {}).get("finding_id")))
    assert got == {("294-0150", "SW", "browndwarf:1")}  # the newcomer never gets the famous square's track
    assert routes_tasks.quarter_of(grid.by_id("294-0150"), *at("294-0150", "SW")) == "SW"
    # the task says why in the card's words, where the find is, and which question series to open it with
    t = _client(app).get("/api/tasks/next", params={"kind": "cell"}).json()["task"]
    w = t["why"]
    assert w["text"] == "a possible brown dwarf · worth a look: 77 of 100" and (w["ra"], w["dec"]) == tuple(round(v, 6) for v in at("294-0150", "SW"))  # noqa: E501
    assert w["ask"] == {"kind": "moves", "finding_id": "browndwarf:1", "series": "slow"}
    # the most promising quarter, every time (not a random one of the first few): a second built lead of lower promise is not given
    finds.append(lead("peak:2", "peak", "294-0150", "NE", 70))
    fake(st, {("294-0150", "SW"), ("294-0148", "NW"), ("294-0150", "NE")})
    monkeypatch.setitem(routes_tasks._promising, "at", 0.0)
    assert {_client(app).get("/api/tasks/next", params={"kind": "cell"}).json()["task"]["why"]["finding_id"] for _ in range(6)} == {
        "browndwarf:1"
    }
    finds.pop()
    # not built yet: not given first (it took 1 min 45 s cold); the stock builds it, and the stock's built quarters are given meanwhile
    fake(st, set())
    monkeypatch.setitem(routes_tasks._promising, "at", 0.0)
    t = _client(app).get("/api/tasks/next", params={"kind": "cell"}).json()["task"]
    assert "why" not in t
    fake(st, {("294-0150", "SW"), ("294-0148", "NW")})
    # once somebody has looked through that quarter, it is not given first any more: the stock's order
    accounts.get(st).db._conn().execute("INSERT INTO grid_views VALUES ('294-0150','user:a','nothing',2.0,'SW')")
    monkeypatch.setitem(routes_tasks._promising, "at", 0.0)
    t = _client(app).get("/api/tasks/next", params={"kind": "cell"}).json()["task"]
    assert "why" not in t


def test_a_quarter_somebody_just_took_is_not_given_to_another(
    site: tuple[FastAPI, _State, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The user, 20 Nov 2026 (for the launch): a crowd of newcomers is not sent to the top find's quarter all at once. A quarter
    somebody (a guest too) took in the last BUSY_QUARTER_S is not given to another; the next most promising one is, then the stock's;
    after half an hour the top one is given again."""
    from service import accounts, grid, routes_tasks

    app, st, _mail = site
    _fake_grid(st)

    def lead(fid: str, cell: str, q: str, promise: int) -> dict[str, Any]:
        qc = grid.quarter(grid.by_id(cell), q)
        return {
            "finding_id": fid,
            "kind": "browndwarf",
            "ra": qc.ra,
            "dec": qc.dec,
            "status": "unexplained",
            "promise": promise,
            "_listed": True,
        }

    finds = [lead("browndwarf:1", "294-0150", "SW", 77), lead("browndwarf:2", "294-0150", "NE", 60)]
    fake(st, {("294-0150", "SW"), ("294-0150", "NE")})
    monkeypatch.setattr(routes_tasks, "all_rows", lambda st: finds)
    monkeypatch.setitem(routes_tasks._promising, "at", 0.0)
    got = [_client(app).get("/api/tasks/next", params={"kind": "cell"}).json()["task"] for _ in range(3)]
    assert [(t["cell_id"], t["quarter"]) for t in got[:2]] == [("294-0150", "SW"), ("294-0150", "NE")]
    assert "why" not in got[2] and (got[2]["cell_id"], got[2]["quarter"]) not in {("294-0150", "SW"), ("294-0150", "NE")}
    accounts.get(st).db._conn().execute("UPDATE acc_tasks SET created=created-?", (routes_tasks.BUSY_QUARTER_S + 60,))
    again = _client(app).get("/api/tasks/next", params={"kind": "cell"}).json()["task"]
    assert (again["cell_id"], again["quarter"], again["why"]["finding_id"]) == ("294-0150", "SW", "browndwarf:1")


def test_an_old_open_square_task_is_not_given_again(
    site: tuple[FastAPI, _State, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explorer round 2: "Give me a square" gave this account the quarter it had taken 11 h earlier. A recent open task is still its
    task (the lab asks again after a reload); one older than OPEN_TASK_KEEP_S is left open in the cabinet but not handed out again,
    and nor is its quarter."""
    from service import accounts, routes_tasks

    app, st, mail = site
    _fake_grid(st)
    fake(st, set())
    c = _sign_in(app, mail)
    first = c.get("/api/tasks/next", params={"kind": "cell"}).json()["task"]
    assert c.get("/api/tasks/next", params={"kind": "cell"}).json()["task"]["id"] == first["id"]  # a recent one: the same task
    accounts.get(st).db._conn().execute(
        "UPDATE acc_tasks SET created=created-? WHERE id=?", (routes_tasks.OPEN_TASK_KEEP_S + 60, first["id"])
    )
    again = c.get("/api/tasks/next", params={"kind": "cell"}).json()["task"]
    assert again["id"] != first["id"] and (again["cell_id"], again["quarter"]) != (first["cell_id"], first["quarter"])
    # the cabinet's Continue skips the old one (m-8: "Continue · square 099-0543 SW" pointed to a square of the day before)
    listed = {t["id"]: t for t in c.get("/api/me/tasks").json()["tasks"]}
    assert listed[first["id"]]["stale"] is True and listed[again["id"]]["stale"] is False


def test_a_track_lead_opens_centred_on_its_visits_with_a_margin() -> None:
    """The designer, 19 Nov 2026: on its film's own field a visit sat on the left edge. A track's task opens centred on the middle of
    its visits at the smallest size holding them with 15 % each side."""
    from service.routes_tasks import track_place

    def row(pts: list[tuple[float, float]]) -> dict[str, Any]:
        return {"data": {"visits": [{"ra": a, "dec": d, "mjd": 61000.0 + k} for k, (a, d) in enumerate(pts)]}}

    ra0, dec0, fov = track_place(row([(50.0, 7.0), (50.0, 7.0 + 7.0 / 60.0)]))  # 7′ long
    assert fov == 10.0 and abs(ra0 - 50.0) < 1e-9 and abs(dec0 - (7.0 + 3.5 / 60.0)) < 1e-9
    assert track_place(row([(50.0, 7.0), (50.0, 7.0 + 15.0 / 60.0)]))[2] == 60.0  # 15′ / 0.7 = 21.4′: not 20′
    ra_w, _, _ = track_place(row([(359.95, 0.0), (0.05, 0.0)]))  # across RA 0
    assert min(abs(ra_w - 0.0), abs(ra_w - 360.0)) < 1e-6
    assert track_place({"data": {}}) is None
    # the middle of the visits, not their order: the first visit may be anywhere along the track
    assert track_place(row([(50.1, 7.0), (50.0, 7.0), (50.05, 7.0)]))[0] == pytest.approx(50.05)
