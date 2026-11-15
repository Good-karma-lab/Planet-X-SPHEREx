"""Red sources on synthetic detector stacks: a cold source bright at 3.8–5 µm and faint at 1.1–1.6 µm stands out among ordinary stars."""

from __future__ import annotations

import numpy as np

from engine.red import red_sources

STEP = 1.5375


def stacks(seed: int, stars: list[tuple[float, float, float, float]]) -> tuple[np.ndarray, np.ndarray]:
    """stars: (x, y, flux at D2, flux at D5–D6); other detectors follow a smooth ordinary colour."""
    rng = np.random.default_rng(seed)
    n = 160
    yy, xx = np.mgrid[0:n, 0:n]
    s = 8.8 / 2.3548 / STEP
    d = rng.normal(0, 0.01, (6, n, n))
    for x, y, f2, f56 in stars:
        g = np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * s * s))
        for i, f in enumerate([f2 * 1.1, f2, f2 * 0.7, f56 * 1.2, f56, f56]):
            d[i] += f * g
    return d.astype(np.float32), np.full((6, n, n), 0.01, np.float32)


def test_the_cold_source_is_red_and_the_stars_are_not() -> None:
    ordinary = [(30.0 + 20 * k, 40.0 + 9 * (k % 4), 1.0, 0.3) for k in range(6)] + [(40.0 + 15 * k, 120.0, 0.6, 0.18) for k in range(5)]
    cold = (100.0, 80.0, 0.15, 0.6)
    passes = [stacks(k, [*ordinary, cold]) for k in range(3)]
    rs = red_sources([p[0] for p in passes], [p[1] for p in passes], STEP)
    red = [r for r in rs if r.red]
    assert len(red) == 1 and abs(red[0].x - 100) < 3 and abs(red[0].y - 80) < 3
    assert red[0].colour > 3.0 and not red[0].moves


def test_a_cold_source_beside_a_bright_star_is_not_given_the_stars_light() -> None:
    """WISE 0855 on the ring in D5-D6 only, a bright star 4 detector pixels beside it: its D2 light is the star's, not the dwarf's."""
    n = 160
    ordinary = [(30.0 + 20 * k, 40.0 + 9 * (k % 4), 1.0, 0.3) for k in range(6)] + [(40.0 + 15 * k, 120.0, 0.6, 0.18) for k in range(5)]
    cold = (100.0, 80.0, 0.0, 0.6)
    neighbour = (104.0, 83.0, 3.0, 0.2)  # a star 5 pixels off: bright at D2, faint at D5-D6
    passes = [stacks(k, [*ordinary, cold, neighbour]) for k in range(3)]
    rs = red_sources([p[0] for p in passes], [p[1] for p in passes], STEP)
    near = [r for r in rs if abs(r.x - 100) < 4 and abs(r.y - 80) < 4]
    assert near and near[0].snr_short < 5.0 and near[0].lower_limit  # not "seen at D2": a lower limit on the colour
    assert n > 0
