"""The field's known objects as one stable set (service/known_stable.py): rings that stay put from pass to pass."""

from __future__ import annotations

from typing import Any

import numpy as np

from engine.grid import grid_for_fov
from service import known_stable
from service.products.sxp import encode

RA0, DEC0, EPOCH = 120.0, -20.0, 61000.0


def _blob(n: int, x: float, y: float, amp: float, fwhm: float = 4.0) -> np.ndarray:
    yy, xx = np.mgrid[0:n, 0:n]
    return amp * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * (fwhm / 2.355) ** 2))


def _field() -> tuple[list[bytes], dict[str, Any], list[tuple[float, float]], Any]:
    g = grid_for_fov(RA0, DEC0, 10.0)
    rng = np.random.default_rng(5)
    stars = [(rng.uniform(10, g.n - 10), rng.uniform(10, g.n - 10)) for _ in range(30)]
    raws = []
    for k in range(3):
        img = sum(_blob(g.n, x, y, 3.0) for x, y in stars) + np.random.default_rng(20 + k).normal(0.0, 0.02, (g.n, g.n))
        if k == 0:
            img = img + _blob(g.n, g.n / 2 + 7.3, g.n / 2 - 9.1, 3.0)  # in one pass only: not a source of the field
        raws.append(encode({"white": img.astype(np.float16), "sat": np.zeros((g.n, g.n), np.uint8)}, {"pass": k}))
    grid = {"ra0": g.ra0, "dec0": g.dec0, "step_arcsec": g.step_arcsec, "n": g.n}
    ra, dec = g.grid_to_sky([x for x, _ in stars], [y for _, y in stars])
    return raws, grid, [(float(a), float(d)) for a, d in zip(ra, dec, strict=True)], g


def test_the_stable_set_rings_what_the_field_shows_in_most_passes() -> None:
    raws, grid, sky, g = _field()
    measured = known_stable.measured_sources(raws, grid)
    assert len(measured) == 30 and all(m["passes"] == 3 for m in measured)  # the one-pass source is out
    # the catalogues know 25 of the 30 stars, one bright star the field does not show (in a gap), a faint one below the field's
    # depth, and a JPL body (it moves: not in the stable set)
    objects = [
        {"id": f"gaia:{i}", "source": "gaia", "mag": 12.0 + i * 0.2, "mag_band": "G", "label": False,
         "positions": [{"epoch": EPOCH, "ra": ra, "dec": dec}]}
        for i, (ra, dec) in enumerate(sky[:25])
    ]  # fmt: skip
    gr, gd = g.grid_to_sky([5.0], [5.0])
    gap = (float(gr[0]), float(gd[0]))
    objects.append({"id": "gaia:gap", "source": "gaia", "mag": 13.0, "mag_band": "G", "label": False,
                    "positions": [{"epoch": EPOCH, "ra": gap[0], "dec": gap[1]}]})  # fmt: skip
    objects.append({"id": "gaia:faint", "source": "gaia", "mag": 20.5, "mag_band": "G", "label": False,
                    "positions": [{"epoch": EPOCH, "ra": gap[0] + 0.01, "dec": gap[1]}]})  # fmt: skip
    objects.append({"id": "jpl:1", "source": "jpl", "mag": 18.0, "mag_band": "V", "label": True,
                    "positions": [{"epoch": EPOCH, "ra": sky[26][0], "dec": sky[26][1]}]})  # fmt: skip
    got = known_stable.stable_set(measured, {"objects": objects, "match": []}, EPOCH)
    by = {s["id"]: s for s in got["sources"]}
    assert all(by[f"gaia:{i}"]["seen"] for i in range(25)) and by["gaia:0"]["sep_arcsec"] < 3.0
    assert by["gaia:gap"]["seen"] is False  # as bright as the field shows, yet not there: kept, said not seen
    assert "gaia:faint" not in by and "jpl:1" not in by  # below the depth; a body that moves
    assert 16.0 < got["depth"]["G"] <= 16.8
    assert len(got["unmatched"]) == 5  # the stars no catalogue has (jpl's place does not count as a catalogue source)
