from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

from indexer.irsa import Row, parse_rows, parse_wkb_polygon
from indexer.store import Index, boxes_for


def ewkb(ra: list[float], dec: list[float]) -> str:
    pts = list(zip([*ra, ra[0]], [*dec, dec[0]], strict=True))
    b = struct.pack("<BII", 1, 0x20000003, 4326) + struct.pack("<II", 1, len(pts))
    for x, y in pts:
        b += struct.pack("<dd", x, y)
    return b.hex().upper()


def square(ra: float, dec: float, half: float = 1.75) -> tuple[list[float], list[float]]:
    """A detector-sized square footprint (3.5° on a side) centred on (ra, dec), built in the tangent plane."""
    a0, d0 = np.radians(ra), np.radians(dec)
    out_ra, out_dec = [], []
    for xi, eta in ((-half, -half), (half, -half), (half, half), (-half, half)):
        x, y = np.radians(xi), np.radians(eta)
        den = np.cos(d0) - y * np.sin(d0)
        a = a0 + np.arctan2(x, den)
        d = np.arctan2(np.sin(d0) + y * np.cos(d0), np.hypot(x, den))
        out_ra.append(float(np.degrees(a) % 360))
        out_dec.append(float(np.degrees(d)))
    return out_ra, out_dec


def row(key_n: int, ra: float, dec: float, mjd: float = 61000.0, det: int = 1, release: str = "dr1") -> Row:
    r, d = square(ra, dec)
    return Row(
        key=f"{release}/level2/2025W40_1A/l2b-v27-2026-235/{det}/level2_2025W40_1A_{key_n:04d}_1D{det}_spx_l2b-v27-2026-235.fits",
        release=release,
        period="2025W40_1A",
        pipeline="l2b-v27-2026-235",
        seq=key_n,
        group=1,
        det=det,
        mjd0=mjd,
        mjd1=mjd + 0.0013,
        size=65_000_000,
        md5="0" * 32,
        ra=tuple(x % 360 for x in r),
        dec=tuple(d),
    )


def test_wkb_polygon_round_trip_drops_the_closing_vertex() -> None:
    ra, dec = parse_wkb_polygon(ewkb([10.0, 12.0, 12.0, 10.0], [1.0, 1.0, 3.0, 3.0]))
    assert ra == (10.0, 12.0, 12.0, 10.0) and dec == (1.0, 1.0, 3.0, 3.0)


def test_csv_rows_parse_and_map_to_bucket_keys() -> None:
    poly = ewkb([82.9, 79.5, 79.3, 82.8], [-6.97, -7.2, -3.7, -3.5])
    text = (
        "uri,contentlength,contentchecksum,energy_bandpassname,time_bounds_lower,time_bounds_upper,poly\n"
        "ibe/data/spherex/dr1/level2/2025W36_2B/l2b-v27-2026-234/2/level2_2025W36_2B_0728_1D2_spx_l2b-v27-2026-234.fits,"
        f"64422720,md5:f2014eef,SPHEREx-D2,60926.000294,60926.001627,{poly}\n"
        "ibe/data/spherex/dr1/calib/something_else.fits,1,,,0,0," + poly + "\n"
    )
    rows = parse_rows(text)
    assert len(rows) == 1
    r = rows[0]
    assert r.key.startswith("dr1/level2/2025W36_2B/") and r.det == 2 and r.seq == 728 and r.group == 1 and r.md5 == "f2014eef"


def test_overlap_query_finds_fields_inside_and_near_the_edge(tmp_path: Path) -> None:
    ix = Index(tmp_path / "i.sqlite")
    ix.add([row(1, 100.0, 20.0), row(2, 140.0, 20.0)])
    ix.commit()
    assert [e.seq for e in ix.overlapping(100.0, 20.0, 0.1)] == [1]
    assert [e.seq for e in ix.overlapping(100.0, 20.0 + 1.75 + 0.05, 0.1)] == [1]  # field edge reaches into the footprint
    assert ix.overlapping(100.0, 20.0 + 1.75 + 0.5, 0.1) == []
    assert ix.overlapping(120.0, 20.0, 0.5) == []


def test_ra_zero_and_poles(tmp_path: Path) -> None:
    ix = Index(tmp_path / "i.sqlite")
    ix.add([row(1, 0.3, 10.0), row(2, 270.0, 88.9), row(3, 90.0, -89.0)])
    ix.commit()
    assert [e.seq for e in ix.overlapping(359.8, 10.0, 0.05)] == [1]
    assert [e.seq for e in ix.overlapping(1.0, 10.0, 0.05)] == [1]
    assert [e.seq for e in ix.overlapping(90.0, 89.5, 0.2)] == [2]  # across the pole from its centre
    assert [e.seq for e in ix.overlapping(0.0, -89.9, 0.05)] == [3]


def test_results_are_in_time_order_and_keys_unique(tmp_path: Path) -> None:
    ix = Index(tmp_path / "i.sqlite")
    ix.add([row(2, 50.0, 0.0, mjd=61010.0), row(1, 50.0, 0.0, mjd=61000.0)])
    assert ix.add([row(1, 50.0, 0.0, mjd=61000.0)]) == 0  # the same key twice is one file
    ix.commit()
    assert [e.mjd0 for e in ix.overlapping(50.0, 0.0, 0.1)] == [61000.0, 61010.0]
    assert ix.count() == 2


def test_days_are_remembered(tmp_path: Path) -> None:
    ix = Index(tmp_path / "i.sqlite")
    ix.mark_day(60926, 3142, "2026-11-14T10:00:00+00:00")
    ix.commit()
    ix.close()
    assert Index(tmp_path / "i.sqlite").days_done() == {60926}


@pytest.mark.parametrize(("ra", "dec", "r"), [(10.0, 0.0, 1.0), (359.5, 0.0, 1.0), (0.5, 50.0, 1.0), (180.0, 89.5, 1.0)])
def test_boxes_cover_the_circle(ra: float, dec: float, r: float) -> None:
    boxes = boxes_for(ra, dec, r)
    rng = np.random.default_rng(1)
    for _ in range(200):  # random points inside the circle must fall into a box
        t = rng.uniform(0, 2 * np.pi)
        s = r * np.sqrt(rng.uniform(0, 1))
        d = dec + s * np.cos(t)
        if abs(d) > 90:
            continue
        a = (ra + s * np.sin(t) / max(np.cos(np.radians(d)), 1e-6)) % 360
        assert any(a0 <= a <= a1 and d0 <= d <= d1 for a0, a1, d0, d1 in boxes) or abs(d) > 89
