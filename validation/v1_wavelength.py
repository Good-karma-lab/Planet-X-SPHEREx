"""V1: the wavelength of a pixel against the mission's per-pixel calibration.

IRSA documents the WCS-WAVE table of a Level-2 file as an approximation for visualisation and the `spectral_wcs` calibration files
(CWAVE, CBAND: one value per detector pixel) as the reference. This compares, for each detector, our bicubic spline through the WCS-WAVE
nodes of a DR1 science file with the CWAVE plane of `dr1/spectral_wcs/` on a grid of pixels.

Run:  uv run python -m validation.v1_wavelength   -> docs/validation/<date>-v1-wavelength.md
"""

from __future__ import annotations

import asyncio
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
import numpy as np

from engine.spx import wave
from engine.spx.fitsio import hdu_at
from service.reader.files import FileReader
from service.reader.s3 import BUCKET_URL, S3

ROOT = Path(__file__).resolve().parent.parent
CAL = "dr1/spectral_wcs/cal-swcs-v5-2026-191/{d}/spectral_wcs_D{d}_spx_cal-swcs-v5-2026-191.fits"
SCIENCE_PERIOD = "dr1/level2/2025W45_1A/"


def plane(client: httpx.Client, key: str, name: str) -> np.ndarray:
    off, i = 0, 0
    while True:
        h = hdu_at(i, off, client.get(BUCKET_URL + key, headers={"Range": f"bytes={off}-{off + 40000}"}).content)
        if h.name == name:
            data = client.get(BUCKET_URL + key, headers={"Range": f"bytes={h.data_offset}-{h.data_offset + h.data_size - 1}"}).content
            n1, n2 = int(h.header["NAXIS1"]), int(h.header["NAXIS2"])
            return np.frombuffer(data, ">f4").reshape(n2, n1).astype(np.float64)
        off, i = h.next_offset, i + 1


async def run() -> str:
    client = httpx.Client(timeout=120)
    s3 = S3(max_in_flight=8)
    fr = FileReader(s3, Path(tempfile.mkdtemp(prefix="sky-v1-")))
    pipes = re.findall(
        r"<Prefix>([^<]*)</Prefix>", client.get(BUCKET_URL, params={"list-type": "2", "delimiter": "/", "prefix": SCIENCE_PERIOD}).text
    )[1:]
    lines = [
        f"# V1 — wavelength of a pixel against the per-pixel calibration ({datetime.now(UTC).date().isoformat()})",
        "",
        "Reference: CWAVE of `dr1/spectral_wcs/cal-swcs-v5-2026-191` (one wavelength per detector pixel). Compared: the bicubic spline",
        "through the WCS-WAVE nodes of a DR1 science file of the same detector (what the fields use), on every 16th pixel.",
        "",
        "| detector | science file calibration | median |Δλ| (µm) | 99th percentile (µm) | max (µm) | max / channel width |",
        "|---|---|---|---|---|---|",
    ]
    for d in range(1, 7):
        cw = plane(client, CAL.format(d=d), "CWAVE")
        key = None
        for p in pipes:
            ks = re.findall(
                r"<Key>([^<]*\.fits)</Key>", client.get(BUCKET_URL, params={"list-type": "2", "prefix": f"{p}{d}/", "max-keys": "2"}).text
            )
            if ks:
                key = ks[0]
                break
        assert key is not None
        lay = await fr.layout(key)
        h, data = await fr.table_bytes(key, "wave")
        t = wave.table_from_hdu_bytes(h, data)
        wm = wave.WaveMap(t, lay.detector)
        yy, xx = np.mgrid[0:2040:16, 0:2040:16]
        lam, bw = wm(xx.astype(float), yy.astype(float))
        ref = cw[yy, xx]
        ok = np.isfinite(ref) & (ref > 0)
        diff = np.abs(lam - ref)[ok]
        width = float(np.median(bw[ok]))
        p99 = np.percentile(diff, 99)
        lines.append(f"| D{d} | {t.calibration} | {np.median(diff):.5f} | {p99:.5f} | {diff.max():.5f} | {diff.max() / width:.2f} |")
    await s3.close()
    client.close()
    return "\n".join(lines) + "\n"


def main() -> None:
    rep = asyncio.run(run())
    out = ROOT / "docs" / "validation" / f"{datetime.now(UTC).date().isoformat()}-v1-wavelength.md"
    out.write_text(rep, encoding="utf-8")
    print(rep)


if __name__ == "__main__":
    main()
