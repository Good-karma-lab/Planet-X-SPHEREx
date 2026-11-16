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
    app.include_router(make_grid_router(SimpleNamespace(index=None), db))
    c = TestClient(app)
    meta = c.get("/api/grid/meta").json()
    assert meta["scheme"] == grid.SCHEME and meta["counts"]["searched"] == 1 and meta["counts"]["with_finds"] == 1
    cov = c.get("/api/grid/coverage").json()["cells"]
    assert cov == [{"id": cid, "state": "searched", "finds": 2, "viewers": 0}]
    withc = c.get("/api/grid/coverage", params={"corners": 1}).json()["cells"]
    assert withc[0]["corners"] == grid.by_id(cid).corners()
    assert meta["scheme_params"]["n_bands"] == 540
    cells = c.get("/api/grid/cells", params={"ra": 50, "dec": 8, "radius": 0.5}).json()["cells"]
    assert cells[0]["id"] == cid and cells[0]["state"] == "searched" and len(cells[0]["corners"]) == 4
    one = c.get(f"/api/grid/cell/{cid}").json()
    assert one["field_key"] == "k" and one["bar"]["partial"] is True and one["open"]["fov"] == 20
    assert c.post(f"/api/grid/cell/{cid}/viewed", json={"result": "nothing"}).json() == {"viewers": 1}
    assert c.post(f"/api/grid/cell/{cid}/viewed", json={"result": "marks"}).json() == {"viewers": 1}  # same viewer (cookie)
    c2 = TestClient(app)
    assert c2.post(f"/api/grid/cell/{cid}/viewed", json={}).json() == {"viewers": 2}
    assert c.get("/api/grid/cell/999-0000").status_code == 404
    other = grid.cell_at(120, -30).id
    c.post(f"/api/grid/cell/{other}/viewed", json={})
    assert {x["id"]: x["state"] for x in c.get("/api/grid/coverage").json()["cells"]}[other] == "new"


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
