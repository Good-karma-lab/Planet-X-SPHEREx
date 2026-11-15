from __future__ import annotations

import numpy as np

from engine import ring as er


def star_image(flux: float, n: int = 201, core: float = 4.0) -> tuple[np.ndarray, np.ndarray]:
    """A star with wings (a Moffat-like profile) whose core inside `core` pixels is saturated (cut out), on a flat background with noise."""
    rng = np.random.default_rng(5)
    yy, xx = np.mgrid[0:n, 0:n]
    r = np.hypot(xx - n // 2, yy - n // 2)
    img = flux * (1 + (r / 3.0) ** 2) ** -2.5 + 0.3 + rng.normal(0, 0.002, (n, n))
    sat = (r <= core).astype(np.float32)
    return img.astype(np.float32), sat


def test_the_ring_outside_a_saturated_core_follows_the_stars_brightness() -> None:
    a, sat = star_image(10.0)
    b, _ = star_image(15.0)  # the same star 50 % brighter
    c = er.core_radius(sat, 100, 100, 20)
    assert 3.5 < c < 4.5
    ra, rb = er.ring_flux(a, 100, 100, c + 1, c + 8), er.ring_flux(b, 100, 100, c + 1, c + 8)
    assert ra is not None and rb is not None
    rr = er.ratio(rb, ra)
    assert rr is not None and abs(rr[0] - 1.5) < 0.03 and rr[1] < 0.05


def test_a_ring_that_leaves_the_data_or_has_no_background_is_not_measured() -> None:
    a, _ = star_image(10.0, n=60)
    assert er.ring_flux(a, 30, 30, 5, 25) is None  # the background annulus is outside the picture
    assert er.ratio(None, None) is None
