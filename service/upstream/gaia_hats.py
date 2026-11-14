"""Gaia DR3 from the HATS Parquet catalogue in a public AWS bucket (STScI's `stpubdata`): the same stars as the Gaia archive's TAP, read
directly, so a check that needs Gaia still works when the archive does not answer.

A Gaia source_id carries its HEALPix pixel at order 12 in its high bits (source_id // 2^35), and the catalogue's partitions are sorted
by source_id: a cone needs only the row groups whose source_id range overlaps the order-12 pixels of the cone, a few columns of
one or two row groups (about 200 000 rows each).
"""

from __future__ import annotations

import csv
import io
import math
import threading
from functools import lru_cache
from typing import Any

import httpx
import pyarrow.compute as pc
import pyarrow.fs as pafs
import pyarrow.parquet as pq

from service.coverage import ang2pix_nested

BUCKET = "stpubdata"
ROOT = "gaia/gaia_dr3/public/hats/gaia"
COLUMNS = ["source_id", "ra", "dec", "pmra", "pmdec", "parallax", "parallax_error", "ruwe", "phot_g_mean_mag", "bp_rp"]
_lock = threading.Lock()


@lru_cache(maxsize=1)
def _partitions() -> set[tuple[int, int]]:
    r = httpx.get(f"https://{BUCKET}.s3.amazonaws.com/{ROOT}/partition_info.csv", timeout=30)
    r.raise_for_status()
    return {(int(x["Norder"]), int(x["Npix"])) for x in csv.DictReader(io.StringIO(r.text))}


@lru_cache(maxsize=1)
def _fs() -> pafs.S3FileSystem:
    return pafs.S3FileSystem(anonymous=True, region="us-east-1")


def _pixels12(ra: float, dec: float, radius_deg: float) -> set[int]:
    """The order-12 pixels (about 52″) that a small cone touches: its centre and 16 points on its edge."""
    out = {ang2pix_nested(12, ra, dec)}
    for k in range(16):
        a = 2 * math.pi * k / 16
        d = dec + radius_deg * math.sin(a)
        r = ra + radius_deg * math.cos(a) / max(0.05, math.cos(math.radians(dec)))
        out.add(ang2pix_nested(12, r % 360.0, max(-90.0, min(90.0, d))))
    return out


def cone(ra: float, dec: float, radius_deg: float) -> list[dict[str, Any]]:
    """Gaia DR3 sources within `radius_deg` of (ra, dec) at Gaia's epoch (J2016.0); the columns of COLUMNS."""
    parts = _partitions()
    by_file: dict[tuple[int, int], list[int]] = {}
    # the pixels of the cone widened by 3′: a source_id carries the pixel of the star's place about J2000, so a fast-moving star
    # (Barnard's Star: 2.8′ between 2000 and 2016) sits in a pixel near, not under, the cone
    wide = set()
    for extra in (0.0, 0.01, 0.02, 0.03, 0.04, 0.05):
        wide |= _pixels12(ra, dec, radius_deg + extra)
    for p12 in wide:
        for order in range(6, 1, -1):
            npix = p12 >> (2 * (12 - order))
            if (order, npix) in parts:
                by_file.setdefault((order, npix), []).append(p12)
                break
    out: list[dict[str, Any]] = []
    cosd = math.cos(math.radians(dec))
    for (order, npix), pix in by_file.items():
        path = f"{BUCKET}/{ROOT}/dataset/Norder={order}/Dir={npix // 10000 * 10000}/Npix={npix}.parquet"
        lo, hi = min(pix) << 35, (max(pix) + 1) << 35
        with _lock:  # one read at a time: the fallback must not crowd the service's own reads
            table = pq.read_table(path, columns=COLUMNS, filesystem=_fs(), filters=[("source_id", ">=", lo), ("source_id", "<", hi)])
        if table.num_rows == 0:
            continue
        d_ra = pc.multiply(pc.subtract(table["ra"], ra), cosd)
        d_dec = pc.subtract(table["dec"], dec)
        near = pc.less(pc.add(pc.multiply(d_ra, d_ra), pc.multiply(d_dec, d_dec)), radius_deg * radius_deg)
        out.extend(table.filter(near).to_pylist())
    return out
