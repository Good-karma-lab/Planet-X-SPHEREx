"""Synthetic photometry of a spectrum under the 2MASS and WISE filters."""

from __future__ import annotations

import numpy as np
import pytest

from service.catalogue_check import synthetic


def points(f: callable) -> list[dict[str, object]]:  # type: ignore[valid-type]
    lam = np.linspace(0.75, 5.0, 400)
    return [{"ok": True, "lam": float(x), "flux": float(f(x))} for x in lam]


def test_a_flat_spectrum_gives_its_level_in_every_covered_band() -> None:
    pts = points(lambda x: 10.0)
    for band in ("2mass_j", "2mass_h", "2mass_ks", "wise_w1"):
        v, why = synthetic(pts, band)
        assert why == "" and v == pytest.approx(10.0, rel=1e-6)
    v, why = synthetic(pts, "wise_w2")
    assert v is None and "beyond" in why


def test_a_hole_under_the_filter_is_said() -> None:
    pts = [p for p in points(lambda x: 5.0) if not 1.15 < p["lam"] < 1.30]  # type: ignore[operator]
    v, why = synthetic(pts, "2mass_j")
    assert v is None and "hole" in why
