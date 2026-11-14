from __future__ import annotations

import io

import numpy as np
from astropy.io import fits

from engine.spx.footprint import contains, footprint, overlaps_circle
from tests.synth_l2 import make_file


def header(ra: float = 10.0, dec: float = -30.0, n: int = 2040) -> fits.Header:
    data, _ = make_file("v27", n=64, ra=ra, dec=dec)
    with fits.open(io.BytesIO(data)) as hl:
        h = hl["IMAGE"].header.copy()
    # widen the synthetic detector to the real size and centre
    h["NAXIS1"] = h["NAXIS2"] = n
    h["CRPIX1"] = h["CRPIX2"] = (n + 1) / 2
    return h


def test_footprint_of_a_full_detector() -> None:
    fp = footprint(header())
    assert len(fp.ra) == 12
    side = 2040 * 6.15 / 3600
    assert np.hypot(side / 2, side / 2) * 0.98 < fp.radius_deg < np.hypot(side / 2, side / 2) * 1.05
    assert abs(fp.centre[0] - 10.0) < 0.01 and abs(fp.centre[1] + 30.0) < 0.01


def test_contains_centre_not_far_point() -> None:
    fp = footprint(header())
    assert contains(fp, 10.0, -30.0)
    assert not contains(fp, 10.0, -35.0)


def test_overlap_of_a_field_beyond_the_edge() -> None:
    fp = footprint(header())
    half = 2040 * 6.15 / 3600 / 2
    just_outside_dec = -30.0 + half + 0.05
    assert not contains(fp, 10.0, just_outside_dec)
    assert overlaps_circle(fp, 10.0, just_outside_dec, radius_deg=0.1)  # a 12' field whose edge reaches in
    assert not overlaps_circle(fp, 10.0, just_outside_dec, radius_deg=0.02)


def test_footprint_across_ra_zero() -> None:
    fp = footprint(header(ra=0.2, dec=10.0))
    assert contains(fp, 359.9, 10.0) and contains(fp, 0.5, 10.0)
    assert np.all((fp.ra >= 0) & (fp.ra < 360))
