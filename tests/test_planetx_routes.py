# ruff: noqa: N803, N806
"""Find Planet X: the routes (validation, plan, jump, link) on one-pass results written by hand; no exposure is read."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine import link as elink  # noqa: F401  (the same engine the job uses)
from service.jobs.scheduler import Scheduler
from service.products.store import Store, product_key
from service.routes_planetx import make_planetx_router
from service.workers import distantjob
from tests.test_planetx_engine import SPAN, T1, T2, T3, candidate_of, circular_body

KEYS = {T1: "aa11aa11aa11aa11aa11aa11", T2: "bb22bb22bb22bb22bb22bb22", T3: "cc33cc33cc33cc33cc33cc33"}


class EmptyIndex:
    def overlapping(self, ra: float, dec: float, radius: float) -> list[Any]:
        return []


def make_app(tmp_path: Path) -> tuple[TestClient, Store]:
    store = Store(tmp_path / "store")
    sched = Scheduler()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        sched.start()
        yield
        await sched.stop()

    st = SimpleNamespace(
        index=EmptyIndex(), store=store, scheduler=sched, files=object(), wavelengths=None, cfg=SimpleNamespace(store_dir=tmp_path)
    )
    app = FastAPI(lifespan=lifespan)
    app.include_router(make_planetx_router(st))
    app.state.scheduler = sched
    return TestClient(app), store


def write_pass(store: Store, t0: float, X0: Any, V0: Any, d_au: float) -> str:
    key = KEYS[t0]
    grid, c = candidate_of(X0, V0, T1, t0, d_au)
    meta = {
        "status": "ok",
        "grid": grid,
        "t0": t0,
        "span_days": SPAN,
        "candidates": [
            {
                **c,
                "sn": 12.0,
                "phot_sn": 9.0,
                "ok": True,
                "ra": grid["ra0"],
                "dec": grid["dec0"],
                "d_lo": 60.0,
                "d_hi": 140.0,
                "flux_mjy": 0.08,
            }
        ],
    }
    store.begin(distantjob.KIND, key, {})
    store.write_meta(distantjob.KIND, key, meta)
    store.complete(key)
    return key


def test_validation(tmp_path: Path) -> None:
    client, _ = make_app(tmp_path)
    with client:
        assert client.get("/api/v1/distant?ra=10&dec=5&dmin=100&dmax=110").status_code == 400
        assert client.get("/api/v1/distant?ra=400&dec=5").status_code == 422
        assert client.get("/api/v1/deep?fields=10,5,61000.5").status_code == 400
        assert client.get("/api/v1/deep?fields=a,b,c;1,2,3").status_code == 400
        assert client.get("/api/v1/link?keys=onlyone").status_code == 400
        assert client.get("/api/v1/jump?key=nothere&mjd=61000").status_code == 404


def test_plan_without_coverage(tmp_path: Path) -> None:
    client, _ = make_app(tmp_path)
    with client:
        r = client.get("/api/v1/distant?ra=10&dec=5&mjd=61000&plan=1").json()
        assert r["status"] == "no-coverage"
        r = client.get("/api/v1/distant?ra=10&dec=5&mjd=61000").json()
        assert r["state"] == "done" and r["result"]["status"] == "no-coverage"
        d = client.get("/api/v1/deep?centre=10,5,61000&passes=61000;61180;61360&dmin=80&dmax=115&plan=1").json()
        assert d["status"] == "no-coverage" and len(d["fields"]) == 3 and d["limit"] > 1_000_000


def test_honest(tmp_path: Path) -> None:
    client, _ = make_app(tmp_path)
    with client:
        h = client.get("/api/v1/planetx/honest").json()
        assert "limit" in h and "lead" in h and "mJy" not in h["limit"]  # the depth is the field's own measured bar


def test_jump_and_link_on_stored_results(tmp_path: Path) -> None:
    client, store = make_app(tmp_path)
    X0, V0 = circular_body(27.378, -0.12, T1, 95.0)
    import numpy as np

    from engine import orbit as eorbit

    keys = []
    for t0 in (T1, T2, T3):
        d = float(np.linalg.norm(eorbit.kepler(X0, V0, np.float64((t0 - T1) * 86400.0))) / eorbit.AU)
        keys.append(write_pass(store, t0, X0, V0, d))
    with client:
        j = client.get(f"/api/v1/jump?key={keys[0]}&i=0&mjd={T2}&corridor=60").json()
        assert len(j["line"]) == 40 and j["line_length_arcmin"] > 10
        assert any(x["key"] == keys[1] for x in j["in_corridor"])
        assert "limit" in j["honest"]
        r = client.get(f"/api/v1/link?keys={','.join(keys)}").json()
        t_end = time.time() + 120
        while r["state"] == "running" and time.time() < t_end:
            time.sleep(0.5)
            r = client.get(f"/api/v1/link?keys={','.join(keys)}").json()
        assert r["state"] == "done", r
        best = r["result"]["best"]
        assert best["passes"] == 3 and best["confirmed"]
        assert abs(best["distance_au"] - 95.0) < 4.0
        assert r["result"]["honest"]["lead"]


def test_link_with_a_missing_result(tmp_path: Path) -> None:
    client, _ = make_app(tmp_path)
    with client:
        k = product_key("link", {"keys": ["x1", "y2"], "tol": 6.0, "all": False})
        assert k
        r = client.get("/api/v1/link?keys=x1,y2").json()
        t_end = time.time() + 30
        while r["state"] == "running" and time.time() < t_end:
            time.sleep(0.3)
            r = client.get("/api/v1/link?keys=x1,y2").json()
        assert r["state"] == "done" and r["result"]["status"] == "missing"


@pytest.mark.parametrize("fields", ["27,0,61000", "27,0,61000;28,0,61200;29,0,61400;30,0,61600;31,0,61800;32,0,62000;33,0,62200"])
def test_deep_needs_two_to_six_fields(tmp_path: Path, fields: str) -> None:
    client, _ = make_app(tmp_path)
    with client:
        r = client.get(f"/api/v1/deep?fields={fields}")
        if fields.count(";") == 0:
            assert r.status_code == 400
        else:
            assert r.status_code == 200
            t_end = time.time() + 30
            body = r.json()
            while body["state"] == "running" and time.time() < t_end:
                time.sleep(0.3)
                body = client.get(f"/api/v1/deep?fields={fields}").json()
            # a job on a field without exposures ends at once; at most six fields are taken
            assert body["state"] == "done" and body["result"]["status"] == "no-coverage" and len(body["result"]["fields"]) == 6


def test_the_jumps_checks_run_to_their_end_a_pages_background_search_is_followed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The scheduler cancels a background /distant run nobody asks about for 90 s (its page left); the searches jump(check=1) starts
    for a person are asked about by nobody (the jump is asked again), so they are not followed and run to their end."""
    import asyncio

    async def plan(index: Any, params: Any) -> dict[str, Any]:
        return {"estimate_s": 1}

    async def forever(*a: Any) -> dict[str, Any]:
        await asyncio.sleep(60)
        return {}

    # every field has exposures here (none is read: the search itself waits)
    monkeypatch.setattr(distantjob, "selection", lambda index, params: (["e"], 0, "pk", {}, None))
    monkeypatch.setattr(distantjob, "signature", lambda exps: "sig")
    monkeypatch.setattr(distantjob, "plan_async", plan)
    monkeypatch.setattr(distantjob, "run_distant", forever)
    client, store = make_app(tmp_path)
    X0, V0 = circular_body(27.378, -0.12, T1, 95.0)
    key = write_pass(store, T1, X0, V0, 95.0)
    sched = client.app.state.scheduler  # type: ignore[attr-defined]
    with client:
        j = client.get(f"/api/v1/jump?key={key}&i=0&mjd={T2}&corridor=60&dmin=60&dmax=200&check=1").json()
        started = {x["key"] for x in j["started"]}
        assert started and all(sched.jobs[sched.by_key[k]].followed is False for k in started)
        r = client.get("/api/v1/distant?ra=40&dec=1&fov=5&mjd=61000&bg=1").json()
        assert sched.jobs[sched.by_key[r["key"]]].followed is True
        r0 = client.get("/api/v1/distant?ra=41&dec=1&fov=5&mjd=61000").json()  # a person's own (P0): abandoned the old way, not followed
        assert sched.jobs[sched.by_key[r0["key"]]].followed is False
