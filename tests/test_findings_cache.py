"""The Findings list is the same for every guest (service/routes_findings.py): the answer to the same question is kept a few seconds
and a crowd asking at once costs one query."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from service import routes_findings


def test_a_crowd_costs_one_query_and_another_question_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, ...]] = []
    lock = threading.Lock()

    def query(db: Any, kind: Any, status: Any, *a: Any, **k: Any) -> dict[str, Any]:
        with lock:
            calls.append((kind, status))
        time.sleep(0.2)  # a slow list: everybody arrives while it is being made
        return {"count": 0, "total": 0, "finds": []}

    monkeypatch.setattr(routes_findings, "query", query)
    monkeypatch.setattr(routes_findings, "snapshot", lambda *a, **k: object())
    st = SimpleNamespace(
        findings=SimpleNamespace(db=object()), fpx_known=None, accounts=None, clips=None, store=None, findings_flow=object()
    )
    app = FastAPI()
    app.include_router(routes_findings.make_findings_router(st))

    async def go() -> list[int]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            crowd = await asyncio.gather(*(c.get("/api/v1/findings", params={"status": "all", "lite": 1}) for _ in range(20)))
            again = await c.get("/api/v1/findings", params={"status": "all", "lite": 1})  # within the TTL: from the cache
            other = await c.get("/api/v1/findings", params={"status": "known", "lite": 1})  # another question: its own query
            return [r.status_code for r in [*crowd, again, other]]

    codes = asyncio.run(go())
    assert set(codes) == {200} and calls == [(None, "all"), (None, "known")]


def test_a_find_kept_out_of_the_lists_as_a_blend_still_has_its_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """A brown-dwarf candidate that turned out a blend leaves the lists, not its address (History, a lab link): its page answers,
    by id or by slug."""
    find = {"id": "browndwarf:b5e2f0136331", "kind": "browndwarf", "status": "blend", "credit": {}}

    def query(db: Any, kind: Any, status: Any, *a: Any, **k: Any) -> dict[str, Any]:
        only = k.get("only")
        hit = status == "blend" or (status == "any" and (only is None or find["id"] in only))  # the page reads one find, any status
        return {"count": 0, "total": 0, "finds": [find] if hit else []}

    monkeypatch.setattr(routes_findings, "query", query)
    monkeypatch.setattr(routes_findings, "snapshot", lambda *a, **k: object())
    monkeypatch.setattr(routes_findings, "find_row", lambda db, fid: {"id": fid} if fid == find["id"] else None)
    st = SimpleNamespace(
        findings=SimpleNamespace(db=object()), fpx_known=None, accounts=None, clips=None, store=None, findings_flow=object()
    )
    app = FastAPI()
    app.include_router(routes_findings.make_findings_router(st))
    from fastapi.testclient import TestClient

    c = TestClient(app)
    for name in ("browndwarf:b5e2f0136331", "browndwarf-b5e2f0136331"):
        r = c.get(f"/api/v1/findings/{name}")
        assert r.status_code == 200 and r.json()["status"] == "blend"
    assert c.get("/api/v1/findings", params={"status": "blend"}).status_code == 200


def test_the_first_lists_are_kept_for_the_next_start_and_a_stale_one_is_handed_out_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Forty people with the page's own filters after a restart (launch day): the lists of the last run are handed out at once from
    disk, and made again behind them; nobody waits for the finds to be read."""
    import json
    import sqlite3

    from service.routes_findings import WARM_FILE, warm_keys

    monkeypatch.setattr(routes_findings, "LIST_TTL_S", 0.2)
    started = threading.Event()
    go_on = threading.Event()

    def slow_snapshot(*a: Any, **k: Any) -> object:
        started.set()
        go_on.wait(10.0)
        return object()

    def query(db: Any, kind: Any, status: Any, *a: Any, **k: Any) -> dict[str, Any]:
        return {"count": 1, "total": 1, "finds": [], "from": "new"}

    monkeypatch.setattr(routes_findings, "snapshot", slow_snapshot)
    monkeypatch.setattr(routes_findings, "query", query)
    first = warm_keys()[0]
    (tmp_path / WARM_FILE).write_text(json.dumps({json.dumps(first, sort_keys=True): json.dumps({"from": "disk", "finds": []})}))
    con = sqlite3.connect(":memory:", check_same_thread=False)
    con.execute("CREATE TABLE finds (id)")
    db = SimpleNamespace(db=con, read_only=False)
    st = SimpleNamespace(
        findings=SimpleNamespace(db=db, writer=True), fpx_known=None, accounts=None, clips=None, store=None, findings_flow=object(),
        cfg=SimpleNamespace(store_dir=tmp_path),
    )  # fmt: skip
    app = FastAPI()
    app.include_router(routes_findings.make_findings_router(st))
    params = {k: (1 if v is True else v) for k, v in first.items()}

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            warm = asyncio.create_task(st.warm_findings())
            await asyncio.to_thread(started.wait, 5.0)
            t0 = time.monotonic()
            crowd = await asyncio.gather(*(c.get("/api/v1/findings", params=params) for _ in range(40)))
            assert time.monotonic() - t0 < 2.0 and all(r.json()["from"] == "disk" for r in crowd)  # the finds are still being read
            go_on.set()
            for _ in range(100):
                if (await c.get("/api/v1/findings", params=params)).json()["from"] == "new":
                    break
                await asyncio.sleep(0.05)
            assert (await c.get("/api/v1/findings", params=params)).json()["from"] == "new"
            for _ in range(100):
                kept = json.loads((tmp_path / WARM_FILE).read_text())
                if len(kept) == len(warm_keys()):
                    break
                await asyncio.sleep(0.05)
            assert all(json.loads(v)["from"] == "new" for v in kept.values()) and len(kept) == len(warm_keys())
            warm.cancel()

    asyncio.run(run())


def test_the_plain_list_is_among_the_warm_ones() -> None:
    """26 Nov: the first /api/v1/findings with no filter after a start took 5.3 s (33 s at the deploy's own check)."""
    from service.routes_findings import warm_keys

    assert {} in warm_keys()
