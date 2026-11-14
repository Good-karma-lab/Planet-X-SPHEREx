"""The quick-look spectrum from the all-sky cube: tile addressing, header walk, pixel reads, the route."""

from __future__ import annotations

import asyncio
import struct
from typing import Any

import numpy as np
import pytest
from astropy.io import fits
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.extras.cube import (
    CubeError,
    CubeSource,
    celestial_wcs,
    check_layout,
    parse_channels,
    read_layout,
    tile_key,
    tile_of,
)
from service.routes_extras import Extras, register
from tests.fake_s3 import FakeBucket

ROOT = "dr1/hips5_cube/hips_test"
NCHAN = 4
N = 512
WCS_CARDS: dict[str, Any] = {
    "CRPIX1": 8192.5, "CRPIX2": 8704.5, "PC1_1": -0.00274658203125, "PC1_2": -0.00274658203125, "PC2_1": 0.00274658203125,
    "PC2_2": -0.00274658203125, "CDELT1": 1.0, "CDELT2": 1.0, "CUNIT1": "deg", "CUNIT2": "deg", "CTYPE1": "RA---HPX",
    "CTYPE2": "DEC--HPX", "CRVAL1": 0.0, "CRVAL2": 0.0, "PV2_1": 4.0, "PV2_2": 3.0, "LONPOLE": 0.0, "LATPOLE": 90.0,
}  # fmt: skip
LAM = [0.75, 1.0, 1.5, 4.9]


def _hdu_header(extname: str, bitpix: int, extra: dict[str, Any] | None = None) -> bytes:
    h = fits.Header()
    h["XTENSION"] = "IMAGE"
    h["BITPIX"] = bitpix
    h["NAXIS"] = 3
    h["NAXIS1"], h["NAXIS2"], h["NAXIS3"] = N, N, NCHAN
    h["PCOUNT"], h["GCOUNT"] = 0, 1
    h["EXTNAME"] = extname
    for k, v in WCS_CARDS.items():
        h[k] = v
    for k, v in (extra or {}).items():
        h[k] = v
    return h.tostring(sep="", endcard=True, padding=True).encode("ascii")


def _pad(b: bytes) -> bytes:
    return b + b"\0" * (-len(b) % 2880)


def synthetic_tile(blank: tuple[int, ...] = (2,)) -> tuple[bytes, np.ndarray, np.ndarray]:
    """A tile of 512 x 512 x 4 with a value that tells the pixel and the channel: sb = 10 k + x/1000 + y/1e6, flags = k + 1."""
    yy, xx = np.mgrid[0:N, 0:N]
    img = np.stack([10.0 * k + xx / 1000.0 + yy / 1e6 for k in range(NCHAN)]).astype(">f4")
    for k in blank:
        img[k] = np.nan
    flg = np.stack([np.full((N, N), (k + 1) * (4 if k in blank else 1)) for k in range(NCHAN)]).astype(">i4")
    primary = fits.Header()
    primary["SIMPLE"], primary["BITPIX"], primary["NAXIS"], primary["EXTEND"] = True, 8, 0, True
    prim = primary.tostring(sep="", endcard=True, padding=True).encode("ascii")
    mask = {"MSKN0000": "TRANSIENT", "MSKM0000": 1, "MSKN0001": "OVERFLOW", "MSKM0001": 2, "MSKN0002": "SUR_ERROR", "MSKM0002": 4}
    chan = np.zeros(
        NCHAN,
        dtype=[("det", ">i2"), ("sub", ">i2"), ("wl", ">f4"), ("lo", ">f4"), ("hi", ">f4"), ("r", ">f4"), ("rs", ">f4"), ("bw", ">f4")],
    )
    chan["det"] = [1, 1, 3, 6]
    chan["wl"] = LAM
    chan["bw"] = 0.02
    th = fits.Header()
    th["XTENSION"], th["BITPIX"], th["NAXIS"], th["NAXIS1"], th["NAXIS2"] = "BINTABLE", 8, 2, 28, NCHAN
    th["PCOUNT"], th["GCOUNT"], th["TFIELDS"], th["EXTNAME"] = 0, 1, 0, "SPECTRAL_CHANNELS"
    blob = (
        prim
        + _pad(_hdu_header("IMAGE", -32) + img.tobytes())
        + _pad(_hdu_header("NHITS", 32) + np.zeros((NCHAN, N, N), ">i4").tobytes())
        + _pad(_hdu_header("FLAGS", 32, mask) + flg.tobytes())
        + _pad(th.tostring(sep="", endcard=True, padding=True).encode("ascii") + chan.tobytes())
        + _pad(fits.Header({"XTENSION": "BINTABLE"}).tostring(sep="", endcard=True, padding=True).encode("ascii"))
    )
    return blob, img.astype(float), flg


def _bucket() -> tuple[FakeBucket, np.ndarray, np.ndarray]:
    blob, img, flg = synthetic_tile()
    b = FakeBucket({tile_key(ROOT, 0): blob})
    return b, img, flg


def _tile0_place(x: int, y: int) -> tuple[float, float]:
    ra, dec = celestial_wcs(fits.Header(WCS_CARDS)).all_pix2world(x, y, 0)
    return float(ra) % 360.0, float(dec)


def test_tile_key_counts_directories_in_tens_of_thousands() -> None:
    assert tile_key("a/b/", 0) == "a/b/Norder5/Dir0/Npix0_cube.fits"
    assert tile_key("a/b", 7945) == "a/b/Norder5/Dir0/Npix7945_cube.fits"
    assert tile_key("a", 12288 - 1).endswith("Dir10000/Npix12287_cube.fits")


def test_the_centre_of_tile_zero_is_in_order_5_pixel_zero() -> None:
    for x, y in [(255, 255), (10, 255), (500, 255), (255, 10), (255, 500)]:
        assert tile_of(*_tile0_place(x, y)) == 0


def test_the_wcs_returns_the_pixel_it_was_made_from() -> None:
    from engine.extras.cube import pixel_of

    for x, y in [(255, 255), (3, 400), (500, 17)]:
        ra, dec = _tile0_place(x, y)
        assert pixel_of(fits.Header(WCS_CARDS), ra, dec, N, N) == (x, y)
    assert pixel_of(fits.Header(WCS_CARDS), 200.0, -40.0, N, N) is None


def test_channel_table_is_read_big_endian() -> None:
    row = struct.pack(">hhffffff", 3, 7, 1.5, 1.4, 1.6, 40.0, 2.0, 0.04)
    ch = parse_channels(row * 2, 2)
    assert list(ch.detector) == [3, 3] and float(ch.wavelength[0]) == pytest.approx(1.5) and float(ch.bandwidth[1]) == pytest.approx(0.04)


def test_the_header_walk_finds_the_cubes_and_the_flag_names() -> None:
    bucket, img, flg = _bucket()

    async def go() -> None:
        s3 = bucket.s3()
        layout, header, channels = await read_layout(s3, tile_key(ROOT, 0))
        assert (layout.nx, layout.ny, layout.nchan) == (N, N, NCHAN)
        first = await s3.get_range(tile_key(ROOT, 0), layout.image, layout.image + 4)
        assert struct.unpack(">f", first)[0] == pytest.approx(float(img[0, 0, 0]))
        assert layout.flags > layout.image and layout.channels > layout.flags
        last_flag = await s3.get_range(tile_key(ROOT, 0), layout.flags + 4 * (NCHAN * N * N - 1), layout.flags + 4 * NCHAN * N * N)
        assert struct.unpack(">i", last_flag)[0] == int(flg[NCHAN - 1, N - 1, N - 1])
        assert layout.mask_names == {1: "TRANSIENT", 2: "OVERFLOW", 4: "SUR_ERROR"}
        assert header["EXTNAME"] == "IMAGE" and list(channels.wavelength) == pytest.approx(LAM)
        await s3.close()

    asyncio.run(go())


def test_another_tile_with_another_layout_is_refused() -> None:
    bucket, _, _ = _bucket()
    blob = bucket.objects[tile_key(ROOT, 0)]
    bucket.objects[tile_key(ROOT, 1)] = blob[:2880] + blob[2880:].replace(
        b"NAXIS3  =                    4",
        b"NAXIS3  =                    5",
        1,
    )

    async def go() -> None:
        s3 = bucket.s3()
        layout, _, _ = await read_layout(s3, tile_key(ROOT, 0))
        with pytest.raises(CubeError):
            await check_layout(s3, tile_key(ROOT, 1), layout)
        await s3.close()

    asyncio.run(go())


def test_a_spectrum_is_one_four_byte_read_per_channel_and_the_right_pixel() -> None:
    bucket, img, flg = _bucket()
    ra, dec = _tile0_place(100, 200)

    async def go() -> None:
        s3 = bucket.s3()
        src = CubeSource(s3, ROOT)
        sp = await src.spectrum(ra, dec)
        assert sp is not None and (sp.x, sp.y, sp.tile) == (100, 200, 0)
        assert [v is None for v in sp.sb] == [False, False, True, False]
        assert sp.sb[1] == pytest.approx(float(img[1, 200, 100]), rel=1e-6)
        assert sp.flags == [None, None, int(flg[2, 200, 100]), None]  # "bad": only the empty channel's flags
        n_before = len(bucket.requests)
        sp_all = await src.spectrum(ra, dec, flags="all")
        assert sp_all is not None and sp_all.flags == [int(flg[k, 200, 100]) for k in range(NCHAN)]
        new = bucket.requests[n_before:]
        sizes = [int(r[2].split("=")[1].split("-")[1]) - int(r[2].split("=")[1].split("-")[0]) + 1 for r in new if r[2]]
        assert sizes == [4] * (2 * NCHAN)  # a known layout: nothing but one four-byte read per channel and cube
        await s3.close()

    asyncio.run(go())


def test_the_route_answers_and_caches() -> None:
    bucket, img, _ = _bucket()
    app = FastAPI()
    extras = register(app, Extras(bucket.s3(), ROOT))
    c = TestClient(app)
    ra, dec = _tile0_place(100, 200)
    r = c.get("/api/v1/cube/spectrum", params={"ra": ra, "dec": dec})
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "ok" and body["release"] == "dr1" and body["unit"] == "MJy/sr"
    assert body["pixel"] == {"x": 100, "y": 200} and len(body["points"]) == NCHAN
    assert body["points"][1]["sb"] == pytest.approx(float(img[1, 200, 100]), rel=1e-6)
    assert body["points"][2]["ok"] is False and body["points"][2]["flags"] == 12
    assert body["flag_names"]["2"] == "OVERFLOW" and body["caveats"]
    n = len(bucket.requests)
    assert c.get("/api/v1/cube/spectrum", params={"ra": ra, "dec": dec}).json() == body
    assert len(bucket.requests) == n  # the second answer came from the cache
    assert c.get("/api/v1/cube/spectrum", params={"ra": ra, "dec": dec, "flags": "x"}).status_code == 422
    assert c.get("/api/v1/cube/spectrum", params={"ra": 400, "dec": 0}).status_code == 422
    asyncio.run(extras.close())


def test_a_tile_that_is_not_in_the_bucket_is_a_502() -> None:
    app = FastAPI()
    register(app, Extras(FakeBucket({}).s3(), ROOT))
    r = TestClient(app).get("/api/v1/cube/spectrum", params={"ra": 10, "dec": 10})
    assert r.status_code == 502


@pytest.mark.network
def test_live_a_spectrum_of_barnards_star_field_from_the_real_cube() -> None:
    from service.reader.s3 import S3
    from service.routes_extras import CUBE_ROOT

    async def go() -> None:
        s3 = S3(max_in_flight=32)
        sp = await CubeSource(s3, CUBE_ROOT).spectrum(269.4479, 4.7687)
        await s3.close()
        assert sp is not None and sp.tile == 7945 and len(sp.lam) == 102
        assert sp.lam[0] == pytest.approx(0.753, abs=0.01) and sp.lam[-1] == pytest.approx(4.988, abs=0.01)
        assert sum(v is not None for v in sp.sb) > 20

    asyncio.run(go())
