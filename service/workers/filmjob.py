"""The film job (docs/plan/02-methods/2.3-views.md 2.3.10): the visits of one pass as frames, published as one SXP1 part.

film.sxp   sky (frames × n × n, uint8: the display law of the lab × 255; 0 = no data) and motion (uint8: 0 = masked or no data,
           1 + 254 × clip((z + 5) / 20) otherwise); meta: grid, the pass span, one entry per frame (visit, detector, MJD, exposures)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

import numpy as np

from engine.film import FILM_DETECTORS, build_film, film_grid
from engine.passes import split_passes, split_visits
from indexer.store import Exposure, Index
from service.jobs.scheduler import Context
from service.products.store import Store
from service.products.sxp import encode
from service.reader.calib import WavelengthMaps
from service.reader.files import FileReader
from service.workers.fieldjob import canonical
from service.workers.native import read_native

KIND = "film"
LO, HI, SOFT = -0.05, 400.0, 0.4


def pass_exposures(index: Index, params: dict[str, Any]) -> tuple[list[Exposure], tuple[float, float]]:
    """The D1–D3 exposures of the chosen pass: the pass nearest `mjd` when given, else the one with the most of them."""
    found = [e for e in index.overlapping(params["ra"], params["dec"], params["fov"] / 60.0 / 2.0 * 2**0.5) if e.det in FILM_DETECTORS]
    passes = split_passes([e.mjd0 for e in found])
    if not passes:
        return [], (0.0, 0.0)
    mjd = params.get("mjd")
    p = min(passes, key=lambda q: abs(q.mjd_mid - float(mjd))) if mjd is not None else max(passes, key=lambda q: len(q.members))
    return [found[i] for i in p.members], (p.mjd0, p.mjd1)


def film_params(ra: float, dec: float, fov: float, mjd: float | None) -> dict[str, Any]:
    p = canonical(ra, dec, fov)
    if mjd is not None:
        p["mjd"] = round(float(mjd), 1)
    return p


def signature(exps: list[Exposure]) -> str:
    return hashlib.sha256(json.dumps(sorted(e.key for e in exps)).encode()).hexdigest()[:16]


def _law(x: np.ndarray) -> np.ndarray:
    return np.asarray(np.clip(np.arcsinh((x - LO) / SOFT) / np.arcsinh((HI - LO) / SOFT), 0, 1), float)


async def run_film(
    ctx: Context, index: Index, fr: FileReader, store: Store, params: dict[str, Any], wavelengths: WavelengthMaps | None = None
) -> dict[str, Any]:
    key = ctx.job.key
    store.begin(KIND, key, params)
    exps, (m0, m1) = pass_exposures(index, params)
    grid = film_grid(params["ra"], params["dec"], params["fov"])
    visits = split_visits([e.mjd0 for e in exps], [e.det for e in exps])
    visit_of = {i: k for k, v in enumerate(visits) for i in v}
    natives = []
    done = 0
    sem = asyncio.Semaphore(32)

    async def one(i: int) -> None:
        nonlocal done
        async with sem:
            try:
                ne = await read_native(fr, exps[i].key, grid, visit_of[i], keep_outlier=True, wavelengths=wavelengths)
            except Exception:  # an unreadable exposure is counted below, never hidden
                ne = None
        done += 1
        ctx.phase(f"reading the exposures of the pass: {done} of {len(exps)}", done, len(exps))
        if ne is not None and ne.val.size:
            natives.append(ne)

    await asyncio.gather(*(one(i) for i in range(len(exps))))
    await ctx.checkpoint()
    ctx.phase("making the frames of the visits")
    frames = await ctx.compute(build_film, natives, grid)
    sky = np.zeros((len(frames), grid.n, grid.n), np.uint8)
    motion = np.zeros_like(sky)
    for k, f in enumerate(frames):
        ok = np.isfinite(f.sky)
        sky[k][ok] = np.clip(np.round(1 + 254 * _law(f.sky[ok])), 1, 255).astype(np.uint8)
        if f.motion is not None:
            okm = np.isfinite(f.motion)
            motion[k][okm] = np.clip(np.round(1 + 254 * np.clip((f.motion[okm] + 5.0) / 20.0, 0, 1)), 1, 255).astype(np.uint8)
    meta = {
        "kind": KIND,
        "params": params,
        "grid": {
            "ra0": grid.ra0,
            "dec0": grid.dec0,
            "step_arcsec": grid.step_arcsec,
            "n": grid.n,
            "orientation": "row 0 north, column 0 east (east left)",
        },
        "pass": {"mjd0": m0, "mjd1": m1},
        "exposures": len(exps),
        "read": len(natives),
        "frames": [{"visit": f.visit, "det": f.det, "mjd": f.mjd, "exposures": f.n_exposures} for f in frames],
        "encoding": {
            "sky": "1 + 254 × the lab's display law; 0 = no data",
            "motion": "1 + 254 × clip((z + 5) / 20); 0 = masked or no data",
        },
    }
    store.write_part(KIND, key, "film.sxp", encode({"sky": sky, "motion": motion}, meta))
    store.write_meta(KIND, key, meta)
    store.complete(key)
    ctx.part_ready("film")
    return {"frames": len(frames), "exposures": len(exps)}
