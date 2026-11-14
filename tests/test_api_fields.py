"""Contract test of the field life cycle on synthetic Level-2 files served by a fake bucket."""

from __future__ import annotations

import io
import time
from pathlib import Path
from typing import Any

from astropy.io import fits
from fastapi.testclient import TestClient

from engine.spx.footprint import footprint
from indexer.irsa import Row
from indexer.store import Index
from service.app import create_app
from service.config import Config
from service.jobs.scheduler import Scheduler
from service.products.sxp import decode
from tests.fake_s3 import FakeBucket
from tests.synth_l2 import make_file

RA, DEC = 120.0, 10.0


def setup(tmp_path: Path) -> FakeBucket:
    bucket = FakeBucket()
    ix = Index(tmp_path / "index" / "exposures.sqlite")
    rows = []
    for p, mjd0 in enumerate((61000.0, 61200.0)):
        for k in range(4):
            key = f"dr1/level2/2025W40_1A/l2b-v27-2026-235/2/level2_2025W40_1A_{p * 10 + k:04d}_1D2_spx_l2b-v27-2026-235.fits"
            data, _ = make_file("v27", n=256, detector=2, ra=RA, dec=DEC, mjd=mjd0 + 0.01 * k, seed=p * 10 + k)
            bucket.objects[key] = data
            with fits.open(io.BytesIO(data)) as hl:
                fp = footprint(hl["IMAGE"].header)
            rows.append(
                Row(
                    key,
                    "dr1",
                    "2025W40_1A",
                    "l2b-v27-2026-235",
                    p * 10 + k,
                    1,
                    2,
                    mjd0 + 0.01 * k,
                    mjd0 + 0.01 * k + 0.001,
                    len(data),
                    "",
                    tuple(fp.ra[[0, 3, 6, 9]]),
                    tuple(fp.dec[[0, 3, 6, 9]]),
                )
            )
    ix.add(rows)
    ix.commit()
    ix.close()
    return bucket


def test_field_life_cycle(tmp_path: Path) -> None:
    bucket = setup(tmp_path)
    app = create_app(Config(store_dir=tmp_path, web_dist=tmp_path / "none", serve_web=False), scheduler=Scheduler(), s3=bucket.s3())
    with TestClient(app) as c:
        r = c.get("/api/v1/fields", params={"ra": RA, "dec": DEC, "fov": 2})
        assert r.status_code == 200
        body = r.json()
        key, job = body["key"], body["job"]
        assert body["params"]["fov"] == 2.0
        second = c.get("/api/v1/fields", params={"ra": RA, "dec": DEC, "fov": 2}).json()
        assert second.get("job", {}).get("id", job["id"]) == job["id"] or second["state"] == "done"  # attached, or already finished
        for _ in range(300):
            v = c.get(f"/api/v1/jobs/{job['id']}").json()
            if v["state"] in ("done", "error"):
                break
            time.sleep(0.1)
        assert v["state"] == "done", v
        assert {"quick_p0", "quick_p1", "picture_p0", "picture_p1", "dets_p0", "meta", "diff"} <= set(v["parts"])
        assert v["parts"].index("quick_p0") < v["parts"].index("picture_p0")  # the quick picture comes first
        pic = c.get(f"/api/v1/fields/{key}/parts/picture_p0")
        assert pic.status_code == 200 and pic.headers["cache-control"].endswith("immutable")
        meta, arrays = decode(pic.content)
        assert meta == {"pass": 0} and arrays["rgb"].shape[0] == 3
        m = c.get(f"/api/v1/fields/{key}/parts/meta.json").json()
        assert len(m["passes"]) == 2 and m["cdet"] == [2] and m["grid"]["orientation"].startswith("row 0 north")
        again = c.get("/api/v1/fields", params={"ra": RA, "dec": DEC, "fov": 2}).json()
        assert again["state"] == "done" and again["key"] == key  # the finished product, no new job
        assert c.get(f"/api/v1/fields/{key}/parts/nothing").status_code == 404
        assert c.get("/api/v1/jobs/j999").status_code == 404


def test_field_built_in_worker_processes(tmp_path: Path, monkeypatch: Any) -> None:
    """The same build with the real process pool: every compute step crosses to a worker (all its inputs and outputs pickle)."""
    monkeypatch.setenv("SKY_WORKERS", "2")
    monkeypatch.setenv("SKY_WARM", "0")
    bucket = setup(tmp_path)
    app = create_app(Config(store_dir=tmp_path, web_dist=tmp_path / "none", serve_web=False), s3=bucket.s3())
    with TestClient(app) as c:
        body = c.get("/api/v1/fields", params={"ra": RA, "dec": DEC, "fov": 2}).json()
        for _ in range(600):
            v = c.get(f"/api/v1/jobs/{body['job']['id']}").json()
            if v["state"] in ("done", "error"):
                break
            time.sleep(0.1)
        assert v["state"] == "done", v
        assert {"quick_p0", "picture_p1", "dets_p0", "bands_p1", "meta", "diff"} <= set(v["parts"])
        assert c.get("/api/v1/health").json()["cpu"]["waiting"] == 0
