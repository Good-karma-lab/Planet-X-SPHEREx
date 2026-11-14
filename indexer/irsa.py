"""The exposure list from IRSA's TAP service: one query per day of the survey (docs/plan/03-resources.md, 3.3–3.4).

`spherex.artifact` joined with `spherex.plane` gives, for every Level-2 file of DR1 and QR3 (deep files included), its path, size, MD5,
detector, time span and 4-corner sky footprint. The path maps one to one to the S3 key of the public bucket.
"""

from __future__ import annotations

import csv
import io
import re
import struct
from dataclasses import dataclass

import httpx

TAP_URL = "https://irsa.ipac.caltech.edu/TAP/sync"
URI_PREFIX = "ibe/data/spherex/"
QUERY = (
    "SELECT a.uri, a.contentlength, a.contentchecksum, p.energy_bandpassname, p.time_bounds_lower, p.time_bounds_upper, p.poly "
    "FROM spherex.artifact a JOIN spherex.plane p ON a.planeid = p.planeid "
    "WHERE p.time_bounds_lower >= {t0} AND p.time_bounds_lower < {t1}"
)
NAME = re.compile(
    r"^(?P<release>[a-z0-9]+)/level2/(?P<period>[^/]+)/(?P<pipeline>[^/]+)/(?P<folder>\d+)/"
    r"level2_(?P=period)_(?P<seq>\d+)_(?P<group>\d+)D(?P<det>\d)_spx_[^/]+\.fits$"
)


@dataclass(frozen=True)
class Row:
    key: str
    release: str
    period: str
    pipeline: str
    seq: int
    group: int
    det: int
    mjd0: float
    mjd1: float
    size: int
    md5: str
    ra: tuple[float, ...]  # polygon vertices (open ring)
    dec: tuple[float, ...]


def parse_wkb_polygon(hexstr: str) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """(RA, Dec) of the outer ring of a (E)WKB polygon in hex; the closing vertex is dropped."""
    b = bytes.fromhex(hexstr)
    end = "<" if b[0] == 1 else ">"
    gtype = struct.unpack(end + "I", b[1:5])[0]
    off = 5
    if gtype & 0x20000000:  # EWKB with an SRID
        off += 4
    if gtype & 0xFF != 3:
        raise ValueError(f"not a polygon (type {gtype & 0xFF})")
    (nrings,) = struct.unpack(end + "I", b[off : off + 4])
    off += 4
    if nrings < 1:
        raise ValueError("polygon without a ring")
    (npts,) = struct.unpack(end + "I", b[off : off + 4])
    off += 4
    pts = struct.unpack(end + "d" * (2 * npts), b[off : off + 16 * npts])
    ra, dec = list(pts[0::2]), list(pts[1::2])
    if npts > 1 and ra[0] == ra[-1] and dec[0] == dec[-1]:
        ra, dec = ra[:-1], dec[:-1]
    return tuple(x % 360.0 for x in ra), tuple(dec)


def parse_rows(text: str) -> list[Row]:
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        uri = r["uri"]
        if not uri.startswith(URI_PREFIX):
            continue
        key = uri[len(URI_PREFIX) :]
        m = NAME.match(key)
        if not m:
            continue
        ra, dec = parse_wkb_polygon(r["poly"])
        md5 = r.get("contentchecksum") or ""
        out.append(
            Row(
                key=key,
                release=m["release"],
                period=m["period"],
                pipeline=m["pipeline"],
                seq=int(m["seq"]),
                group=int(m["group"]),
                det=int(m["det"]),
                mjd0=float(r["time_bounds_lower"]),
                mjd1=float(r["time_bounds_upper"]),
                size=int(r["contentlength"] or 0),
                md5=md5.removeprefix("md5:"),
                ra=ra,
                dec=dec,
            )
        )
    return out


def fetch_day(client: httpx.Client, day_mjd: int) -> list[Row]:
    """Every file whose exposure starts in [day, day + 1) (MJD)."""
    q = QUERY.format(t0=float(day_mjd), t1=float(day_mjd + 1))
    r = client.post(TAP_URL, data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": q}, timeout=300)
    r.raise_for_status()
    if not r.text.startswith("uri,"):
        raise RuntimeError(f"IRSA TAP answered without the expected table: {r.text[:200]!r}")
    return parse_rows(r.text)
