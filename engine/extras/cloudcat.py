"""Cone searches in catalogues stored as spatially partitioned Parquet in public buckets, read in place (no TAP service needed).

Two layouts are in use: IRSA's HEALPix datasets (`.../wise-allwise.parquet/healpix_k0=<k0>/healpix_k5=<pix>/part0.snappy.parquet`, one
file per order-5 pixel, nested ordering) and HATS (see service/upstream/gaia_hats.py). This module handles the first and gives the
geometry helpers both need. A cone is answered from the file of every order-5 pixel it touches; rows are tested against the cone in numpy.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.fs as pafs
import pyarrow.parquet as pq

from service.coverage import ang2pix_nested


def cone_pixels(order: int, ra: float, dec: float, radius_deg: float) -> list[int]:
    """The nested pixels of `order` that a small cone touches: the one under its centre and under 16 points of its edge."""
    out = {ang2pix_nested(order, ra % 360.0, dec)}
    cosd = max(0.05, math.cos(math.radians(dec)))
    for k in range(16):
        a = 2 * math.pi * k / 16
        d = max(-90.0, min(90.0, dec + radius_deg * math.sin(a)))
        out.add(ang2pix_nested(order, (ra + radius_deg * math.cos(a) / cosd) % 360.0, d))
    return sorted(out)


def separation_arcsec(ra: np.ndarray, dec: np.ndarray, ra0: float, dec0: float) -> np.ndarray:
    """Angular distance in arcseconds of arrays of positions from (ra0, dec0): the haversine form, exact at every scale."""
    r1, d1, r0, d0 = np.radians(ra), np.radians(dec), math.radians(ra0), math.radians(dec0)
    h = np.sin((d1 - d0) / 2) ** 2 + np.cos(d1) * math.cos(d0) * np.sin((r1 - r0) / 2) ** 2
    return np.asarray(np.degrees(2 * np.arcsin(np.sqrt(np.clip(h, 0.0, 1.0)))) * 3600.0)


@dataclass(frozen=True)
class HealpixDataset:
    """An IRSA HEALPix Parquet dataset: where it is and how its files are named."""

    root: str  # `bucket/prefix/name.parquet`
    order: int = 5
    parent_order: int = 0
    ra: str = "ra"
    dec: str = "dec"

    def path(self, pix: int) -> str:
        parent = pix >> (2 * (self.order - self.parent_order))
        return f"{self.root}/healpix_k{self.parent_order}={parent}/healpix_k{self.order}={pix}/part0.snappy.parquet"


Reader = Callable[[str, Sequence[str]], pa.Table]


def s3_reader(region: str) -> Reader:
    """A reader of the named columns of one anonymous S3 Parquet file."""
    fs = pafs.S3FileSystem(anonymous=True, region=region)

    def read(path: str, columns: Sequence[str]) -> pa.Table:
        return pq.read_table(path, columns=list(columns), filesystem=fs)

    return read


def cone(ds: HealpixDataset, reader: Reader, columns: Sequence[str], ra: float, dec: float, radius_arcsec: float) -> list[dict[str, Any]]:
    """The rows of a dataset within `radius_arcsec` of (ra, dec), nearest first, each with `sep_arcsec`."""
    cols = list(dict.fromkeys([ds.ra, ds.dec, *columns]))
    rows: list[dict[str, Any]] = []
    for pix in cone_pixels(ds.order, ra, dec, radius_arcsec / 3600.0):
        table = reader(ds.path(pix), cols)
        if table.num_rows == 0:
            continue
        sep = separation_arcsec(table[ds.ra].to_numpy(zero_copy_only=False), table[ds.dec].to_numpy(zero_copy_only=False), ra, dec)
        keep = np.flatnonzero(sep <= radius_arcsec)
        if keep.size == 0:
            continue
        sub = table.take(pa.array(keep)).to_pylist()
        for row, s in zip(sub, sep[keep], strict=True):
            row["sep_arcsec"] = round(float(s), 3)
            rows.append(row)
    rows.sort(key=lambda r: r["sep_arcsec"])
    return rows
