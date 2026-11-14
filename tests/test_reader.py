from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest

from service.reader.files import BAND_ROWS, FileReader
from service.reader.s3 import ReadError
from service.upstream.breaker import Breaker, BreakerOpenError
from tests.fake_s3 import FakeBucket
from tests.synth_l2 import make_file


@pytest.mark.parametrize("layout", ["qr2", "v27"])
def test_rows_through_the_band_cache(tmp_path: Path, layout: str) -> None:
    data, planes = make_file(layout, n=256)
    bucket = FakeBucket({"f.fits": data})

    async def run() -> None:
        s3 = bucket.s3()
        fr = FileReader(s3, tmp_path)
        y0, y1 = BAND_ROWS - 5, BAND_ROWS + 70  # crosses two band boundaries
        for plane in ("image", "flags", "variance", "zodi"):
            got = await fr.rows("f.fits", plane, y0, y1)  # type: ignore[arg-type]
            np.testing.assert_array_equal(got, planes[plane][y0:y1])
        n_requests = len(bucket.requests)
        fr2 = FileReader(s3, tmp_path)  # a new reader: layout and bands come from the store, no request
        np.testing.assert_array_equal(await fr2.rows("f.fits", "image", y0, y1), planes["image"][y0:y1])
        assert len(bucket.requests) == n_requests
        await s3.close()

    asyncio.run(run())


def test_one_failure_is_retried(tmp_path: Path) -> None:
    data, planes = make_file("v27", n=128)
    bucket = FakeBucket({"f.fits": data}, fail_next=[503])

    async def run() -> None:
        s3 = bucket.s3()
        got = await FileReader(s3, tmp_path).rows("f.fits", "image", 0, 10)
        np.testing.assert_array_equal(got, planes["image"][:10])
        assert s3.stats.retries == 1
        await s3.close()

    asyncio.run(run())


def test_missing_object_is_a_read_error(tmp_path: Path) -> None:
    bucket = FakeBucket({})

    async def run() -> None:
        s3 = bucket.s3()
        with pytest.raises(ReadError, match="404"):
            await FileReader(s3, tmp_path).layout("nothing.fits")
        await s3.close()

    asyncio.run(run())


def test_breaker_opens_and_probes() -> None:
    now = [0.0]
    b = Breaker("x", threshold=5, consecutive=3, open_s=30, clock=lambda: now[0])
    for _ in range(3):
        b.before()
        b.failure()
    assert b.state == "open"
    with pytest.raises(BreakerOpenError):
        b.before()
    now[0] = 31.0
    assert b.state == "half-open"
    b.before()  # the probe
    with pytest.raises(BreakerOpenError):
        b.before()  # only one probe at a time
    b.failure()  # failed probe: open twice as long
    now[0] = 31.0 + 59.0
    assert b.state == "open"
    now[0] = 31.0 + 61.0
    b.before()
    b.success()
    assert b.state == "closed"


def test_psf_is_read_once_per_detector_and_layout(tmp_path: Path) -> None:
    from service.reader.psfs import PsfProvider

    a, _ = make_file("v27", n=128, seed=1)
    b, _ = make_file("v27", n=128, seed=2, mjd=61200.0)
    bucket = FakeBucket({"a.fits": a, "b.fits": b})

    async def run() -> None:
        s3 = bucket.s3()
        fr = FileReader(s3, tmp_path)
        prov = PsfProvider(fr, tmp_path)
        pa = await prov.for_file("a.fits")
        pb = await prov.for_file("b.fits")
        assert pa is pb and prov.reads == 1
        prov2 = PsfProvider(FileReader(s3, tmp_path), tmp_path)  # from the store, no read
        pc = await prov2.for_file("a.fits")
        assert prov2.reads == 0 and pc.checksum == pa.checksum
        np.testing.assert_allclose(pc.zones, pa.zones, rtol=1e-6)
        await s3.close()

    asyncio.run(run())
