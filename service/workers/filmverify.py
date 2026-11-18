"""On − off photometry of candidate movers in the exposures of a field (engine/onoff.py), reading each exposure once for every place in
it."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from engine import onoff, photometry
from engine.spx import flags as spxflags
from engine.spx import psf as spxpsf
from engine.spx import wcs as spxwcs
from indexer.store import Exposure
from service.reader.calib import WavelengthMaps
from service.reader.files import FileReader
from service.reader.psfs import PsfProvider

HALF = photometry.HALF
Item = tuple[int, NDArray[np.float64], NDArray[np.bool_], NDArray[np.bool_], NDArray[np.float64], float, float, float]
OMEGA_SR = (6.15 / 206264.806) ** 2  # a SPHEREx pixel; the on − off significance does not depend on the flux scale


@dataclass(frozen=True)
class Place:
    ra: float
    dec: float
    det: int
    mjd: float  # when the candidate was seen
    visit: int  # the visit (film frame) of the candidate


def fit_stamps(
    var: float, items: list[tuple[int, NDArray[np.float64], NDArray[np.bool_], NDArray[np.bool_], NDArray[np.float64], float, float, float]]
) -> list[tuple[int, onoff.Point]]:
    """PSF fits of many stamps (a worker process): -> (place, point) for the stamps that could be fitted."""
    out: list[tuple[int, onoff.Point]] = []
    for k, st, usable, saturated, psf, fx, fy, lam in items:
        pt = photometry.fit(st, np.full(st.shape, var), usable, saturated, psf, fx, fy, OMEGA_SR)
        if pt.ok and pt.flux_mjy is not None and pt.err_mjy is not None:
            out.append((k, onoff.Point(pt.flux_mjy, pt.err_mjy, lam, True)))
    return out


def stamps_and_fits(
    img_raw: NDArray[np.generic],
    flg_raw: NDArray[np.generic],
    y0: int,
    px: NDArray[np.float64],
    py: NDArray[np.float64],
    inside: NDArray[np.bool_],
    cw_rows: NDArray[np.float64],
    bad_mask: int,
    over_mask: int,
    ps: Any,
) -> list[tuple[int, onoff.Point]]:
    """The stamps of every place of one exposure and their PSF fits (a worker process). `img_raw`/`flg_raw`: the detector rows y0.. read;
    `cw_rows`: the wavelength map of the same rows; px, py: the places on the detector; inside: which of them the window covers."""
    img = img_raw.astype(np.float64)
    flg = flg_raw.astype(np.int64)
    good = np.isfinite(img) & (flg == 0)
    if good.sum() < 100:
        return []
    var = max(1.4826 * float(np.median(np.abs(img[good] - np.median(img[good])))), 1e-6) ** 2
    bad = (flg & bad_mask) != 0
    overflow = (flg & over_mask) != 0
    items: list[Item] = []
    for k in np.where(inside)[0]:
        x, y = float(px[k]), float(py[k])
        xi, yi = round(x), round(y)
        sl = (slice(yi - HALF - y0, yi + HALF + 1 - y0), slice(xi - HALF, xi + HALF + 1))
        st = img[sl]
        lam = float(cw_rows[yi - y0, xi])
        if st.shape != (2 * HALF + 1, 2 * HALF + 1) or not (math.isfinite(lam) and lam > 0):
            continue
        usable = np.isfinite(st) & ~bad[sl] & ~overflow[sl]
        saturated = overflow[sl] | (~np.isfinite(st) & ~bad[sl])
        fx, fy = x - xi, y - yi
        items.append((int(k), st, usable, saturated, spxpsf.stamp(ps, ps.zone_index(x, y), fx, fy, HALF), fx, fy, lam))
    return fit_stamps(var, items)


async def measure_places(
    fr: FileReader,
    wl: WavelengthMaps,
    psfs: PsfProvider,
    key: str,
    det: int,
    places: Sequence[tuple[float, float]],
    compute: Callable[..., Awaitable[Any]],
) -> list[onoff.Point | None]:
    """The PSF flux at every place of one exposure (None where the place is off the detector or the window could not be read).
    The reading is done here; every array step runs in a worker process."""
    lay = await fr.layout(key)
    w = spxwcs.celestial(lay.image_header)
    ra = np.array([p[0] for p in places])
    dec = np.array([p[1] for p in places])
    px, py = spxwcs.sky2pix(w, ra, dec)
    nx, ny = lay.image.nx, lay.image.ny
    inside = np.isfinite(px) & np.isfinite(py) & (px >= HALF) & (py >= HALF) & (px <= nx - 1 - HALF) & (py <= ny - 1 - HALF)
    out: list[onoff.Point | None] = [None] * len(places)
    if not inside.any():
        return out
    y0 = int(np.floor(py[inside].min())) - HALF
    y1 = int(np.ceil(py[inside].max())) + HALF + 1
    img_raw = await fr.rows(key, "image", y0, y1)
    flg_raw = await fr.rows(key, "flags", y0, y1)
    table, _, _ = spxflags.resolve(lay.flags_header)
    ps = await psfs.for_file(key)
    cw, _ = await wl.get(det)
    bad_mask = spxflags.mask_of(table, [n for n in spxflags.BAD_ALWAYS if n not in spxflags.MOVER_KEEP])
    over_mask = spxflags.mask_of(table, spxflags.SATURATION)
    cw_rows = np.ascontiguousarray(cw[y0:y1])
    for k, pt in await compute(stamps_and_fits, img_raw, flg_raw, y0, px, py, inside, cw_rows, bad_mask, over_mask, ps):
        out[k] = pt
    return out


async def verify(
    fr: FileReader,
    wl: WavelengthMaps,
    store_dir: Path,
    exps: Sequence[Exposure],
    visit_of: dict[int, int],
    places: Sequence[Place],
    nmin: Sequence[int],
    compute: Callable[..., Awaitable[Any]],
    progress: object = None,
) -> list[dict[str, object] | None]:
    """On − off significance of every place. `exps`: every exposure of the field's detectors (all passes: the references); `visit_of`:
    exposure index -> visit
    for the exposures of the searched pass. -> per place None or the `onoff.reduce` dict."""
    psfs = PsfProvider(fr, store_dir)
    sem = asyncio.Semaphore(24)
    on: dict[int, list[onoff.Point]] = {k: [] for k in range(len(places))}
    ref: dict[int, list[onoff.Point]] = {k: [] for k in range(len(places))}
    done = 0

    async def one(i: int) -> None:
        nonlocal done
        e = exps[i]
        mine = [k for k, p in enumerate(places) if p.det == e.det]
        if not mine:
            return
        async with sem:
            try:
                pts = await measure_places(fr, wl, psfs, e.key, e.det, [(places[k].ra, places[k].dec) for k in mine], compute)
            except Exception:  # an unreadable exposure is one missing measurement, never a failed search
                pts = [None] * len(mine)
        mid = 0.5 * (e.mjd0 + e.mjd1)
        for k, pt in zip(mine, pts, strict=True):
            if pt is None:
                continue
            if visit_of.get(i) == places[k].visit:
                on[k].append(pt)
            elif abs(mid - places[k].mjd) >= onoff.REF_GAP_DAYS:
                ref[k].append(pt)
        done += 1
        if callable(progress):
            progress(done, len(exps))

    await asyncio.gather(*(one(i) for i in range(len(exps))))
    # the on − off reductions of every place (a median per on-exposure): a worker process, not the event loop (it held it a second)
    got: list[dict[str, object] | None] = await compute(reduce_all, on, ref, list(nmin))
    return got


def reduce_all(on: dict[int, list[onoff.Point]], ref: dict[int, list[onoff.Point]], nmin: list[int]) -> list[dict[str, object] | None]:
    """`onoff.reduce` of every place (module level: it runs in a worker process)."""
    return [onoff.reduce(on[k], ref[k], nmin[k]) for k in range(len(nmin))]
