# ruff: noqa: N806
# mypy: disable-error-code="no-untyped-call,no-any-return"
"""Pieces shared by the Find Planet X jobs (distance search of one pass, deep sum of several passes, link, jump).

  exposures   the D1–D3 exposures of a field, its passes (the film's rule), the searched pass, and the cap by whole visits (near the
  ecliptic poles a
              field holds 18 000 exposures in six passes: at most 1400 are read, the searched pass up to 800)
  visits      the visit images of every detector and pass on a 3.075″ grid (the exposures of a visit smoothed to a point source and
  combined by their
              median), with the telescope's place at the visit (the Earth from ERFA plus the spacecraft from the file header)
  photometry  PSF photometry at given sky places in an exposure (the same fit as the spectrum), for on − off along a track
Orientation: the search engine (engine/distant.py) works with y to the NORTH and x to the WEST; the film's grid has row 0 north, so the
frames are
flipped once when the visit images are made.
"""

from __future__ import annotations

import asyncio
import functools
import json
import math
import os
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from engine import orbit as eorbit
from engine import photometry
from engine.distant import Tangent
from engine.film import visit_frame
from engine.grid import Grid
from engine.passes import split_passes, split_visits
from engine.spx import flags as spxflags
from engine.spx import psf as spxpsf
from engine.spx import wcs as spxwcs
from indexer.store import Exposure, Index
from service.jobs.scheduler import Context
from service.reader.calib import WavelengthMaps
from service.reader.files import FileReader
from service.reader.psfs import PsfProvider
from service.workers.native import read_native

DETECTORS = (1, 2, 3)
STEP_ARCSEC = 3.075
MAX_EXPOSURES = 1400
PASS_EXPOSURES = 800
MIN_VISIT_EXPOSURES = 3
HALF = photometry.HALF
OMEGA_SR = (6.15 / 206264.806) ** 2


def honest_words(kind: str, det: tuple[int, ...] = (1, 2, 3)) -> dict[str, Any]:
    """What the page must say next to a result of Find Planet X, with the numbers it rests on (measured on artificial bodies and on
    Eris, Sedna and
    Makemake; not re-measured by the job that carries them). kind: distant (one pass), deep (sum of passes), link, jump."""
    d4 = 4 in det
    numbers = {
        "blind_limit_mjy": {
            "one_pass_d1_d3": [0.045, 0.065],
            "sum_of_passes_d1_d3": 0.034,
            "d4_clean_sky": 0.039,
            "d4_near_ecliptic": 0.047,
            "d4_planet_nine_prior": 0.042,
            "wise_planet_nine_search": 0.065,
        },
        "blind_limit_v_reflected_light": [20.0, 21.0],
        "sedna_v": 20.8,
        "planet_nine_margin_over_wise": [1.4, 1.7],
    }
    by_kind = {
        "distant": "0.045-0.065 mJy for one pass",
        "deep": "0.034 mJy for the sum of passes (Sedna, V 20.8, was found at 0.031 mJy)",
        "link": "the limit of the passes that were linked",
        "jump": "the limit of the pass that is searched",
    }
    return {
        "limit": "One blind search reaches about V 20-21 in reflected light (a flux of 0.034-0.065 mJy at 3 µm in D1-D3: "
        f"{by_kind.get(kind, 'see the job')}). A body fainter than that is not found, and a search that finds nothing does not show that "
        "nothing is there.",
        "planet_nine": "The predicted Planet Nine is fainter than that limit in D1-D3. The one place SPHEREx adds to the existing "
        "Planet Nine searches is 3 µm (D4, 2.4-3.8 µm), where the blind limit is 1.4-1.7 times deeper than the WISE search "
        "(0.039-0.047 mJy against 0.065 mJy, measured on artificial bodies in 1 degree fields at 250-2000 au): the margin is small."
        + ("" if d4 else " This job did not use D4."),
        "lead": "A candidate is a lead, not a discovery: it needs the link of two or three passes, the check against the known bodies "
        "(SkyBoT, JPL) and, for a body seen in D4, the archive check.",
        "numbers": numbers,
    }


def grid_for(ra: float, dec: float, fov: float) -> Grid:
    """The grid of the search: n even, its centre half a grid step east and south of the field centre, so that the engine's index convention
    (the field centre at index n/2, u = (x − n/2)·step to the west, v = (y − n/2)·step to the north) holds for the images' pixels."""
    n = math.ceil(fov * 60.0 / STEP_ARCSEC / 2.0) * 2
    a, d = Tangent(ra, dec).uv2sky(-0.5 * STEP_ARCSEC, -0.5 * STEP_ARCSEC)
    return Grid(float(a), float(d), STEP_ARCSEC, n)


def pass_of_field(
    index: Index, ra: float, dec: float, fov: float, dets: tuple[int, ...], mjd: float | None, pass_: int | None
) -> tuple[list[Exposure], NDArray[np.int64], int, list[dict[str, Any]]]:
    """The exposures of the field (detectors `dets`) in time order, the pass number of each, the searched pass (the one nearest `mjd`,
    or the
    numbered one, or the one with the most exposures) and the list of passes."""
    found = [e for e in index.overlapping(ra, dec, fov / 60.0 / 2.0 * 2**0.5) if e.det in dets]
    found.sort(key=lambda e: e.mjd0)
    if not found:
        return [], np.zeros(0, np.int64), -1, []
    passes = split_passes([e.mjd0 for e in found])
    pno = np.zeros(len(found), np.int64)
    for p in passes:
        pno[list(p.members)] = p.index
    info = [{"index": p.index, "n": len(p.members), "mjd0": round(p.mjd0, 3), "mjd1": round(p.mjd1, 3)} for p in passes]
    if mjd is not None:
        pk = min(passes, key=lambda p: max(p.mjd0 - mjd, mjd - p.mjd1, 0.0)).index
    elif pass_ is not None and 0 <= pass_ < len(passes):
        pk = pass_
    else:
        pk = max(passes, key=lambda p: len(p.members)).index
    return found, pno, pk, info


def cap_exposures_by_visits(
    exps: list[Exposure], pno: NDArray[np.int64], pk: int, total: int = MAX_EXPOSURES, selected: int = PASS_EXPOSURES
) -> tuple[list[Exposure], NDArray[np.int64], str | None]:
    """A field with too many exposures keeps WHOLE visits at an even stride, for every detector and pass (the median of the exposures of
    a visit
    needs them together): the searched pass up to `selected` exposures, the other passes share the rest.
    -> (exposures, pass numbers, note)."""
    n = len(exps)
    if n <= total:
        return exps, pno, None
    npass = int(pno.max()) + 1
    cnt = np.array([(pno == q).sum() for q in range(npass)], float)
    quota = {q: (min(selected, int(cnt[q])) if q == pk else 0) for q in range(npass)}
    others = [q for q in range(npass) if q != pk]
    rest = total - quota[pk]
    tot_o = max(float(cnt[others].sum()), 1.0)
    for q in others:
        quota[q] = int(min(cnt[q], max(40.0, rest * cnt[q] / tot_o)))
    keep: list[int] = []
    for q in range(npass):
        for k in sorted({e.det for e, qq in zip(exps, pno, strict=True) if qq == q}):
            ids = [i for i in range(n) if pno[i] == q and exps[i].det == k]
            share = max(3, round(quota[q] * len(ids) / max(float(cnt[q]), 1.0)))
            if len(ids) <= share:
                keep += ids
                continue
            groups = split_visits([exps[i].mjd0 for i in ids], [k] * len(ids))
            nvis = max(1, round(share / max(len(ids) / float(len(groups)), 1.0)))
            take = sorted({int(x) for x in np.linspace(0, len(groups) - 1, min(nvis, len(groups)))})
            for gi in take:
                keep += [ids[a] for a in groups[gi]]
    keep = sorted(set(keep))
    in_pass = int((pno[keep] == pk).sum())
    note = f"the field holds {n} exposures: {len(keep)} were used (whole visits at an even stride, {in_pass} of them in the searched pass)"
    return [exps[i] for i in keep], pno[keep], note


class _FileCancel:
    """The cancel flag of a job as seen from a worker process: a file the job creates."""

    def __init__(self, path: str) -> None:
        self.path = path

    def is_set(self) -> bool:
        return os.path.exists(self.path)


def _tracked_call(fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any], progress_path: str, cancel_path: str) -> Any:
    """Runs `fn(*args, progress=..., cancel=..., **kwargs)` in a worker process: progress is written to a file, cancel is a file."""

    last = [0.0]

    def progress(*vals: Any) -> None:
        now = time.monotonic()
        if now - last[0] < 0.5:
            return
        last[0] = now
        tmp = progress_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump([float(v) for v in vals], f)
        os.replace(tmp, progress_path)

    return fn(*args, progress=progress, cancel=_FileCancel(cancel_path), **kwargs)


async def tracked(ctx: Context, fn: Callable[..., Any], *args: Any, on_progress: Callable[..., None] | None = None, **kwargs: Any) -> Any:
    """`await ctx.compute(fn, ...)` for a long call that reports progress and can be cancelled: `fn` (a module-level function) takes
    `progress` (called with numbers) and `cancel` (an object with is_set()) as keywords; the progress arrives here every 2 s as
    `on_progress(*numbers)`, and a cancelled job stops the call at its next check."""
    work = tempfile.mkdtemp(prefix="px_")
    progress_path, cancel_path = os.path.join(work, "progress.json"), os.path.join(work, "cancel")
    fut = asyncio.ensure_future(ctx.compute(functools.partial(_tracked_call, fn, args, kwargs, progress_path, cancel_path)))
    try:
        while not fut.done():
            await asyncio.wait({fut}, timeout=2.0)
            if ctx.job._cancel and not os.path.exists(cancel_path):
                open(cancel_path, "w").close()
            if on_progress is not None and not fut.done():
                try:
                    with open(progress_path) as f:
                        vals = json.load(f)
                except (OSError, ValueError):
                    continue
                on_progress(*vals)
        return fut.result()
    finally:
        fut.cancel()
        shutil.rmtree(work, ignore_errors=True)


class CancelFlag:
    """The scheduler's cancel flag as an object with is_set(), for the engine's loops."""

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx

    def is_set(self) -> bool:
        return bool(self.ctx.job._cancel)


async def spacecraft_of(fr: FileReader, key: str) -> NDArray[np.float64] | None:
    """The spacecraft's place from the Earth's centre (km) at the exposure, from the file's header; None when the header does not have
    it."""
    lay = await fr.layout(key)
    h = lay.image_header
    try:
        v = np.array([float(h["X_SC"]), float(h["Y_SC"]), float(h["Z_SC"])])
    except (KeyError, ValueError, TypeError):
        return None
    return v if np.all(np.isfinite(v)) else None


@dataclass
class PassVisits:
    """What the visit images of a field are made from: the exposures that were read (index into the capped list) and their spacecraft
    places."""

    vis: list[dict[str, Any]]
    loaded: list[int]
    sc: dict[int, NDArray[np.float64]]
    failed: int


async def build_visits(
    ctx: Context,
    fr: FileReader,
    wl: WavelengthMaps | None,
    exps: list[Exposure],
    pno: NDArray[np.int64],
    grid: Grid,
    label: str,
) -> PassVisits:
    """The visit images of every detector and pass of a field (see the module text), with progress."""
    visits = split_visits([e.mjd0 for e in exps], [e.det for e in exps])
    done = 0
    failed = 0
    loaded: list[int] = []
    sc_of: dict[int, NDArray[np.float64]] = {}
    vis: list[dict[str, Any]] = []
    sem = asyncio.Semaphore(24)
    total = len(exps)

    async def one_visit(vi: int, members: tuple[int, ...]) -> None:
        nonlocal done, failed
        natives = []
        scs = []
        got = []

        async def one(i: int) -> None:
            nonlocal done, failed
            async with sem:
                try:
                    ne = await read_native(fr, exps[i].key, grid, vi, keep_outlier=True, wavelengths=wl)
                    sc = await spacecraft_of(fr, exps[i].key)
                except Exception:  # an unreadable exposure is counted, never hidden
                    ne, sc = None, None
            done += 1
            ctx.phase(f"{label}: reading the exposures, {done} of {total}", done, total)
            if ne is None or not ne.val.size or sc is None:
                failed += 1
                return
            natives.append(ne)
            scs.append(sc)
            got.append(i)

        await asyncio.gather(*(one(i) for i in members))
        if len(natives) >= MIN_VISIT_EXPOSURES:
            med, cov = await ctx.compute(visit_frame, natives, grid)
            if cov > 0.2:
                t, R = eorbit.observer([ne.mjd for ne in natives], np.array(scs))
                i0 = got[0]
                vis.append(
                    {"k": exps[i0].det - 1, "p": int(pno[i0]), "t": t, "R": R, "img": np.ascontiguousarray(med[::-1]), "n": len(natives)}
                )
        for i, ne_sc in zip(got, scs, strict=True):
            loaded.append(i)
            sc_of[i] = ne_sc

    await asyncio.gather(*(one_visit(vi, m) for vi, m in enumerate(visits)))
    await ctx.checkpoint()
    vis.sort(key=lambda v: v["t"])
    return PassVisits(vis, sorted(loaded), sc_of, failed)


class PointMeter:
    """PSF photometry at sky places in an exposure: flux and error (mJy) and the wavelength, or None where the place cannot be measured."""

    def __init__(self, fr: FileReader, psfs: PsfProvider, wl: WavelengthMaps | None) -> None:
        self.fr, self.psfs, self.wl = fr, psfs, wl

    async def measure(self, key: str, ras: NDArray[np.float64], decs: NDArray[np.float64]) -> list[tuple[float, float, float] | None]:
        """-> per place (lam µm, flux mJy, error mJy) or None. A place is measured in a 7 × 7 stamp with a flat noise (the robust
        scatter of the
        exposure's rows around it); OUTLIER pixels are kept (the pipeline puts that flag on moving bodies)."""
        n = len(ras)
        out: list[tuple[float, float, float] | None] = [None] * n
        lay = await self.fr.layout(key)
        w = self.fr.wcs_of(key, lay)
        px, py = spxwcs.sky2pix(w, ras, decs)
        nx, ny = lay.image.nx, lay.image.ny
        ok = np.isfinite(px) & np.isfinite(py) & (px >= HALF) & (py >= HALF) & (px <= nx - 1 - HALF) & (py <= ny - 1 - HALF)
        if not ok.any():
            return out
        y0 = int(np.floor(py[ok].min())) - HALF - 1
        y1 = int(np.ceil(py[ok].max())) + HALF + 2
        y0, y1 = max(0, y0), min(ny, y1)
        img = (await self.fr.rows(key, "image", y0, y1)).astype(np.float64)
        flg = (await self.fr.rows(key, "flags", y0, y1)).astype(np.int64)
        table, _, _ = spxflags.resolve(lay.flags_header)
        bad_names = [m for m in spxflags.BAD_ALWAYS if m not in spxflags.MOVER_KEEP]
        bad = (flg & spxflags.mask_of(table, bad_names)) != 0
        overflow = (flg & spxflags.mask_of(table, spxflags.SATURATION)) != 0
        good = np.isfinite(img) & ~bad & ~overflow
        if good.sum() < 100:
            return out
        sg = 1.4826 * float(np.median(np.abs(img[good] - np.median(img[good]))))
        var = np.full((2 * HALF + 1, 2 * HALF + 1), max(sg, 1e-6) ** 2)
        ps = await self.psfs.for_file(key)
        cw = None
        if self.wl is not None:
            try:
                cw, _ = await self.wl.get(lay.detector)
            except Exception:  # the calibration map could not be read: such places are left out
                cw = None
        for k in np.flatnonzero(ok):
            x, y = float(px[k]), float(py[k])
            xi, yi = round(x), round(y)
            sl = (slice(yi - HALF - y0, yi + HALF + 1 - y0), slice(xi - HALF, xi + HALF + 1))
            st = img[sl]
            if st.shape != (2 * HALF + 1, 2 * HALF + 1):
                continue
            usable = good[sl]
            saturated = overflow[sl] | (~np.isfinite(st) & ~bad[sl])
            fx, fy = x - xi, y - yi
            psf = spxpsf.stamp(ps, ps.zone_index(x, y), fx, fy, HALF)
            pt = photometry.fit(st, var, usable, saturated, psf, fx, fy, OMEGA_SR)
            if not pt.ok or pt.flux_mjy is None or pt.err_mjy is None or cw is None:
                continue
            lam = float(cw[yi, xi])
            if np.isfinite(lam) and lam > 0 and np.isfinite(pt.flux_mjy):
                out[k] = (lam, pt.flux_mjy, pt.err_mjy)
        return out


def track_stats(rows: list[tuple[float, int, float, float, float]]) -> dict[str, Any]:
    """The measurements (mjd, detector, wavelength, flux on − off, error) along one track -> F, err, sn (flux in mJy) and the flux by
    detector."""
    rec: dict[str, Any] = {"F": None, "err": None, "sn": float("nan"), "bydet": {}}
    if len(rows) < 6:
        return rec
    a = np.array(rows)
    w = 1 / a[:, 4] ** 2
    f = float((w * a[:, 3]).sum() / w.sum())
    chi = float(np.sqrt(np.mean(((a[:, 3] - f) / a[:, 4]) ** 2)))
    err = float(max(chi, 1.0) / np.sqrt(w.sum()))
    bydet = {
        int(k): (float(np.average(a[a[:, 1] == k, 3], weights=1 / a[a[:, 1] == k, 4] ** 2)), int((a[:, 1] == k).sum()))
        for k in np.unique(a[:, 1])
    }
    return {"F": f, "err": err, "sn": f / err, "bydet": bydet}


def off_median(refs: list[tuple[float, float, float]], lam: float, n: int = 5) -> float | None:
    """The sky flux at a place in the other passes: the median of the `n` exposures nearest in wavelength (at least three)."""
    if len(refs) < 3:
        return None
    near = sorted(refs, key=lambda r: abs(r[0] - lam))[:n]
    return float(np.median([r[1] for r in near]))


async def on_off_tracks(
    ctx: Context,
    meter: PointMeter,
    exps: list[Exposure],
    pno: NDArray[np.int64],
    pk: int,
    loaded: list[int],
    pos: dict[tuple[int, int], tuple[float, float]],
    ntracks: int,
    label: str,
) -> list[dict[str, Any]]:
    """PSF photometry along tracks: `pos[(track, exposure)]` is the sky place (ra, dec) of the track in an exposure of the searched pass
    (the exposures of `loaded`). Every place is measured in its own exposure (on) and in the exposures of the other passes of the same
    detector (off: the median of the five nearest in wavelength, at least three); the flux is on − off. -> one `track_stats` per track."""
    mid = {i: 0.5 * (exps[i].mjd0 + exps[i].mjd1) for i in loaded}
    on_ids = [i for i in loaded if pno[i] == pk]
    ref_ids = [i for i in loaded if pno[i] != pk]
    by_det_on: dict[int, list[tuple[int, int]]] = {}
    for ti, i in pos:
        by_det_on.setdefault(exps[i].det, []).append((ti, i))
    on_m: dict[tuple[int, int], tuple[float, float, float]] = {}
    ref_m: dict[tuple[int, int], list[tuple[float, float, float]]] = {}
    sem = asyncio.Semaphore(12)
    ids_all = on_ids + ref_ids
    done = 0

    async def work(i: int) -> None:
        nonlocal done
        async with sem:
            targets = [(ti, i) for ti in range(ntracks) if (ti, i) in pos] if pno[i] == pk else by_det_on.get(exps[i].det, [])
            if targets:
                ras = np.array([pos[t][0] for t in targets])
                decs = np.array([pos[t][1] for t in targets])
                try:
                    got = await meter.measure(exps[i].key, ras, decs)
                except Exception:  # an unreadable exposure is one missing point, never a failed job
                    got = [None] * len(targets)
                for t, g in zip(targets, got, strict=True):
                    if g is None:
                        continue
                    if pno[i] == pk:
                        on_m[t] = g
                    else:
                        ref_m.setdefault(t, []).append(g)
        done += 1
        ctx.phase(f"{label}: exposure {done} of {len(ids_all)}", done, len(ids_all))

    await asyncio.gather(*(work(i) for i in ids_all))
    await ctx.checkpoint()
    out: list[dict[str, Any]] = []
    for ti in range(ntracks):
        rows: list[tuple[float, int, float, float, float]] = []
        for i in on_ids:
            m = on_m.get((ti, i))
            if m is None:
                continue
            off = off_median(ref_m.get((ti, i), []), m[0])
            if off is not None:
                rows.append((mid[i], exps[i].det, m[0], m[1] - off, m[2]))
        out.append(track_stats(rows))
    return out
