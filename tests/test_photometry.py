from __future__ import annotations

import numpy as np
import pytest

from engine.photometry import fit, neighbour_outliers
from tests.synth_l2 import gaussian_psf


def psf_on_stamp(fx: float, fy: float, sigma: float = 0.7) -> np.ndarray:
    jj, ii = np.mgrid[-3:4, -3:4]
    g = np.exp(-((ii - fx) ** 2 + (jj - fy) ** 2) / (2 * sigma**2))
    return g / g.sum()


def test_injected_flux_is_recovered() -> None:
    rng = np.random.default_rng(0)
    psf = psf_on_stamp(0.2, -0.1)
    stamp = 50.0 * psf + 0.3 + rng.normal(0, 0.05, psf.shape)
    var = np.full(psf.shape, 0.0025)
    p = fit(stamp, var, np.ones(psf.shape, bool), np.zeros(psf.shape, bool), psf, 0.2, -0.1, omega_sr=1.0e-9)
    assert p.ok and p.flux_mjy == pytest.approx(50.0, rel=0.02) and p.err_mjy is not None and p.err_mjy < 1.0


def test_a_hit_is_rejected_and_a_saturated_core_skipped() -> None:
    psf = psf_on_stamp(0, 0)
    stamp = 50.0 * psf
    stamp[0, 6] += 40.0  # a cosmic-ray hit in a corner
    var = np.full(psf.shape, 0.0025)
    p = fit(stamp, var, np.ones(psf.shape, bool), np.zeros(psf.shape, bool), psf, 0, 0, 1e-9)
    assert p.flux_mjy == pytest.approx(50.0, rel=0.02)
    sat = np.zeros(psf.shape, bool)
    sat[3, 3] = True
    assert fit(stamp, var, np.ones(psf.shape, bool), sat, psf, 0, 0, 1e-9).why == "saturated core"


def test_neighbour_outlier() -> None:
    lam = np.linspace(1, 2, 40)
    flux = np.ones(40) + np.random.default_rng(1).normal(0, 0.01, 40)
    flux[17] = 3.0
    bad = neighbour_outliers(lam, flux)
    assert bad[17] and bad.sum() == 1


def test_gaussian_psf_helper_is_normalised() -> None:
    assert gaussian_psf(41, 10, 0.6).sum() == pytest.approx(1.0)
