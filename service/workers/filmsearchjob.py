"""The film search job: the unknown movers of the film of one pass (engine/filmsearch.py).

It reads the exposures of the pass again (the reader's caches make that cheap once the film is built), builds the frames and searches
their motion
maps; the result is the product `filmsearch` (meta.json: tracks, the chance control, the film grid) keyed by `product_key(KIND, film
params, extra=(film key,))`.
"""

from __future__ import annotations

import asyncio
import math
from typing import Any

import numpy as np

from engine.film import FILM_DETECTORS, build_film, film_grid
from engine.filmsearch import VERIFY_MAX, find_candidates, search
from engine.passes import split_visits
from indexer.store import Index
from service.jobs.scheduler import Context
from service.products.store import Store
from service.reader.calib import WavelengthMaps
from service.reader.files import FileReader
from service.workers.filmjob import pass_exposures
from service.workers.filmverify import Place, verify
from service.workers.native import read_native

KIND = "filmsearch"


async def run_filmsearch(
    ctx: Context,
    index: Index,
    fr: FileReader,
    store: Store,
    film_key: str,
    params: dict[str, Any],
    wavelengths: WavelengthMaps | None = None,
) -> dict[str, Any]:
    key = ctx.job.key
    store.begin(KIND, key, params)
    exps, _ = pass_exposures(index, params)
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
            except Exception:  # an unreadable exposure is counted, never hidden
                ne = None
        done += 1
        ctx.phase(f"reading the exposures of the pass: {done} of {len(exps)}", done, len(exps))
        if ne is not None and ne.val.size:
            natives.append(ne)

    await asyncio.gather(*(one(i) for i in range(len(exps))))
    await ctx.checkpoint()
    ctx.phase("making the frames of the visits")
    frames = await ctx.compute(build_film, natives, grid)
    cands = find_candidates(frames, grid.step_arcsec, grid.n)
    top = cands[:VERIFY_MAX]
    # the exposures that stand in for "off": every exposure of the field's detectors in every pass
    allx = [e for e in index.overlapping(params["ra"], params["dec"], params["fov"] / 60.0 / 2.0 * 2**0.5) if e.det in FILM_DETECTORS]
    pass_index = {e.key: i for i, e in enumerate(exps)}
    visit_all = {i: visit_of[pass_index[e.key]] for i, e in enumerate(allx) if e.key in pass_index}
    ra, dec = grid.grid_to_sky(np.array([q.x for q in top]), np.array([q.y for q in top]))
    ctl_src = list(range(0, len(top), 8))  # a control: the same measurement 40 arcsec to the side of every eighth candidate
    places = [
        Place(float(a), float(d), frames[q.frame].det, frames[q.frame].mjd, frames[q.frame].visit)
        for q, a, d in zip(top, ra, dec, strict=True)
    ]
    places += [
        Place(
            places[k].ra + 40.0 / 3600.0 / max(math.cos(math.radians(places[k].dec)), 1e-3),
            places[k].dec,
            places[k].det,
            places[k].mjd,
            places[k].visit,
        )
        for k in ctl_src
    ]
    nmin = [min(3, 3) for _ in places]

    def verify_progress(done_: int, total_: int) -> None:
        ctx.phase(f"checking {len(top)} candidates by on − off photometry: exposure {done_} of {total_}", done_, total_)

    ver = (
        await verify(fr, wavelengths, store.root, allx, visit_all, places, nmin, ctx.compute, verify_progress)
        if wavelengths is not None
        else [None] * len(places)
    )
    verified = {k: v for k, v in enumerate(ver[: len(top)]) if v is not None}
    control = [v for v in ver[len(top) :] if v is not None]
    obs = await observers(fr, exps, visit_of, frames)
    ctx.phase("linking the verified candidates")
    result = await ctx.compute(search, frames, cands, grid.step_arcsec, grid.n, verified, obs, grid.ra0, grid.dec0)
    result["verification"] = {
        **result.get("verification", {}),
        "control_measured": len(control),
        "control_passed": sum(1 for v in control if v["ok"]),
    }
    meta = {
        "kind": KIND,
        "film": film_key,
        "grid": {
            "ra0": grid.ra0,
            "dec0": grid.dec0,
            "step_arcsec": grid.step_arcsec,
            "n": grid.n,
            "orientation": "row 0 north, column 0 east",
        },
        "frames": [{"visit": f.visit, "det": f.det, "mjd": f.mjd} for f in frames],
        "exposures": len(exps),
        "read": len(natives),
        **result,
    }
    store.write_meta(KIND, key, meta)
    store.complete(key)
    return {"tracks": len(result["tracks"]), "candidates": result["candidates"]}


async def observers(fr: FileReader, exps: list[Any], visit_of: dict[int, int], frames: list[Any]) -> dict[int, tuple[float, Any]]:
    """For every frame the mean time and the mean heliocentric place of the telescope over the exposures of its visit: the Earth plus
    the spacecraft
    (X_SC, Y_SC, Z_SC of the exposures' headers, km from the Earth's centre)."""
    from engine import orbit as _orbit

    eorbit: Any = _orbit
    by_visit: dict[int, list[int]] = {}
    for i, v in visit_of.items():
        by_visit.setdefault(v, []).append(i)
    out: dict[int, tuple[float, Any]] = {}
    for k, f in enumerate(frames):
        mjds: list[float] = []
        sc: list[list[float]] = []
        for i in by_visit.get(f.visit, []):
            if exps[i].det != f.det:
                continue
            h = (await fr.layout(exps[i].key)).image_header
            try:
                xyz = [float(h["X_SC"]), float(h["Y_SC"]), float(h["Z_SC"])]
            except (KeyError, ValueError):
                continue
            mjds.append(0.5 * (exps[i].mjd0 + exps[i].mjd1))
            sc.append(xyz)
        if mjds:
            out[k] = eorbit.observer(mjds, sc)
    return out
