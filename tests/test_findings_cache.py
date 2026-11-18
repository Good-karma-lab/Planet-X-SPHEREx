"""The Findings list is the same for every guest (service/routes_findings.py): the answer to the same question is kept a few seconds
and a crowd asking at once costs one query."""

from __future__ import annotations

import asyncio
import threading
import time
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
        return {"count": 0, "total": 0, "finds": [find] if status == "blend" else []}

    monkeypatch.setattr(routes_findings, "query", query)
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
