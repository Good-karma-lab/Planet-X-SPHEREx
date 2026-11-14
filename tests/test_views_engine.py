from __future__ import annotations

import numpy as np
import pytest

from engine.colour import colour_matrix, lam_rgb, white_balance
from engine.diff import stouffer, z_map
from engine.halo import accept, halo_light, saturated_zones
from engine.register import register, shift_sums, stars

STEP = 1.5375


def star_field(n: int, rng: np.random.Generator, shift: tuple[float, float] = (0.0, 0.0), k: int = 25) -> np.ndarray:
    img = rng.normal(0, 0.05, (n, n))
    jj, ii = np.mgrid[0:n, 0:n]
    pos = np.random.default_rng(42).uniform(15, n - 15, (k, 2))
    for x, y in pos:
        img += 20 * np.exp(-((ii - x - shift[0]) ** 2 + (jj - y - shift[1]) ** 2) / (2 * 2.5**2))
    return img


def test_z_map_has_unit_width_on_noise_and_a_changed_source_stands_out() -> None:
    rng = np.random.default_rng(0)
    n = 160
    a = star_field(n, rng)
    b = star_field(n, rng)
    sig = np.full((n, n), 0.02)  # the model noise is wrong by 2.5×: the calibration must fix it
    b[80, 80] += 3.0  # a source that brightened (a single bright pixel on empty sky)
    z = z_map(a, b, sig, sig, STEP)
    empty = np.abs(a) < 0.3
    empty[75:86, 75:86] = False
    w = 1.4826 * np.median(np.abs(z[empty] - np.median(z[empty])))
    assert w == pytest.approx(1.0, abs=0.1)
    assert z[80, 80] > 10  # 3 / sqrt((0.05·√2)² + (5 % of 3)²) ≈ 18


def test_stouffer_counts_only_present_bands() -> None:
    z1 = np.array([[2.0, np.nan]])
    z2 = np.array([[2.0, 3.0]])
    s = stouffer([z1, z2])
    assert s[0, 0] == pytest.approx(4 / np.sqrt(2)) and s[0, 1] == pytest.approx(3.0)


def test_registration_recovers_a_shift() -> None:
    rng = np.random.default_rng(1)
    ref = star_field(200, rng)
    cur = star_field(200, rng, shift=(0.6, -0.4))
    r = register(stars(ref, None, STEP), stars(cur, None, STEP), STEP)
    assert r.n_stars >= 10
    assert r.dx == pytest.approx(-0.6, abs=0.1) and r.dy == pytest.approx(0.4, abs=0.1)
    back = shift_sums(cur, r.dx, r.dy)
    assert np.abs(back - ref)[20:-20, 20:-20].std() < np.abs(cur - ref)[20:-20, 20:-20].std()


def test_flat_spectrum_is_grey_and_red_is_long() -> None:
    m = colour_matrix()
    rgb = m @ np.ones(6)
    np.testing.assert_allclose(rgb, 1.0, atol=1e-9)
    r, g, b = lam_rgb(4.8)
    assert r > g and r > b
    r, g, b = lam_rgb(0.8)
    assert b > r
    m4 = colour_matrix((2, 3, 5, 6))
    assert np.all(m4[:, [0, 3]] == 0)


def test_white_balance() -> None:
    g = np.full(100, 2.0)
    r = np.full(100, 4.0)
    b = np.full(100, 1.0)
    kr, kb = white_balance(r, g, b, np.ones(100, bool))
    assert kr == pytest.approx(0.5) and kb == pytest.approx(2.0)
    assert white_balance(r, g, b, np.zeros(100, bool)) == (1.0, 1.0)


def test_a_source_in_a_halo_is_kept_and_spikes_are_not() -> None:
    n = 200
    jj, ii = np.mgrid[0:n, 0:n]
    r = np.hypot(ii - 100, jj - 100)
    img = 50.0 * np.exp(-r / 25.0)  # the halo of a bright star
    img[100, 140:144] += 1.0  # a spike: faint against the halo
    img += 30.0 * np.exp(-((ii - 130) ** 2 + (jj - 100) ** 2) / (2 * 2.0**2))  # a real source inside the halo
    halo, hl = halo_light(img, STEP)
    ok = accept(img, halo, hl, noise=0.05)
    assert ok[100, 130] and not ok[100, 141]


def test_saturated_zone_is_one_source_if_large_enough() -> None:
    sat = np.zeros((100, 100))
    sat[40:50, 40:50] = 1.0  # 100 grid px > one detector pixel (16 grid px)
    sat[10, 10:13] = 1.0  # 3 grid px: not a star
    z = saturated_zones(sat, STEP)
    assert len(z) == 1 and z[0][0] == pytest.approx(44.5) and z[0][1] == pytest.approx(44.5)
