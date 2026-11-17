"""Square quarters without the cold wait (service/quarters.py): a built quarter is handed out first, the next one is built while a
person looks, and a small stock of ready quarters is kept ahead, never while a person's job waits."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any

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
