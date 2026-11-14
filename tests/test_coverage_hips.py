"""Coverage map cells and the survey tile paths of the globe."""

from __future__ import annotations

import pytest

from service.coverage import ang2pix_nested
from service.upstream.hips import HipsTiles


def test_nested_pixels_of_known_places() -> None:
    # order 0: twelve base faces; the north pole lies in faces 0-3, the south pole in 8-11, the equator at RA 0 in face 4
    assert ang2pix_nested(0, 45.0, 89.0) == 0
    assert ang2pix_nested(0, 0.0, 0.0) == 4
    assert 8 <= ang2pix_nested(0, 10.0, -89.0) <= 11
    # a child pixel lies inside its parent: nested numbering divides by four per order
    for ra, dec in ((269.45, 4.69), (331.0, -56.8), (133.8, -7.2), (12.0, 70.0)):
        p3 = ang2pix_nested(3, ra, dec)
        assert ang2pix_nested(2, ra, dec) == p3 // 4
        assert 0 <= p3 < 12 * 4**3


def test_hips_tile_paths() -> None:
    assert HipsTiles.relative("dss2", 3, "allsky") == "Norder3/Allsky.jpg"
    assert HipsTiles.relative("wise", 5, "12000") == "Norder5/Dir10000/Npix12000.jpg"
    assert HipsTiles.relative("spx88", 3, "allsky") == "Norder3/Allsky_88.png"
    assert HipsTiles.relative("spx8", 5, "100") == "Norder5/Dir0/Npix100_8.png"
    for bad in (("nope", 3, "allsky"), ("dss2", 4, "allsky"), ("wise", 9, "1"), ("dss2", 3, "../x"), ("dust", 3, "999")):
        with pytest.raises(ValueError):
            HipsTiles.relative(*bad)
