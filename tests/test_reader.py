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


def test_the_layouts_kept_have_a_ceiling_and_one_that_left_is_read_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every exposure ever read stayed in memory (270 KB each, 73,000 of them on prod). The least lately used leaves; its answers stay."""
    from service.reader import files

    monkeypatch.setattr(files, "LAYOUTS_KEEP", 3)
    made = {f"f{i}.fits": make_file("qr2", n=128)[0] for i in range(6)}
    bucket = FakeBucket(made)

    async def run() -> None:
        fr = FileReader(bucket.s3(), tmp_path)
        first = {k: (await fr.layout(k)).image.ny for k in made}
        assert list(fr._layouts) == ["f3.fits", "f4.fits", "f5.fits"] and not fr._locks
        await fr.layout("f3.fits")  # used: the latest again
        n = len(bucket.requests)
        assert (await fr.layout("f0.fits")).image.ny == first["f0.fits"] and len(bucket.requests) == n  # from the store, no request
        assert list(fr._layouts) == ["f5.fits", "f3.fits", "f0.fits"]
        # many at once, far more than are kept: every answer is right and the ceiling holds
        got = await asyncio.gather(*(fr.rows(k, "image", 0, 8) for _ in range(12) for k in made))
        want = [await fr.rows(k, "image", 0, 8) for k in made]
        for j, g in enumerate(got):
            np.testing.assert_array_equal(g, want[j % 6])
        assert len(fr._layouts) <= 3 and not fr._locks
        with pytest.raises(Exception):  # noqa: B017 (a file that is not there leaves no lock behind)
            await fr.layout("none.fits")
        assert not fr._locks

    asyncio.run(run())


def test_threads_that_keep_and_take_layouts_never_meet_one_that_left(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    from service.reader import files

    monkeypatch.setattr(files, "LAYOUTS_KEEP", 5)
    fr = FileReader(None, tmp_path)  # type: ignore[arg-type]
    wrong: list[str] = []

    def work(seed: int) -> None:
        try:
            for i in range(4000):
                k = str((i * 7 + seed * 13) % 23)
                lay: object = fr._kept(k)
                if lay is not None and lay != f"layout {k}":
                    wrong.append(k)
                fr._keep(k, f"layout {k}")  # type: ignore[arg-type]
        except Exception as e:
            wrong.append(repr(e))

    ts = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not wrong and len(fr._layouts) == 5
