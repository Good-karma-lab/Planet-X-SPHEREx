"""Brightness of a (saturated) star in every pass and detector of a finished field, from the ring of light outside its saturated core.

`GET /api/v1/brightness?ra&dec&fov&pmra&pmdec`: (ra, dec) is where the star stands at 2025.9 (the page's marker), the proper motion carries
it to every pass. The ring is the same on every pass and detector (engine/ring.py); stars of the same field with unsaturated cores, measured
with the same ring, give the scatter of the ratio between passes that the method really has (`control`): a change smaller than that is not
claimed.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy.ndimage import maximum_filter

from engine import ring as er
from engine.grid import NATIVE_PIXEL_ARCSEC, Grid
from service import cpu
from service.products.sxp import decode
from service.red import read_parts

# the ring outside the core, in detector pixels (it starts at the core radius + 1 when the core is larger)
RING_NATIVE = (3.0, 8.0)
MAX_CONTROLS = 12
# the ring of the control stars (unsaturated, so it starts near the core): small, so that a small field holds some
CONTROL_RING_NATIVE = (2.0, 5.0)
MJY_SR_TO_MJY = 1e9  # MJy/sr x sr -> mJy: 1 MJy = 1e9 mJy


def year_of(mjd: float) -> float:
    return 2000.0 + (mjd - 51544.5) / 365.25


def brightness_result(
    meta: dict[str, Any], raws: list[tuple[bytes, bytes]], ra: float, dec: float, pmra: float, pmdec: float
) -> dict[str, Any]:
    """The ring photometry of the star and of the control stars in every pass (a worker process)."""
    g = meta["grid"]
    grid = Grid(g["ra0"], g["dec0"], g["step_arcsec"], g["n"])
    step = g["step_arcsec"]
    native = NATIVE_PIXEL_ARCSEC / step
    dets, sats, whites = [], [], []
    for raw, q in raws:
        _, a = decode(raw)
        _, b = decode(q)
        dets.append(a["dets"].astype(np.float32))
        sats.append(b["sat"].astype(np.float32) / 255.0)
        whites.append(b["white"].astype(np.float32))
    passes = meta["passes"]
    sr = (step / 206264.806) ** 2  # the solid angle of a grid pixel

    def at(k: int) -> tuple[float, float]:
        dt = year_of(passes[k]["mjd_mid"]) - 2025.9
        d = dec + pmdec * dt / 3.6e6
        a = ra + pmra * dt / 3.6e6 / math.cos(math.radians(d))
        x, y = grid.sky_to_grid([a], [d])
        return float(x[0]), float(y[0])

    pos = [at(k) for k in range(len(passes))]
    cores = [er.core_radius(sats[k], pos[k][0], pos[k][1], 10 * native) for k in range(len(passes))]
    r1 = max(RING_NATIVE[0] * native, (max(cores) + 1.0 * native) if cores else 0.0)
    r2 = max(RING_NATIVE[1] * native, r1 + 4.0 * native)

    def rings(x: float, y: float, k: int, a: float = r1, b: float = r2) -> dict[int, er.Ring | None]:
        return {d: er.ring_flux(dets[k][d], x, y, a, b) for d in range(6)}

    c1, c2 = CONTROL_RING_NATIVE[0] * native, CONTROL_RING_NATIVE[1] * native

    target = [rings(*pos[k], k) for k in range(len(passes))]
    # control stars: the brightest unsaturated peaks of the first pass's white stack, away from the target and the edges
    w0 = np.nan_to_num(whites[0], nan=0.0)
    pk = (w0 == maximum_filter(w0, size=int(6 * native) | 1)) & (w0 > 0)
    ys, xs = np.nonzero(pk)
    order = np.argsort(-w0[ys, xs])
    ctrl: list[tuple[float, float]] = []
    margin = er.BG_FACTOR[1] * c2 + 2.0
    for i in order:
        x, y = float(xs[i]), float(ys[i])
        if not (margin < x < grid.n - margin and margin < y < grid.n - margin):
            continue
        if math.hypot(x - pos[0][0], y - pos[0][1]) < 8 * native:
            continue
        lo_y, lo_x = max(0, int(y) - 3), max(0, int(x) - 3)
        if any(float(sats[k][lo_y : int(y) + 4, lo_x : int(x) + 4].max()) > er.SAT_LEVEL for k in range(len(passes))):
            continue
        ctrl.append((x, y))
        if len(ctrl) >= MAX_CONTROLS:
            break
    ctrl_ratios: dict[int, dict[int, list[float]]] = {d: {k: [] for k in range(1, len(passes))} for d in range(6)}
    for x, y in ctrl:
        base = rings(x, y, 0, c1, c2)
        for k in range(1, len(passes)):
            cur = rings(x, y, k, c1, c2)
            for d in range(6):
                rr = er.ratio(cur[d], base[d])
                if rr is not None:
                    ctrl_ratios[d][k].append(rr[0])
    control: dict[str, Any] = {"stars": len(ctrl), "ring_native_px": list(CONTROL_RING_NATIVE), "scatter": {}}
    for d in range(6):
        allr = [v for k in ctrl_ratios[d] for v in ctrl_ratios[d][k]]
        if len(allr) >= 4:
            control["scatter"][f"D{d + 1}"] = round(1.4826 * float(np.median(np.abs(np.array(allr) - np.median(allr)))), 3)
    out_passes = []
    for k, p in enumerate(passes):
        row: dict[str, Any] = {
            "index": p["index"],
            "mjd": round(p["mjd_mid"], 3),
            "core_radius_native_px": round(cores[k] / native, 2),
            "detectors": {},
        }
        for d in range(6):
            r = target[k][d]
            if r is None:
                row["detectors"][f"D{d + 1}"] = None
                continue
            rr = er.ratio(r, target[0][d]) if k else (1.0, 0.0) if r.flux > 0 else None
            row["detectors"][f"D{d + 1}"] = {
                "flux_mjy": round(r.flux * sr * MJY_SR_TO_MJY, 4),
                "err_mjy": round(r.err * sr * MJY_SR_TO_MJY, 4),
                "ratio_to_first": None if rr is None else round(rr[0], 3),
                "ratio_err": None if rr is None else round(rr[1], 3),
            }
        out_passes.append(row)
    return {
        "ring_native_px": [round(r1 / native, 2), round(r2 / native, 2)],
        "passes": out_passes,
        "control": control,
        "note": (
            "the ring outside the saturated core, the same on every pass and detector; the ratio between passes is the change of "
            "brightness, the absolute value is not a flux"
        ),
    }


async def brightness_of(store: Any, field_key: str, ra: float, dec: float, pmra: float, pmdec: float) -> dict[str, Any]:
    import asyncio

    meta, raws = await asyncio.to_thread(read_parts, store, field_key)
    result: dict[str, Any] = await cpu.run(brightness_result, meta, raws, ra, dec, pmra, pmdec)
    return result
