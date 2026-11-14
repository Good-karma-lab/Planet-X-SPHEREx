"""M1 validation on live files (docs/plan/02-methods/2.7-validation.md, V1 and V2).

V2: the PSF data of two files of the same detector and release, taken months apart, are byte-identical (so one PSF per detector and
release can be shared after a checksum check). Also: whether QR3 and DR1 share their EPSF.
V1 (partial): the wavelength map. The comparison with IRSA's own tool needs that tool and is not run here; what is run: the spline
passes through the table's nodes, and the difference between the spline and a bilinear lookup between nodes is measured.

Run:  uv run python -m validation.m1_level2 [--store DIR]   -> docs/validation/<date>-m1-level2.md
"""

from __future__ import annotations

import argparse
import asyncio
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
import numpy as np
from scipy.interpolate import RegularGridInterpolator

from engine.spx import psf as spxpsf
from engine.spx import wave as spxwave
from service.reader.files import FileReader
from service.reader.s3 import BUCKET_URL, S3

ROOT = Path(__file__).resolve().parent.parent


def list_prefixes(client: httpx.Client, prefix: str) -> list[str]:
    out, token = [], None
    while True:
        params = {"list-type": "2", "delimiter": "/", "prefix": prefix}
        if token:
            params["continuation-token"] = token
        s = client.get(BUCKET_URL, params=params).text
        out += re.findall(r"<Prefix>([^<]*)</Prefix>", s)[1:] if not token else re.findall(r"<Prefix>([^<]*)</Prefix>", s)
        m = re.search(r"<NextContinuationToken>([^<]*)</NextContinuationToken>", s)
        if not m:
            return [p for p in out if p != prefix]
        token = m.group(1)


def first_key(client: httpx.Client, prefix: str) -> str | None:
    s = client.get(BUCKET_URL, params={"list-type": "2", "prefix": prefix, "max-keys": "5"}).text
    keys = [k for k in re.findall(r"<Key>([^<]*)</Key>", s) if k.endswith(".fits")]
    return keys[0] if keys else None


def file_for(client: httpx.Client, period_prefix: str, det: int) -> str | None:
    for pipe in list_prefixes(client, period_prefix):
        k = first_key(client, f"{pipe}{det}/")
        if k:
            return k
    return None


async def psf_checksum(fr: FileReader, key: str) -> tuple[str, str, int]:
    lay = await fr.layout(key)
    _h, data = await fr.table_bytes(key, "psf")
    return spxpsf.checksum(data), str(lay.psf_kind), len(data)


async def wave_check(fr: FileReader, key: str) -> tuple[float, float, float]:
    """(max |spline − node| at the nodes, median and max |spline − bilinear| on a 64-px grid), µm."""
    lay = await fr.layout(key)
    h, data = await fr.table_bytes(key, "wave")
    t = spxwave.table_from_hdu_bytes(h, data)
    wm = spxwave.WaveMap(t, lay.detector)
    xs, ys = np.meshgrid(t.x_nodes, t.y_nodes)
    at_nodes = float(np.max(np.abs(wm(xs, ys)[0] - wm.offset - t.wavelength)))
    lin = RegularGridInterpolator((t.y_nodes, t.x_nodes), t.wavelength)
    g = np.arange(0, 2040, 64.0)
    gx, gy = np.meshgrid(np.clip(g, t.x_nodes[0], t.x_nodes[-1]), np.clip(g, t.y_nodes[0], t.y_nodes[-1]))
    d = np.abs(wm(gx, gy)[0] - wm.offset - lin(np.stack([gy.ravel(), gx.ravel()], -1)).reshape(gx.shape))
    return at_nodes, float(np.median(d)), float(np.max(d))


async def run(store: Path) -> str:
    client = httpx.Client(timeout=30)
    s3 = S3(max_in_flight=16)
    fr = FileReader(s3, store)
    lines = [
        f"# M1 validation — Level-2 access ({datetime.now(UTC).date().isoformat()})",
        "",
        "Live files of the public bucket `nasa-irsa-spherex`. Written by `validation/m1_level2.py`.",
        "",
        "## V2 — PSF identity per detector and release",
        "",
        "| release | detector | first file | last file | PSF kind | bytes | identical |",
        "|---|---|---|---|---|---|---|",
    ]
    sums: dict[tuple[str, int], str] = {}
    all_ok = True
    for rel in ("qr2", "qr3", "dr1"):
        periods = list_prefixes(client, f"{rel}/level2/")
        for det in range(1, 7):
            pair = []
            for pp in (periods, list(reversed(periods))):
                for p in pp:
                    k = file_for(client, p, det)
                    if k:
                        pair.append(k)
                        break
            if len(pair) < 2:
                lines.append(f"| {rel} | D{det} | — | — | — | — | no files |")
                continue
            (c0, kind, n0), (c1, _, _) = await psf_checksum(fr, pair[0]), await psf_checksum(fr, pair[1])
            same = c0 == c1
            all_ok &= same
            sums[(rel, det)] = c0
            p0, p1 = pair[0].split("/")[2], pair[1].split("/")[2]
            lines.append(f"| {rel} | D{det} | {p0} | {p1} | {kind} | {n0:,} | {'yes' if same else '**no**'} |")
    lines += ["", f"All pairs identical: **{'yes' if all_ok else 'no'}**.", "", "QR3 and DR1 share the EPSF of a detector:"]
    lines.append(
        ", ".join(f"D{d} {'yes' if sums.get(('qr3', d)) == sums.get(('dr1', d)) else 'no'}" for d in range(1, 7) if ("qr3", d) in sums)
    )
    lines += [
        "",
        "## V1 (partial) — wavelength map",
        "",
        "The comparison with IRSA's Spectrophotometry Tool is not run here (it needs that tool). Measured: the spline through the",
        "WCS-WAVE nodes reproduces the nodes, and its difference from a bilinear lookup between nodes (what a -TAB lookup does).",
        "",
        "| file | detector | calibration offset (µm) | max at nodes (µm) | spline − bilinear median (µm) | max (µm) |",
        "|---|---|---|---|---|---|",
    ]
    for rel in ("qr2", "dr1"):
        periods = list_prefixes(client, f"{rel}/level2/")
        for det in range(1, 7):
            k = next((file_for(client, p, det) for p in periods[len(periods) // 2 :] if file_for(client, p, det)), None)
            if not k:
                continue
            lay = await fr.layout(k)
            h, data = await fr.table_bytes(k, "wave")
            off = spxwave.calibration_offset(spxwave.table_from_hdu_bytes(h, data), lay.detector)
            a, med, mx = await wave_check(fr, k)
            lines.append(f"| {k.split('/')[-1]} | D{det} | {off:.4f} | {a:.1e} | {med:.4f} | {mx:.4f} |")
    lines += ["", f"Reads: {s3.stats.requests} requests, {s3.stats.bytes / 1e6:.1f} MB, {s3.stats.retries} retries."]
    await s3.close()
    client.close()
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", type=Path, default=None)
    a = ap.parse_args()
    store = a.store or Path(tempfile.mkdtemp(prefix="sky-validation-"))
    report = asyncio.run(run(store))
    out = ROOT / "docs" / "validation" / f"{datetime.now(UTC).date().isoformat()}-m1-level2.md"
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"written: {out}")


if __name__ == "__main__":
    main()
