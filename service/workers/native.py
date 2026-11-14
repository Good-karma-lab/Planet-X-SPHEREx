"""One exposure of a field as engine input: the detector box that covers the field, its rows, masks, background, and the grid position
and wavelength of every pixel (docs/plan/02-methods/2.1-data-and-reading.md 2.1.2, 2.2-cleaning-and-stacks.md 2.2.1–2.2.2)."""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy.ndimage import distance_transform_edt

from engine import clean
from engine.field import NativeExposure
from engine.grid import Grid
from engine.spx import flags as spxflags
from engine.spx import wave as spxwave
from engine.spx import wcs as spxwcs
from service import cpu
from service.reader.calib import WavelengthMaps
from service.reader.files import FileReader

BOX_MARGIN_PX = 3
LAMBDA_BG_MIN_FOV_ARCMIN = 10.0
CENSUS_NAMES = tuple(n for g in spxflags.GROUPS.values() for n in g)


def grid_outline(grid: Grid, k: int = 9) -> tuple[np.ndarray, np.ndarray]:
    """Sky positions of k points along each edge of the grid (and its centre)."""
    t = np.linspace(-0.5, grid.n - 0.5, k)
    lo, hi = -0.5, grid.n - 0.5
    x = np.concatenate([t, t, np.full(k, lo), np.full(k, hi), [grid.centre_px]])
    y = np.concatenate([np.full(k, lo), np.full(k, hi), t, t, [grid.centre_px]])
    return grid.grid_to_sky(x, y)


async def read_native(
    fr: FileReader,
    key: str,
    grid: Grid,
    visit: int,
    target: tuple[float, float] | None = None,
    keep_outlier: bool = False,
    wavelengths: WavelengthMaps | None = None,
) -> NativeExposure | None:
    """The exposure inside the field, or None when the field is off this detector."""
    lay = await fr.layout(key)
    w = spxwcs.celestial(lay.image_header)
    ra, dec = grid_outline(grid)
    px, py = spxwcs.sky2pix(w, ra, dec)
    ok = np.isfinite(px) & np.isfinite(py)
    if not ok.any():
        return None
    nx, ny = lay.image.nx, lay.image.ny
    x0 = max(0, int(np.floor(px[ok].min())) - BOX_MARGIN_PX)
    x1 = min(nx, int(np.ceil(px[ok].max())) + BOX_MARGIN_PX + 1)
    y0 = max(0, int(np.floor(py[ok].min())) - BOX_MARGIN_PX)
    y1 = min(ny, int(np.ceil(py[ok].max())) + BOX_MARGIN_PX + 1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    img = (await fr.rows(key, "image", y0, y1))[:, x0:x1].astype(np.float32)
    flg = (await fr.rows(key, "flags", y0, y1))[:, x0:x1].astype(np.int64)
    lam_box = None
    if wavelengths is not None:
        try:
            cw, _ = await wavelengths.get(lay.detector)
            lam_box = np.ascontiguousarray(cw[y0:y1, x0:x1], dtype=np.float64)
        except Exception:  # the calibration map could not be read: the file's own table below
            lam_box = None
    wave = None if lam_box is not None else await fr.table_bytes(key, "wave")
    # the rest is compute: in a worker process, never in the API process
    return await cpu.run(
        prepare_native,
        key,
        lay.detector,
        lay.mjd,
        visit,
        lay.image_header,
        lay.flags_header,
        img,
        flg,
        x0,
        y0,
        grid,
        lam_box,
        wave,
        target,
        keep_outlier,
    )


def prepare_native(
    key: str,
    detector: int,
    mjd: float,
    visit: int,
    image_header: Any,
    flags_header: Any,
    img: np.ndarray,
    flg: np.ndarray,
    x0: int,
    y0: int,
    grid: Grid,
    lam_box: np.ndarray | None,
    wave: tuple[Any, bytes] | None,
    target: tuple[float, float] | None,
    keep_outlier: bool,
) -> NativeExposure:
    """Masks, background, grid position and wavelength of every pixel of the box (runs in a worker process)."""
    w = spxwcs.celestial(image_header)
    y1, x1 = y0 + img.shape[0], x0 + img.shape[1]
    table, _, _ = spxflags.resolve(flags_header)
    jj, ii = np.mgrid[y0:y1, x0:x1]
    xd, yd = ii.ravel().astype(float), jj.ravel().astype(float)
    pra, pdec = spxwcs.pix2sky(w, xd, yd)
    u, v = grid.sky_to_grid(pra, pdec)
    if lam_box is not None:
        lam = lam_box.ravel()
    else:
        assert wave is not None
        wh, wd = wave
        lam, _ = spxwave.WaveMap(spxwave.table_from_hdu_bytes(wh, wd), detector)(xd, yd)
    vals = img.ravel()
    flags_flat = flg.ravel()
    r_t = None
    if target is not None:
        tx, ty = spxwcs.sky2pix(w, [target[0]], [target[1]])
        r_t = np.hypot(xd - tx[0], yd - ty[0])
    overflow = spxflags.mask_of(table, spxflags.SATURATION)
    bad_always = spxflags.mask_of(table, spxflags.BAD_ALWAYS)
    sat0 = ((flg & overflow) != 0) | (~np.isfinite(img) & ((flg & bad_always) == 0))
    r_bright = distance_transform_edt(~sat0).ravel() if sat0.any() else None
    usable, sat = clean.pixel_mask(flags_flat, vals, table, r_t, keep_outlier=keep_outlier, r_bright_px=r_bright)
    inside = (u > -1.5) & (u < grid.n + 0.5) & (v > -1.5) & (v < grid.n + 0.5)
    usable &= inside
    bins = clean.LAMBDA_BINS if grid.fov_arcmin >= LAMBDA_BG_MIN_FOV_ARCMIN else 0
    vals_bg = clean.subtract_background(vals, usable, lam, bins)
    sigma = clean.robust_sigma(vals_bg, usable)
    census = {}
    for name in CENSUS_NAMES:
        bit = table.get(name)
        if bit is None:
            continue
        m = inside & ((flags_flat & (1 << bit)) != 0)
        if m.any():
            census[name] = (u[m].astype(np.float32), v[m].astype(np.float32))
    satm = sat & inside
    unreliable = spxflags.mask_of(table, ("TRANSIENT", "SUR_ERROR"))
    fit_ok = ((flags_flat & unreliable) == 0)[usable]
    return NativeExposure(
        key=key,
        det=detector,
        mjd=mjd,
        visit=visit,
        u=u[usable].astype(np.float32),
        v=v[usable].astype(np.float32),
        val=vals_bg[usable].astype(np.float32),
        lam=lam[usable].astype(np.float32),
        sigma=float(sigma) if np.isfinite(sigma) and sigma > 0 else 1.0,
        sat_u=u[satm].astype(np.float32),
        sat_v=v[satm].astype(np.float32),
        census=census,
        footprint_uv=(u[inside].astype(np.float32), v[inside].astype(np.float32)),
        fit_ok=fit_ok,
    )
