"""Cone searches in HEALPix-partitioned Parquet: geometry, file names, selection, the AllWISE photometry table."""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from engine.extras import allwise
from engine.extras.cloudcat import HealpixDataset, cone, cone_pixels, separation_arcsec
from service.coverage import ang2pix_nested


def _edge_ra() -> float:
    """An RA on the equator where the order-5 pixel changes (with a margin of 0.0003 deg on both sides)."""
    for r in np.arange(40.0, 50.0, 0.001):
        x = float(r)
        if ang2pix_nested(5, x - 0.0003, 0.0) != ang2pix_nested(5, x + 0.0003, 0.0):
            return x
    raise AssertionError("no edge found")


def test_a_small_cone_far_from_a_pixel_edge_is_one_pixel_and_a_cone_on_an_edge_is_several() -> None:
    assert len(cone_pixels(5, 269.4479, 4.7687, 3 / 3600)) == 1
    # a point on the boundary between order-5 pixels: its cone must reach the pixels on both sides
    e = _edge_ra()
    pix = cone_pixels(5, e, 0.0, 5 / 3600)
    assert ang2pix_nested(5, e - 0.0003, 0.0) in pix and ang2pix_nested(5, e + 0.0003, 0.0) in pix and len(pix) >= 2


def test_cone_pixels_wrap_over_ra_zero() -> None:
    pix = cone_pixels(5, 359.9999, 0.0, 30 / 3600)
    assert ang2pix_nested(5, 359.9999, 0.0) in pix and ang2pix_nested(5, 0.001, 0.0) in pix


def test_separation_is_exact_at_every_scale() -> None:
    assert separation_arcsec(np.array([10.0]), np.array([0.0]), 10.0, 1.0 / 3600)[0] == pytest.approx(1.0, rel=1e-6)
    assert separation_arcsec(np.array([0.0]), np.array([80.0]), 180.0, 80.0)[0] == pytest.approx(20 * 3600.0, rel=1e-6)
    # a degree of RA at dec 60 is half a degree on the sky
    assert separation_arcsec(np.array([11.0]), np.array([60.0]), 10.0, 60.0)[0] / 3600 == pytest.approx(0.5, rel=2e-3)


def test_the_file_name_follows_irsas_layout() -> None:
    ds = HealpixDataset("b/p/wise-allwise.parquet")
    assert ds.path(6390) == "b/p/wise-allwise.parquet/healpix_k0=6/healpix_k5=6390/part0.snappy.parquet"
    assert ds.path(0).endswith("healpix_k0=0/healpix_k5=0/part0.snappy.parquet")


def _write(root: Path, ds: HealpixDataset, pix: int, rows: dict[str, list[object]]) -> None:
    p = Path(ds.path(pix))
    p.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(rows), p)


def _reader(root: Path) -> allwise.Reader:
    def read(path: str, columns: Sequence[str]) -> pa.Table:
        f = Path(path)
        if not f.exists():
            return pa.table({c: [] for c in columns})
        return pq.read_table(f, columns=list(columns))

    return read


def test_cone_returns_the_rows_inside_nearest_first_with_their_separation(tmp_path: Path) -> None:
    ds = HealpixDataset(str(tmp_path / "cat.parquet"))
    ra0, dec0 = 120.0, 30.0
    pix = ang2pix_nested(5, ra0, dec0)
    d = 1 / 3600
    _write(
        tmp_path,
        ds,
        pix,
        {"ra": [ra0, ra0 + 2 * d / math.cos(math.radians(dec0)), ra0 + 20 * d], "dec": [dec0 + d, dec0, dec0], "name": ["a", "b", "c"]},
    )
    rows = cone(ds, _reader(tmp_path), ["name"], ra0, dec0, 3.0)
    assert [r["name"] for r in rows] == ["a", "b"]
    assert rows[0]["sep_arcsec"] == pytest.approx(1.0, abs=1e-3) and rows[1]["sep_arcsec"] == pytest.approx(2.0, abs=1e-3)
    assert cone(ds, _reader(tmp_path), ["name"], ra0, dec0, 0.5) == []


def test_a_cone_across_a_pixel_edge_reads_both_files(tmp_path: Path) -> None:
    ds = HealpixDataset(str(tmp_path / "cat.parquet"))
    ra_edge = _edge_ra()
    left, right = ang2pix_nested(5, ra_edge - 0.0003, 0.0), ang2pix_nested(5, ra_edge + 0.0003, 0.0)
    assert left != right
    _write(tmp_path, ds, left, {"ra": [ra_edge - 0.0003], "dec": [0.0], "name": ["west"]})
    _write(tmp_path, ds, right, {"ra": [ra_edge + 0.0003], "dec": [0.0], "name": ["east"]})
    rows = cone(ds, _reader(tmp_path), ["name"], ra_edge, 0.0, 5.0)
    assert sorted(r["name"] for r in rows) == ["east", "west"]


def test_allwise_photometry_by_band_marks_upper_limits() -> None:
    row = {
        "w1mpro": 14.8,
        "w1sigmpro": 0.03,
        "w2mpro": 14.9,
        "w2sigmpro": 0.05,
        "w3mpro": 12.0,
        "w3sigmpro": None,
        "w4mpro": 8.0,
        "w4sigmpro": None,
        "ph_qual": "AAUX",
        "j_m_2mass": 15.3,
        "j_msig_2mass": 0.05,
        "h_m_2mass": 14.9,
        "h_msig_2mass": None,
        "k_m_2mass": None,
        "k_msig_2mass": None,
    }
    p = allwise.photometry(row)
    assert p["W1"] == {"mag": 14.8, "err": 0.03, "qual": "A"}
    assert p["W3"]["qual"] == "U" and p["W4"]["qual"] == "X"
    assert p["J"] == {"mag": 15.3, "err": 0.05, "qual": None}
    assert p["H"]["qual"] == "U" and p["Ks"]["mag"] is None


def test_allwise_sources_use_the_catalogue_layout(tmp_path: Path) -> None:
    ds = HealpixDataset(str(tmp_path / "wise-allwise.parquet"))
    ra0, dec0 = 269.4, 4.7
    cols: dict[str, list[object]] = {c: [None] for c in allwise.COLUMNS}
    cols["designation"] = ["J175736.00+044200.0"]
    cols["ph_qual"] = ["AAUU"]
    cols["w1mpro"] = [14.0]
    cols["ra"], cols["dec"] = [ra0], [dec0]
    _write(tmp_path, ds, ang2pix_nested(5, ra0, dec0), cols)
    out = cone(ds, _reader(tmp_path), allwise.COLUMNS, ra0, dec0 + 1 / 3600, 3.0)
    assert out[0]["designation"].startswith("J1757") and out[0]["sep_arcsec"] == pytest.approx(1.0, abs=1e-3)


@pytest.mark.network
def test_live_allwise_finds_the_source_at_barnards_star_neighbour() -> None:
    rows = allwise.sources(269.4485126, 4.7629303, 3.0)
    assert rows and rows[0]["designation"] == "J175747.64+044546.5" and rows[0]["ph_qual"].startswith("AA")


def test_the_xmatch_route_moves_the_place_to_each_epoch_and_reports_each_part(tmp_path: Path) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from service.routes_extras import Extras, register

    seen: dict[str, list[tuple[float, float, float]]] = {"wise": [], "gaia": []}

    def wise_reader(path: str, columns: Sequence[str]) -> pa.Table:
        return pa.table({c: [] for c in columns})

    def gaia_cone(ra: float, dec: float, radius_deg: float) -> list[dict[str, object]]:
        seen["gaia"].append((ra, dec, radius_deg))
        return [
            {"source_id": 2, "ra": ra, "dec": dec + 2 / 3600, "phot_g_mean_mag": 9.0},
            {"source_id": 1, "ra": ra, "dec": dec + 1 / 3600},
        ]

    app = FastAPI()
    register(app, Extras(allwise_reader=wise_reader, gaia_cone=gaia_cone))
    c = TestClient(app)
    # a star of 10 arcsec/yr north: Gaia's epoch (2016.0) is 16 years after J2000
    r = c.get("/api/v1/xmatch", params={"ra": 100.0, "dec": 10.0, "pmra": 0, "pmdec": 10000, "radius": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["allwise"]["state"] == "none" and body["gaia"]["state"] == "ok"
    assert body["gaia"]["dec"] == pytest.approx(10.0 + 16.0 * 10.0 / 3600, abs=1e-6)
    assert body["gaia"]["nearest"]["source_id"] == 1 and body["gaia"]["nearest"]["sep_arcsec"] == pytest.approx(1.0, abs=1e-3)
    assert body["allwise"]["dec"] == pytest.approx(10.0 + 10.5 * 10.0 / 3600, abs=1e-6)
    n = len(seen["gaia"])
    assert c.get("/api/v1/xmatch", params={"ra": 100.0, "dec": 10.0, "pmra": 0, "pmdec": 10000, "radius": 3}).json() == body
    assert len(seen["gaia"]) == n  # cached
    assert c.get("/api/v1/xmatch", params={"ra": 1, "dec": 1, "cats": "nope"}).status_code == 422
    only = c.get("/api/v1/xmatch", params={"ra": 1, "dec": 1, "cats": "gaia"}).json()
    assert "allwise" not in only and only["gaia"]["state"] == "ok"


def test_a_failing_catalogue_is_unavailable_and_not_cached() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from service.routes_extras import Extras, register

    calls = {"n": 0}

    def broken(ra: float, dec: float, radius_deg: float) -> list[dict[str, object]]:
        calls["n"] += 1
        raise OSError("bucket down")

    app = FastAPI()
    register(app, Extras(allwise_reader=lambda p, c: pa.table({k: [] for k in c}), gaia_cone=broken))
    c = TestClient(app)
    a = c.get("/api/v1/xmatch", params={"ra": 5, "dec": 5}).json()
    assert a["gaia"]["state"] == "unavailable" and "OSError" in a["gaia"]["error"] and a["allwise"]["state"] == "none"
    c.get("/api/v1/xmatch", params={"ra": 5, "dec": 5})
    assert calls["n"] == 2  # asked again: an unavailable answer is not kept
