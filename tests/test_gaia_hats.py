"""The order-12 pixels a cone touches (the source_id ranges read from the Parquet copy of Gaia DR3)."""

from __future__ import annotations

from service.coverage import ang2pix_nested
from service.upstream.gaia_hats import _pixels12


def test_a_small_cone_touches_its_own_pixel_and_few_others() -> None:
    p = _pixels12(269.4485, 4.7395, 10 / 3600)
    assert ang2pix_nested(12, 269.4485, 4.7395) in p and len(p) <= 4


def test_barnards_star_source_id_pixel_is_reached_from_its_2016_place() -> None:
    # Gaia DR3 4472832130942575872 carries the pixel of its J2000 place, 2.8′ from where it was in 2016
    wide: set[int] = set()
    for extra in (0.0, 0.01, 0.02, 0.03, 0.04, 0.05):
        wide |= _pixels12(269.4485, 4.7395, 10 / 3600 + extra)
    assert 4472832130942575872 >> 35 in wide
