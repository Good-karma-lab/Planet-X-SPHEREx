"""Build the passes of a field from the archive (the computation behind /fields; the job wrapper comes in M4).

Exposures that overlap the field come from the index (at most 800, thinned evenly keeping every pass and detector); passes and visits
are split by time; each pass is read (exposures concurrently), built and registered on pass 0.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import Executor
from dataclasses import dataclass, field

import numpy as np

from engine.bands import Band, load_bands
from engine.field import NativeExposure, PassProducts, build_pass
from engine.grid import Grid, grid_for_fov
from engine.passes import common_detectors, detectors_per_pass, split_passes, split_visits
from engine.register import register, stars
from indexer.store import Exposure, Index
from service.reader.files import FileReader
from service.workers.native import read_native

MAX_EXPOSURES = 800


@dataclass
class FieldBuild:
    grid: Grid
    exposures: list[Exposure]
    passes: list[PassProducts]
    cdet: tuple[int, ...]
    thinned_from: int
    missing: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)


def thin(exps: list[Exposure], limit: int = MAX_EXPOSURES) -> list[Exposure]:
    """Even thinning that keeps every (pass, detector) represented."""
    if len(exps) <= limit:
        return exps
    passes = split_passes([e.mjd0 for e in exps])
    groups: dict[tuple[int, int], list[int]] = defaultdict(list)
    for p in passes:
        for i in p.members:
            groups[(p.index, exps[i].det)].append(i)
    frac = limit / len(exps)
    keep: list[int] = []
    for idx in groups.values():
        k = max(1, round(len(idx) * frac))
        keep += [idx[j] for j in np.unique(np.linspace(0, len(idx) - 1, k).round().astype(int))]
    return [exps[i] for i in sorted(keep, key=lambda i: exps[i].mjd0)]


async def build_field(
    index: Index,
    fr: FileReader,
    ra: float,
    dec: float,
    fov_arcmin: float,
    executor: Executor | None = None,
    bands: list[Band] | None = None,
    progress: Callable[[str], None] | None = None,
) -> FieldBuild:
    t0 = time.time()
    grid = grid_for_fov(ra, dec, fov_arcmin)
    radius = fov_arcmin / 60.0 / 2.0 * 2**0.5
    found = index.overlapping(ra, dec, radius)
    exps = thin(found)
    bands = bands if bands is not None else load_bands()
    passes = split_passes([e.mjd0 for e in exps])
    visits_all = split_visits([e.mjd0 for e in exps], [e.det for e in exps])
    visit_of = {i: k for k, v in enumerate(visits_all) for i in v}
    loop = asyncio.get_running_loop()
    natives: dict[int, NativeExposure] = {}
    missing: list[str] = []
    t_read = 0.0

    async def one(i: int) -> None:
        try:
            ne = await read_native(fr, exps[i].key, grid, visit_of[i])
        except Exception as err:  # a file that cannot be read is counted and said, never hidden
            missing.append(f"{exps[i].key}: {err}")
            return
        if ne is not None and ne.val.size > 0:
            natives[i] = ne

    built: list[PassProducts] = []
    member_lists: list[list[int]] = []
    for p in passes:
        ts = time.time()
        await asyncio.gather(*(one(i) for i in p.members))
        t_read += time.time() - ts
        members = [i for i in p.members if i in natives]
        member_lists.append(members)
        if progress:
            progress(f"pass {p.index + 1} of {len(passes)}: read {len(members)} of {len(p.members)}")
    counts = [{d: n for d, n in c.items()} for c in detectors_per_pass(passes, [e.det for e in exps])]
    present: list[defaultdict[int, int]] = [defaultdict(int) for _ in passes]
    for k, members in enumerate(member_lists):
        for i in members:
            present[k][natives[i].det] += 1
    cdet = common_detectors([dict(c) for c in present]) or common_detectors(counts)
    tc = time.time()
    for p, members in zip(passes, member_lists, strict=True):
        ex = [natives[i] for i in members]
        if not ex:
            continue
        if executor is not None:
            pp = await loop.run_in_executor(executor, build_pass, p.index, ex, grid, bands, cdet)
        else:
            pp = build_pass(p.index, ex, grid, bands, cdet)
        built.append(pp)
        if progress:
            progress(f"pass {p.index + 1} of {len(passes)}: stacked")
    if built:
        ref = stars(built[0].white(cdet), built[0].sat_share(cdet), grid.step_arcsec)
        for pp in built[1:]:
            pp.register(register(ref, stars(pp.white(cdet), pp.sat_share(cdet), grid.step_arcsec), grid.step_arcsec))
    return FieldBuild(
        grid=grid,
        exposures=exps,
        passes=built,
        cdet=cdet,
        thinned_from=len(found),
        missing=missing,
        timings={"read_s": t_read, "compute_s": time.time() - tc, "total_s": time.time() - t0},
    )
