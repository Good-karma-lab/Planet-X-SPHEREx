"""Build the passes of a field from the archive (the computation behind /fields; the job wrapper comes in M4).

Exposures that overlap the field come from the index (at most 800, thinned evenly keeping every pass and detector); passes and visits
are split by time; each pass is read (exposures concurrently), built and registered on pass 0.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from concurrent.futures import Executor
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from engine.bands import Band, load_bands
from engine.field import NativeExposure, PassProducts, build_detector, pick_spread, quick_per_detector, reduce_pass
from engine.grid import Grid, grid_for_fov
from engine.passes import common_detectors, detectors_per_pass, seasons_with_common_detectors, split_passes, split_visits
from engine.register import register, stars
from indexer.store import Exposure, Index
from service import cpu
from service.reader.calib import WavelengthMaps
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
    natives: dict[int, list[NativeExposure]] = field(default_factory=dict)  # per pass, only with keep_natives


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
    keep_natives: bool = False,
    checkpoint: Callable[[], Awaitable[None]] | None = None,
    on_pass: Callable[[PassProducts, tuple[int, ...]], Awaitable[None]] | None = None,
    on_quick: Callable[[PassProducts, tuple[int, ...]], Awaitable[None]] | None = None,
    wavelengths: WavelengthMaps | None = None,
) -> FieldBuild:
    t0 = time.time()
    grid = grid_for_fov(ra, dec, fov_arcmin)
    radius = fov_arcmin / 60.0 / 2.0 * 2**0.5
    found = index.overlapping(ra, dec, radius)
    exps = thin(found)
    bands = bands if bands is not None else load_bands()
    passes = seasons_with_common_detectors(split_passes([e.mjd0 for e in exps]), [e.det for e in exps])
    visits_all = split_visits([e.mjd0 for e in exps], [e.det for e in exps])
    visit_of = {i: k for k, v in enumerate(visits_all) for i in v}
    natives: dict[int, NativeExposure] = {}
    missing: list[str] = []
    t_read = 0.0

    async def one(i: int) -> None:
        try:
            ne = await read_native(fr, exps[i].key, grid, visit_of[i], wavelengths=wavelengths)
        except Exception as err:  # a file that cannot be read is counted and said, never hidden
            missing.append(f"{exps[i].key}: {err}")
            return
        if ne is not None and ne.val.size > 0:
            natives[i] = ne

    built: list[PassProducts] = []
    counts = detectors_per_pass(passes, [e.det for e in exps])
    cdet = common_detectors(counts)
    kept: dict[int, list[NativeExposure]] = {}
    tc_total = 0.0
    if on_quick is not None:
        # the quick part: a few exposures per pass and detector, built and published before the full build (white light only)
        per, sig_q = quick_per_detector(fov_arcmin)
        for p in passes:
            groups_q: dict[int, list[int]] = defaultdict(list)
            for i in p.members:
                groups_q[exps[i].det].append(i)
            chosen = [g[t] for g in groups_q.values() for t in pick_spread([exps[i].mjd0 for i in g], per)]
            ts = time.time()
            await asyncio.gather(*(one(i) for i in chosen))
            t_read += time.time() - ts
            parts_q = []
            for det in sorted(groups_q):
                group = [natives[i] for i in chosen if i in natives and exps[i].det == det]
                if group:
                    if executor is not None:
                        parts_q.append(await cpu.run(build_detector, group, grid, [], True, sig_q))
                    else:
                        parts_q.append(build_detector(group, grid, [], True, sig_q))
            for i in chosen:
                natives.pop(i, None)
            if parts_q:
                await on_quick(await cpu.run(reduce_pass, p.index, parts_q, grid, cdet), cdet)
            if progress:
                progress(f"quick picture of pass {p.index + 1} of {len(passes)}")
    ref_stars: Any = None
    for p in passes:
        groups: dict[int, list[int]] = defaultdict(list)
        for i in p.members:
            groups[exps[i].det].append(i)
        parts = []
        dets = sorted(groups)
        # reading overlaps computing: the next detector's exposures are read while this one is stacked
        reads: dict[int, asyncio.Task[Any]] = {}

        def start_read(d: int, reads: dict[int, asyncio.Task[Any]] = reads, groups: dict[int, list[int]] = groups) -> None:
            if d not in reads:
                reads[d] = asyncio.ensure_future(asyncio.gather(*(one(i) for i in groups[d])))

        if dets:
            start_read(dets[0])
        for k, det in enumerate(dets):
            if checkpoint is not None:
                await checkpoint()
            ts = time.time()
            await reads.pop(det)
            t_read += time.time() - ts
            if k + 1 < len(dets):
                start_read(dets[k + 1])
            group = [natives.pop(i) for i in groups[det] if i in natives]  # pop: only this detector's exposures stay in memory
            if not group:
                continue
            if keep_natives:
                kept.setdefault(p.index, []).extend(group)
            tc = time.time()
            if executor is not None:
                parts.append(await cpu.run(build_detector, group, grid, bands))
            else:
                parts.append(build_detector(group, grid, bands))
            tc_total += time.time() - tc
            del group
            if progress:
                progress(f"pass {p.index + 1} of {len(passes)}: D{det} stacked")
        if parts:
            if built and ref_stars is None:
                ref_stars = await cpu.run(_stars_of, built[0], cdet, grid.step_arcsec)
            # register on pass 0 as soon as the pass exists, so it can be published at once
            pp = await cpu.run(_reduce_registered, p.index, parts, grid, cdet, ref_stars if built else None)
            built.append(pp)
            if on_pass is not None:
                await on_pass(pp, cdet)
    return FieldBuild(
        grid=grid,
        exposures=exps,
        passes=built,
        cdet=cdet,
        thinned_from=len(found),
        missing=missing,
        timings={"read_s": t_read, "compute_s": tc_total, "total_s": time.time() - t0},
        natives=kept,
    )


def _stars_of(pp: PassProducts, cdet: tuple[int, ...], step_arcsec: float) -> Any:
    return stars(pp.white(cdet), pp.sat_share(cdet), step_arcsec)


def _reduce_registered(index: int, parts: list[Any], grid: Grid, cdet: tuple[int, ...], ref: Any) -> PassProducts:
    """One pass from its detector stacks, registered on pass 0's stars (runs in a worker process)."""
    pp = reduce_pass(index, parts, grid, cdet)
    if ref is not None:
        pp.register(register(ref, stars(pp.white(cdet), pp.sat_share(cdet), grid.step_arcsec), grid.step_arcsec))
    return pp
