"""Synthetic dithered exposures of a star field for engine tests (no FITS): native 6.15″ pixels at random sub-pixel offsets and small
rotations, each pixel the PSF integrated over its area, Gaussian noise."""

from __future__ import annotations

import numpy as np

from engine.grid import NATIVE_PIXEL_ARCSEC
from engine.stack import Exposure


def render(
    stars: list[tuple[float, float, float]],
    n_native: int,
    step_arcsec: float,
    n_grid: int,
    rng: np.random.Generator,
    psf_sigma_native: float = 0.45,
    noise: float = 0.05,
    oversample: int = 5,
) -> Exposure:
    """One exposure. `stars`: (x, y, flux) in grid pixels; returns native pixels with their grid positions (u, v)."""
    scale = NATIVE_PIXEL_ARCSEC / step_arcsec  # grid pixels per native pixel
    theta = rng.uniform(-0.2, 0.2)
    ox, oy = rng.uniform(0, 1, 2)
    c = (n_grid - 1) / 2.0
    jj, ii = np.mgrid[0:n_native, 0:n_native]
    px = (ii - n_native / 2 + ox).ravel()
    py = (jj - n_native / 2 + oy).ravel()
    ct, st = np.cos(theta), np.sin(theta)
    u = c + scale * (ct * px - st * py)
    v = c + scale * (st * px + ct * py)
    sub = (np.arange(oversample) + 0.5) / oversample - 0.5
    val = np.zeros(px.size)
    s = psf_sigma_native * scale
    for sx in sub:
        for sy in sub:
            uu = c + scale * (ct * (px + sx) - st * (py + sy))
            vv = c + scale * (st * (px + sx) + ct * (py + sy))
            for x, y, f in stars:
                val += f * np.exp(-((uu - x) ** 2 + (vv - y) ** 2) / (2 * s * s)) / (2 * np.pi * s * s) * scale * scale
    val /= oversample**2
    val += rng.normal(0, noise, val.size)
    return Exposure(u=u, v=v, val=val.astype(np.float32))
