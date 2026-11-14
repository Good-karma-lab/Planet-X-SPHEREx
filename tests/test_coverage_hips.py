"""Coverage map cells and the survey tile paths of the globe."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from service.coverage import ang2pix_nested, coverage_payload, index_key
from service.upstream.hips import HipsTiles


def test_nested_pixels_of_known_places() -> None:
    # order 0: twelve base faces; the north pole lies in faces 0-3, the south pole in 8-11, the equator at RA 0 in face 4
    assert ang2pix_nested(0, 45.0, 89.0) == 0
    assert ang2pix_nested(0, 0.0, 0.0) == 4
    assert 8 <= ang2pix_nested(0, 10.0, -89.0) <= 11
    # a child pixel lies inside its parent: nested numbering divides by four per order
    for ra, dec in ((269.45, 4.69), (331.0, -56.8), (133.8, -7.2), (12.0, 70.0)):
        p3 = ang2pix_nested(3, ra, dec)
        assert ang2pix_nested(2, ra, dec) == p3 // 4
        assert 0 <= p3 < 12 * 4**3


def test_hips_tile_paths() -> None:
    assert HipsTiles.relative("dss2", 3, "allsky") == "Norder3/Allsky.jpg"
    assert HipsTiles.relative("wise", 5, "12000") == "Norder5/Dir10000/Npix12000.jpg"
    assert HipsTiles.relative("spx88", 3, "allsky") == "Norder3/Allsky_88.png"
    assert HipsTiles.relative("spx8", 5, "100") == "Norder5/Dir0/Npix100_8.png"
    for bad in (("nope", 3, "allsky"), ("dss2", 4, "allsky"), ("wise", 9, "1"), ("dss2", 3, "../x"), ("dust", 3, "999")):
        with pytest.raises(ValueError):
            HipsTiles.relative(*bad)


def _index(path: Path, rows: list[tuple[str, float, float]]) -> None:
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE files (release TEXT, cra REAL, cdec REAL)")
    db.executemany("INSERT INTO files VALUES (?, ?, ?)", rows)
    db.commit()
    db.close()


def test_coverage_payload_is_saved_beside_the_index_and_follows_it(tmp_path: Path) -> None:
    ix = tmp_path / "exposures.sqlite"
    _index(ix, [("dr1", 10.0, 5.0), ("dr1", 10.5, 5.2), ("qr3", 200.0, -40.0)])
    body, etag, key = coverage_payload(ix)
    got = json.loads(body)
    assert got["releases"] == ["dr1", "qr3"] and sum(sum(v) for v in got["cells"].values()) == 3
    assert (tmp_path / "coverage_map.json").is_file() and key == index_key(ix)
    # the second call reads the disk copy: same bytes and the same ETag
    body2, etag2, _ = coverage_payload(ix)
    assert (body2, etag2) == (body, etag)
    # the index changes (a new file): the key changes and the map is computed again
    db = sqlite3.connect(ix)
    db.execute("INSERT INTO files VALUES ('qr3', 201.0, -41.0)")
    db.commit()
    db.close()
    body3, etag3, key3 = coverage_payload(ix)
    assert key3 != key and etag3 != etag and sum(sum(v) for v in json.loads(body3)["cells"].values()) == 4


def test_coverage_map_route_serves_an_etag(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from indexer.store import Index, Row
    from service.app import create_app
    from service.config import Config

    ix = Index(tmp_path / "index" / "exposures.sqlite")
    ix.add([Row("dr1/a.fits", "dr1", "w", "v", 1, 1, 2, 61000.0, 61000.1, 10, "", (10.0, 10.2, 10.2, 10.0), (5.0, 5.0, 5.2, 5.2))])
    ix.commit()
    ix.close()
    app = create_app(Config(store_dir=tmp_path, web_dist=tmp_path / "none", serve_web=False))
    with TestClient(app) as c:
        r = c.get("/api/v1/coverage/map")
        assert r.status_code == 200 and r.json()["releases"] == ["dr1"] and r.headers["etag"]
        again = c.get("/api/v1/coverage/map", headers={"If-None-Match": r.headers["etag"]})
        assert again.status_code == 304 and not again.content
