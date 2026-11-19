"""A second node (service/node.py): signed calls, the squares the main service lends, and what it takes back."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from service import grid, node
from service.grid import GridDB
from service.products.store import Store
from service.products.versions import VERSIONS
from service.routes_node import make_node_router


def test_signatures() -> None:
    body = b'{"node":"a1"}'
    h = node.sign("s3cret", body)
    assert node.verify("s3cret", body, h)
    assert not node.verify("other", body, h) and not node.verify("s3cret", b"{}", h) and not node.verify("s3cret", body, None)
    old = node.sign("s3cret", body, now=time.time() - 2 * node.NODE_SKEW_S)
    assert not node.verify("s3cret", body, old) and not node.verify("", body, h)


def test_leases_lend_in_order_and_take_back_an_expired_one(tmp_path: Path) -> None:
    g = GridDB(tmp_path / "product.sqlite")
    lz = node.Leases(tmp_path / "product.sqlite")
    order = [grid.cell(90, 0), grid.cell(90, 1), grid.cell(90, 2)]
    g.put(order[0].id, "searched", source="sweep")
    a = lz.lend(g, iter(order), "a1")
    assert a.id == order[1].id and g.get(a.id)["state"] == "searching" and g.get(a.id)["source"] == "node:a1"
    assert lz.lend(g, iter(order), "a1").id == order[1].id  # asked again (a restart): the square it holds
    b = lz.lend(g, iter(order), "b2")
    assert b.id == order[2].id  # a held square is not lent to another node
    assert lz.lend(g, iter(order), "c3") is None
    lz.release(order[1].id)
    lz.release(order[2].id)
    g.put(order[1].id, "searching", source="node:a1")
    g.put(order[2].id, "searching", source="node:b2")
    lz.db.execute("INSERT INTO node_leases VALUES (?,?,?,?)", (order[1].id, "a1", time.time() + 60, time.time()))
    lz.db.execute("INSERT INTO node_leases VALUES (?,?,?,?)", (order[2].id, "b2", time.time() + 60, time.time()))
    later = time.time() + node.LEASE_S + 1
    c = lz.lend(g, iter(order), "a1", now=later)  # both leases ended: their squares are new again and lent again
    assert c.id == order[1].id and lz.holds(order[1].id, "a1") and not lz.holds(order[2].id, "a1")


def _body(field_key: str, square: str) -> dict[str, Any]:
    return {
        "node": "a1",
        "square": square,
        "products": [
            {
                "kind": "field",
                "key": field_key,
                "version": VERSIONS["field"],
                "params": {"ra": 1.0, "dec": 2.0, "fov": 20.0},
                "meta": {"passes": [{}, {}, {}]},
            },
            {"kind": "field", "key": "f" * 24, "version": "old", "params": {}, "meta": {}},  # another version: not taken
        ],
        "files": [{"path": "movers/m1.json", "text": json.dumps({"tracks": []})}],
        "row": {"state": "searched", "finds": 1, "find_ids": ["mover:abc"], "bar": {"passes": 3}, "field_key": field_key, "seconds": 120},
    }


def test_apply_result_writes_rows_metas_files_and_the_square(tmp_path: Path) -> None:
    store, g = Store(tmp_path), GridDB(tmp_path / "product.sqlite")
    sq = grid.cell(90, 5).id
    got = node.apply_result(store, tmp_path, g, _body("a" * 24, sq))
    assert got == {"products": 1, "files": 1}
    assert store.is_complete("a" * 24) and store.read_meta("field", "a" * 24) == {"passes": [{}, {}, {}], "stripped": True, "made_on": "a1"}
    assert not store.is_complete("f" * 24)
    assert json.loads((tmp_path / "movers" / "m1.json").read_text()) == {"tracks": []}
    row = g.get(sq)
    assert row["state"] == "searched" and row["source"] == "node:a1" and json.loads(row["find_ids"]) == ["mover:abc"]
    for bad in ("../x.json", "/etc/passwd", "movers/../../x.json", "products/x.json", "movers/a/b.json", "movers/x.txt"):
        with pytest.raises(ValueError):
            node.apply_result(store, tmp_path, g, {**_body("b" * 24, sq), "files": [{"path": bad, "text": "{}"}]})
    with pytest.raises(ValueError):
        node.apply_result(store, tmp_path, g, {**_body("b" * 24, sq), "products": [{"kind": "accounts", "key": "x", "version": "1"}]})
    with pytest.raises(ValueError):
        node.apply_result(store, tmp_path, g, {**_body("b" * 24, sq), "files": [{"path": "red/r.json", "text": "not json"}]})


def test_the_routes_lend_and_take_back_signed_calls_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    st = SimpleNamespace(
        cfg=SimpleNamespace(store_dir=tmp_path), grid=GridDB(tmp_path / "product.sqlite"), store=Store(tmp_path), findings=None
    )
    app = FastAPI()
    app.include_router(make_node_router(st))
    c = TestClient(app)

    def post(path: str, payload: dict[str, Any], key: str = "k1") -> Any:
        body = json.dumps(payload).encode()
        return c.post(path, content=body, headers={"content-type": "application/json", "x-sky-node": node.sign(key, body)})

    monkeypatch.delenv("SKY_NODE_SECRET", raising=False)
    assert post("/api/internal/lease", {"node": "a1"}).status_code == 404  # no node set up: the route is not there
    monkeypatch.setenv("SKY_NODE_SECRET", "k1")
    assert post("/api/internal/lease", {"node": "a1"}, key="wrong").status_code == 403
    assert c.post("/api/internal/lease", json={"node": "a1"}).status_code == 403
    sq = post("/api/internal/lease", {"node": "a1"}).json()["square"]
    assert sq is not None and st.grid.get(sq["id"])["state"] == "searching"
    assert post("/api/internal/result", _body("c" * 24, "b00c00")).status_code == 409  # a square it does not hold
    out = post("/api/internal/result", _body("c" * 24, sq["id"])).json()
    assert out == {"ok": True, "products": 1, "files": 1} and st.grid.get(sq["id"])["state"] == "searched"
    assert post("/api/internal/result", _body("c" * 24, sq["id"])).status_code == 409  # its lease ended with the result
    distant = {"kind": "distant", "key": "e" * 24, "version": VERSIONS["distant"], "params": {}, "meta": {"status": "ok"}}
    late = {"node": "a1", "square": sq["id"], "late": True, "files": [{"path": "red/late.json", "text": "{}"}], "products": [distant]}
    assert post("/api/internal/result", late).json() == {"ok": True, "products": 1, "files": 1}  # a Planet X search that finished later
    assert st.store.is_complete("e" * 24) and (tmp_path / "red" / "late.json").is_file()
    assert st.grid.get(sq["id"])["state"] == "searched"  # the square's row is left as it was
    assert post("/api/internal/result", {**late, "node": "b2"}).status_code == 409  # not the node that searched it
    other = post("/api/internal/lease", {"node": "a1"}).json()["square"]
    assert post("/api/internal/result", {**late, "square": other["id"]}).status_code == 409  # a square still being searched


def test_a_node_sends_what_it_made_since_the_square_began(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.begin("field", "d" * 24, {"ra": 1.0})
    store.write_meta("field", "d" * 24, {"passes": []})
    store.complete("d" * 24)
    (tmp_path / "movers").mkdir()
    (tmp_path / "movers" / "x.json").write_text("{}")
    products, files = node.made_since(store, tmp_path, time.time() - 5)
    assert [p["key"] for p in products] == ["d" * 24] and files == [{"path": "movers/x.json", "text": "{}"}]
    assert node.made_since(store, tmp_path, time.time() + 60) == ([], [])


def test_health_says_what_each_node_does(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """/health `nodes`: the square a node holds, its step (from its heartbeat) and its squares today; a square being searched by a
    node says so on the globe (`by`)."""
    monkeypatch.setenv("SKY_NODE_SECRET", "k1")
    st = SimpleNamespace(
        cfg=SimpleNamespace(store_dir=tmp_path), grid=GridDB(tmp_path / "product.sqlite"), store=Store(tmp_path), findings=None
    )
    app = FastAPI()
    app.include_router(make_node_router(st))
    c = TestClient(app)

    def post(path: str, payload: dict[str, Any]) -> Any:
        body = json.dumps(payload).encode()
        return c.post(path, content=body, headers={"content-type": "application/json", "x-sky-node": node.sign("k1", body)})

    first = post("/api/internal/lease", {"node": "a1"}).json()["square"]
    post("/api/internal/result", _body("c" * 24, first["id"]))
    held = post("/api/internal/lease", {"node": "a1"}).json()["square"]
    assert post("/api/internal/status", {"node": "a1", "sweep": {"state": "searching", "step": "Find Planet X", "per_hour": 2.4}}).json()[
        "ok"
    ]
    got = st.nodes_summary()["a1"]
    assert got["square"] == held["id"] and got["step"] == "Find Planet X" and got["squares_today"] == 1 and got["finds_today"] == 1
    assert got["seen_s"] <= 2 and got["state"] == "searching"
    assert grid.view_of(grid.by_id(held["id"]), st.grid.get(held["id"]), 0)["by"] == "a1"
    assert "by" not in grid.view_of(grid.by_id(first["id"]), st.grid.get(first["id"]), 0)  # searched: no longer being searched


def test_the_leases_take_many_threads_at_once(tmp_path: Path) -> None:
    """/health reads the leases from several request threads at once (Caddy every 2 s): no 'bad parameter or other API misuse'."""
    from concurrent.futures import ThreadPoolExecutor

    lz = node.Leases(tmp_path / "product.sqlite")
    for k in range(50):
        lz.record(f"290-{k:04d}", "a1", 1, 2, 3)
    with ThreadPoolExecutor(8) as ex:
        got = list(ex.map(lambda _: lz.summary({})["a1"]["squares_all"], range(400)))
    assert set(got) == {50}


def test_a_node_runs_two_squares_at_once_each_under_its_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """2 slots on a normal machine, 1 on a small one, SKY_NODE_SLOTS wins; each slot leases under its own name (a1s0, a1s1), also from
    the thread its lease call runs in."""
    import asyncio

    from service import sweep_runner

    monkeypatch.delenv("SKY_NODE_SLOTS", raising=False)
    assert node.slots(SimpleNamespace(small=False)) == 2 and node.slots(SimpleNamespace(small=True)) == 1
    monkeypatch.setenv("SKY_NODE_SLOTS", "3")
    assert node.slots(SimpleNamespace(small=True)) == 3
    monkeypatch.setenv("SKY_NODE_NAME", "a1")
    assert sweep_runner.lease_name() == "a1"

    async def slot(k: int) -> str:
        sweep_runner.SLOT.set(f"a1s{k}")
        return await asyncio.to_thread(sweep_runner.lease_name)

    async def both() -> list[str]:
        return list(await asyncio.gather(slot(0), slot(1)))

    assert asyncio.run(both()) == ["a1s0", "a1s1"]


def test_a_node_lets_its_background_take_every_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """A node serves nobody at the page: no worker is kept free for people (one of four idled while hundreds of calls waited)."""
    from service import cpu

    assert cpu._Gate(4).low_cap == 3 and cpu._Gate(4, reserve=0).low_cap == 4
    monkeypatch.setenv("SKY_NODE_MAIN", "https://main.example")
    monkeypatch.setenv("SKY_NODE_SECRET", "k")
    cpu.configure(object(), 4)  # type: ignore[arg-type]
    try:
        assert cpu.stats()["background_cap"] == 4
    finally:
        cpu.configure(None, 1)
