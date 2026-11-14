"""Live checks against the public bucket (run with SKY_NET=1)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest

from engine.spx import flags, psf, wave, wcs
from service.reader.files import FileReader
from service.reader.s3 import S3

FILES = {
    "qr2": "qr2/level2/2025W49_2A/l2b-v20-2025-342/1/level2_2025W49_2A_0001_1D1_spx_l2b-v20-2025-342.fits",
    "v27": "qr3/level2/2026W30_2A/l2b-v27-2026-224/1/level2_2026W30_2A_0001_1D1_spx_l2b-v27-2026-224.fits",
}


@pytest.mark.network
@pytest.mark.parametrize("layout", ["qr2", "v27"])
def test_real_file_reads(tmp_path: Path, layout: str) -> None:
    async def run() -> None:
        s3 = S3(max_in_flight=8)
        fr = FileReader(s3, tmp_path)
        key = FILES[layout]
        lay = await fr.layout(key)
        assert lay.release_layout == layout and lay.image.nx == lay.image.ny == 2040
        img = await fr.rows(key, "image", 1000, 1010)
        fl = await fr.rows(key, "flags", 1000, 1010)
        assert img.shape == fl.shape == (10, 2040) and img.dtype == np.float32 and fl.dtype == np.int32
        table, src, mismatch = flags.resolve(lay.flags_header)
        assert src == "file" and mismatch == {} and table["OUTLIER"] == 19
        h, data = await fr.table_bytes(key, "psf")
        ps = psf.from_cube(h, data) if lay.psf_kind == "cube" else psf.from_epsf(h, data)
        assert psf.stamp(ps, ps.zone_index(1020, 1020), 0, 0, half=12).sum() == pytest.approx(1.0, abs=0.01)
        h, data = await fr.table_bytes(key, "wave")
        lam, _ = wave.WaveMap(wave.table_from_hdu_bytes(h, data), lay.detector)([1019.5], [1019.5])
        assert 0.75 < lam[0] < 1.11  # D1
        ra, dec = wcs.pix2sky(wcs.celestial(lay.image_header), [0.0], [0.0])
        assert np.isfinite(ra[0]) and np.isfinite(dec[0])
        await s3.close()

    asyncio.run(run())
