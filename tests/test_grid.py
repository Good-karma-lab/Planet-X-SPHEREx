"""The sky grid: stable ids, every place in exactly one square, corners, the sweep order, the coverage routes."""

from __future__ import annotations

import itertools
import random
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service import grid
from service.grid import GridDB
from service.routes_grid import make_grid_router


def test_total_and_ids() -> None:
    assert 360_000 < grid.TOTAL < 380_000
    assert grid.cell_at(0.1, 0.1).id == "270-0000"
    for cid in ("000-0000", "539-0002", "294-0148"):
        assert grid.by_id(cid).id == cid


def test_every_place_in_its_square() -> None:
    rng = random.Random(1)
    for _ in range(2000):
        ra, dec = rng.uniform(0, 360), rng.uniform(-90, 90)
        c = grid.cell_at(ra, dec)
        assert c.dec0 <= dec <= c.dec1
        assert c.ra0 <= ra % 360 <= c.ra1
        assert grid.by_id(c.id) == c
        assert len(c.corners()) == 4


def test_square_size_is_the_search_field() -> None:
    import math

    c = grid.cell_at(50, 8)
    assert abs((c.dec1 - c.dec0) * 60 - 20) < 1e-6
    assert abs((c.ra1 - c.ra0) * math.cos(math.radians(c.dec)) * 60 - 20) < 0.5


def test_cells_near() -> None:
    cs = grid.cells_near(50, 8, 1.0)
    assert cs[0].id == grid.cell_at(50, 8).id
    assert 20 < len(cs) < 40
    assert grid.cells_near(10, 89.9, 0.5)  # near the pole


def test_sweep_order_starts_in_the_planet_nine_band() -> None:
    first = list(itertools.islice(grid.sweep_order(), 50))
    assert all(grid.priority(c)[0] == 0 for c in first)
    assert grid.sep_deg(first[0].ra, first[0].dec, *grid.P9_CENTRE) < 0.5


def test_db_and_routes(tmp_path: Path) -> None:
    db = GridDB(tmp_path / "product.sqlite")
    n = grid.import_searched(db, [{"ra": 50.0, "dec": 8.0, "fov": 5, "state": "done", "found": 2, "key": "k", "kinds": ["passes"]}])
    assert n == 1
    cid = grid.cell_at(50, 8).id
    app = FastAPI()
    who = {"user": None}  # the signed-in user the accounts module would name (None: a guest)
    app.include_router(make_grid_router(SimpleNamespace(index=None, current_user=lambda request: who["user"]), db))
    c = TestClient(app)
    meta = c.get("/api/grid/meta").json()
    assert meta["scheme"] == grid.SCHEME and meta["counts"]["searched"] == 1 and meta["counts"]["with_finds"] == 1
    cov = c.get("/api/grid/coverage").json()["cells"]
    assert [{k: v for k, v in x.items() if k not in ("ra", "dec")} for x in cov] == [
        {"id": cid, "state": "searched", "finds": 2, "viewers": 0, "quarters": 0}
    ]
    assert abs(cov[0]["dec"] - grid.by_id(cid).dec) < 1e-6
    withc = c.get("/api/grid/coverage", params={"corners": 1}).json()["cells"]
    assert withc[0]["corners"] == grid.by_id(cid).corners()
    assert meta["scheme_params"]["n_bands"] == 540
    cells = c.get("/api/grid/cells", params={"ra": 50, "dec": 8, "radius": 0.5}).json()["cells"]
    assert cells[0]["id"] == cid and cells[0]["state"] == "searched" and len(cells[0]["corners"]) == 4
    one = c.get(f"/api/grid/cell/{cid}").json()
    assert one["field_key"] == "k" and one["bar"]["partial"] is True and one["open"]["fov"] == 20
    assert [q["id"] for q in one["quarters"]] == ["NE", "NW", "SE", "SW"] and one["quarters"][0]["fov"] == 10
    nw = one["quarters"][1]
    assert nw["dec"] > one["dec"] and nw["ra"] < one["ra"]  # north is larger Dec, west smaller RA
    # a guest's look is never counted; a signed-in look needs the quarter and the person's own confirm
    assert c.post(f"/api/grid/cell/{cid}/viewed", json={"quarter": "NW", "confirm": True}).status_code == 401
    who["user"] = {"id": "u1"}
    assert c.post(f"/api/grid/cell/{cid}/viewed", json={"quarter": "NW"}).status_code == 422  # no confirm
    assert c.post(f"/api/grid/cell/{cid}/viewed", json={"quarter": "XX", "confirm": True}).status_code == 422
    r = c.post(f"/api/grid/cell/{cid}/viewed", json={"quarter": "NW", "confirm": True, "result": "nothing"}).json()
    assert r == {"viewers": 1, "quarter": "NW", "quarters": 1}
    for q in ("NE", "SW"):
        c.post(f"/api/grid/cell/{cid}/viewed", json={"quarter": q, "confirm": True})
    assert c.get("/api/grid/meta").json()["counts"]["viewed"] == 0  # three quarters: not yet viewed by people
    who["user"] = {"id": "u2"}
    assert c.post(f"/api/grid/cell/{cid}/viewed", json={"quarter": "SE", "confirm": True}).json()["quarters"] == 4
    assert c.get("/api/grid/meta").json()["counts"]["viewed"] == 1 and c.get(f"/api/grid/cell/{cid}").json()["viewers"] == 2
    assert {q["id"]: q["mine"] for q in c.get(f"/api/grid/cell/{cid}").json()["quarters"]} == {
        "NE": False,
        "NW": False,
        "SE": True,
        "SW": False,
    }
    assert {x["id"]: x["quarters"] for x in c.get("/api/grid/coverage").json()["cells"]}[cid] == 4
    assert c.get("/api/grid/cell/999-0000").status_code == 404
    other = grid.cell_at(120, -30).id
    c.post(f"/api/grid/cell/{other}/viewed", json={"quarter": "NE", "confirm": True})
    cov2 = {x["id"]: x for x in c.get("/api/grid/coverage").json()["cells"]}
    assert cov2[other]["state"] == "new" and cov2[other]["quarters"] == 1


def test_old_views_migrate_and_anonymous_ones_do_not_count(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "product.sqlite"
    with sqlite3.connect(path) as c:
        c.executescript(
            "CREATE TABLE grid_views (cell_id TEXT NOT NULL, viewer TEXT NOT NULL, result TEXT, at REAL NOT NULL,"
            " PRIMARY KEY (cell_id, viewer));"
            "INSERT INTO grid_views VALUES ('294-0148','user:a','nothing',1.0), ('294-0148','anon:x',NULL,2.0);"
        )
    db = GridDB(path)
    assert db.viewers(["294-0148"]) == {"294-0148": 1}  # the anonymous cookie is not a viewer
    assert db.quarters_viewed(["294-0148"])["294-0148"] == {"NE": 1, "NW": 1, "SE": 1, "SW": 1}  # a whole-square view of before
    assert db.quarters_of("294-0148", "user:a") == {"NE", "NW", "SE", "SW"}
    assert db.counts()["viewed"] == 1


def test_watchdog_kills_a_tree_over_the_cap(tmp_path: Path) -> None:
    import subprocess
    import sys
    import time

    from service.sweep import Watchdog

    p = subprocess.Popen(
        [sys.executable, "-c", "import time; b = bytearray(300 * 1024 * 1024); b[::4096] = b'x' * len(b[::4096]); time.sleep(60)"]
    )
    dog = Watchdog(p.pid, cap_mb=100, period_s=0.1)
    dog.start()
    t0 = time.monotonic()
    while p.poll() is None and time.monotonic() - t0 < 20:
        time.sleep(0.1)
    assert dog.tripped and p.poll() is not None


def test_bar_at(tmp_path: Path) -> None:
    db = GridDB(tmp_path / "p.sqlite")
    assert grid.bar_at(db, 50, 8) is None
    db.put(grid.cell_at(50, 8).id, "searched", bar={"kind": "sweep", "mover_sigma": 5.0}, source="sweep")
    assert grid.bar_at(db, 50.01, 8.01) == {"kind": "sweep", "mover_sigma": 5.0}


def test_privacy_and_terms_are_server_pages() -> None:
    from service.routes_legal import make_legal_router

    app = FastAPI()
    app.include_router(make_legal_router())
    c = TestClient(app)
    for path, word in (("/privacy", "Resend"), ("/terms", "leads, not discoveries")):
        r = c.get(path)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/html") and word in r.text
