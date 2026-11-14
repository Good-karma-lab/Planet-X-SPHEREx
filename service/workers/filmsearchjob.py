"""The film search job: the unknown movers of the film of one pass (engine/filmsearch.py).

It reads the exposures of the pass again (the reader's caches make that cheap once the film is built), builds the frames and searches
their motion
maps; the result is the product `filmsearch` (meta.json: tracks, the chance control, the film grid) keyed by `product_key(KIND, film
params, extra=(film key,))`.
"""

from __future__ import annotations

import asyncio
from typing import Any

from engine.film import build_film, film_grid
from engine.filmsearch import search
from engine.passes import split_visits
from indexer.store import Index
from service.jobs.scheduler import Context
from service.products.store import Store
from service.reader.calib import WavelengthMaps
from service.reader.files import FileReader
from service.workers.filmjob import pass_exposures
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
    ctx.phase(f"searching {len(frames)} frames for movers")
    result = await ctx.compute(search, frames, grid.step_arcsec, grid.n)
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
