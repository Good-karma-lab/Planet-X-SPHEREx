"""The place of a moving object at an exposure's time, and the parameters that key a spectrum."""

from __future__ import annotations

import pytest

from service.spectrum import place_at, spectrum_params


def test_fixed_place_does_not_move() -> None:
    p = spectrum_params(269.3559, 4.7822)
    assert p == {"ra": 269.3559, "dec": 4.7822}
    assert place_at(p, 61000.0) == (269.3559, 4.7822)


def test_proper_motion_moves_the_place() -> None:
    # Barnard's Star: −801.551, +10362.394 mas/yr from J2000; 25 years later about 4.3′ north
    p = spectrum_params(269.452077, 4.693365, -801.551, 10362.394)
    ra, dec = place_at(p, 51544.5 + 25 * 365.25)
    assert dec - 4.693365 == pytest.approx(10.362394 * 25 / 3600, rel=1e-6)
    assert (ra - 269.452077) * 3600 * 1000 / 25 == pytest.approx(-801.551 / 0.99666, rel=2e-3)
