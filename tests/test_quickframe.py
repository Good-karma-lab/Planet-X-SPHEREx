"""The first frame of a cold field from the DR1 all-sky cube (service/quicklook.frame), on a synthetic cube tile read by byte ranges."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits
from PIL import Image

from service.quicklook import TILE, QuickLook, TileLayout, frame


class FakeS3:
    def __init__(self, blob: bytes) -> None:
        self.blob, self.asked = blob, 0

    async def get_range(self, key: str, start: int, end: int) -> bytes:
        self.asked += end - start
        return self.blob[start:end]


def _tile(ra: float, dec: float, planes: int) -> tuple[TileLayout, bytes, np.ndarray]:
    h = fits.Header()
    h["NAXIS"], h["WCSAXES"] = 2, 2
    h["CTYPE1"], h["CTYPE2"] = "RA---TAN", "DEC--TAN"
    h["CRVAL1"], h["CRVAL2"] = ra, dec
    h["CRPIX1"] = h["CRPIX2"] = TILE / 2 + 0.5
    h["CDELT1"], h["CDELT2"] = -12.9 / 3600, 12.9 / 3600
    h["CUNIT1"] = h["CUNIT2"] = "deg"
    cube = np.zeros((planes, TILE, TILE), np.float32)
    _yy, xx = np.mgrid[:TILE, :TILE]
    for k in range(planes):
        cube[k] = 1.0 + 0.001 * xx
    cube[:, TILE // 2 - 2 : TILE // 2 + 3, TILE // 2 + 40 : TILE // 2 + 45] = 50.0  # a star 40 cube pixels west of the centre
    cube[:, TILE // 2, TILE // 2] = np.nan  # a saturated core: no data
    blob = b"\0" * 2880 + cube.astype(">f4").tobytes()
    lam = list(np.linspace(0.75, 5.0, planes))
    return TileLayout("k", h.tostring(sep="\n"), 2880, planes, [], lam), blob, cube


def test_a_first_frame_reads_only_the_rows_it_needs_and_lies_on_the_field_grid(tmp_path: Path) -> None:
    ra, dec = 120.0, 30.0
    lay, blob, _ = _tile(ra, dec, 102)
    s3 = FakeS3(blob)
    ql = QuickLook(s3, tmp_path)  # type: ignore[arg-type]

    async def layout(npix: int) -> TileLayout:
        return lay

    ql.layout = layout  # type: ignore[method-assign]
    png, info = asyncio.run(frame(ql, ra, dec, 20))
    img = np.asarray(Image.open(io.BytesIO(png)))
    m = info["size"]
    assert img.shape == (m, m, 4) and info["grid"]["n"] == 784 and m == 187  # 20′ at 6.45″ a frame pixel
    assert info["covered"] > 0.99
    # three planes of the rows the field spans (about 93 of the 512), never the whole tile
    assert s3.asked == info["bytes_read"] and s3.asked < 3 * 100 * TILE * 4
    # north up, east left: the star west of the centre is right of the frame's centre, on its middle row
    lum = img[..., :3].astype(float).sum(axis=-1)
    ys, xs = np.nonzero(lum >= 0.9 * lum.max())  # the star's plateau (5 cube pixels, about 10 of the frame's)
    assert abs(ys.mean() - m / 2) <= 3 and xs.mean() - m / 2 > 70
    # the saturated core is transparent, the rest opaque
    assert img[..., 3].min() == 0 and (img[..., 3] == 255).mean() > 0.99
    assert json.loads(json.dumps(info))["planes_um"] == [4.5, 2.8, 1.3]


def test_the_route_caches_the_frame(tmp_path: Path, monkeypatch: Any) -> None:
    from service import quicklook

    calls: list[float] = []

    async def fake(ql: Any, ra: float, dec: float, fov: float) -> tuple[bytes, dict[str, Any]]:
        calls.append(fov)
        return b"\x89PNG-fake", {"size": 1, "seconds": 0.1}

    monkeypatch.setattr(quicklook, "frame", fake)
    from fastapi.testclient import TestClient

    from service.app import create_app
    from service.config import Config
    from service.jobs.scheduler import Scheduler
    from tests.test_api_fields import setup

    bucket = setup(tmp_path)
    app = create_app(Config(store_dir=tmp_path, web_dist=tmp_path / "none", serve_web=False), scheduler=Scheduler(), s3=bucket.s3())
    with TestClient(app) as c:
        r1 = c.get("/api/v1/fields/quick", params={"ra": 10.0, "dec": 20.0, "fov": 10})
        r2 = c.get("/api/v1/fields/quick", params={"ra": 10.0, "dec": 20.0, "fov": 10})
    assert r1.status_code == 200 and r1.headers["content-type"] == "image/png" and r1.content == b"\x89PNG-fake"
    assert json.loads(r1.headers["x-quick"])["size"] == 1 and r2.content == r1.content and calls == [10.0]
