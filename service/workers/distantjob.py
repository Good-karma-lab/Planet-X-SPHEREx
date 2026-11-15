# ruff: noqa: N806, N803, SIM108
# mypy: disable-error-code="no-untyped-call,no-any-return"
"""Find Planet X, one pass: the distance search (the "distance knob") over the visits of one pass of a field.

A body beyond Neptune slides across the sky by parallax (the telescope rides the Earth) plus its own slow motion; a hypothesis
(distance, own motion)
shifts the visit differences by the parallax and adds them. The job: visit images of the searched pass and of the other passes (the
static sky), the
scan over the hypotheses, candidates above the height noise alone reaches (the negated sum), PSF photometry along every candidate's
track minus the
same places in the other passes, control tracks one arcminute aside, and the arrays of the distance slider.

Product `distant`: meta.json = the result, part `bin` = the visit differences (uint8: 128 + 25·d/σ, 0 = no data; one image per visit, y
to the NORTH)
followed by the best-S/N map and the negated one (uint8: S/N × 255/12).
"""

from __future__ import annotations

import asyncio
import itertools
import math
import shutil
import tempfile
import time
from typing import Any

import numpy as np

from engine import distant as edist
from engine import orbit as eorbit
from indexer.store import Exposure, Index
from service import cpu
from service.fpx_known import leans_to_an_edge
from service.jobs.scheduler import Context
from service.products.store import Store
from service.reader.calib import WavelengthMaps
from service.reader.files import FileReader
from service.reader.psfs import PsfProvider
from service.workers import planetx as px

KIND = "distant"
MAX_PHOTOMETRY_CANDIDATES = 12
SEC_PER_HYPOTHESIS = 8e-4  # the scan on the service's threads: 3.6e5 hypotheses took 276 s
SEC_PER_EXPOSURE = 0.5
SEC_PER_HYPOTHESIS_CORE = 9.3e-4  # the scan in blocks on the worker pool: 829,443 hypotheses took 257 s on 3 workers (ε Indi B 20′, D5)
AFTER_SCAN_S = 60  # finding and tuning the candidates, the slider arrays, the photometry


def distant_params(
    ra: float, dec: float, fov: float, mjd: float | None, pass_: int | None, dmin: float, dmax: float, det: tuple[int, ...], mu_scale: float
) -> dict[str, Any]:
    p: dict[str, Any] = {
        "ra": round(ra % 360.0, 5),
        "dec": round(dec, 5),
        "fov": round(fov, 2),
        "dmin": round(dmin, 1),
        "dmax": round(dmax, 1),
        "det": list(det),
        "mu_scale": round(mu_scale, 2),
    }
    if mjd is not None:
        p["mjd"] = round(mjd, 1)
    elif pass_ is not None:
        p["pass"] = int(pass_)
    return p


def selection(index: Index, p: dict[str, Any]) -> tuple[list[Exposure], Any, int, list[dict[str, Any]], str | None]:
    """The capped exposures of the field, their pass numbers, the searched pass, the passes, and the note on the cap."""
    dets = tuple(p["det"])
    exps, pno, pk, info = px.pass_of_field(index, p["ra"], p["dec"], p["fov"], dets, p.get("mjd"), p.get("pass"))
    if not exps:
        return [], pno, pk, info, None
    exps, pno, note = px.cap_exposures_by_visits(exps, pno, pk)
    return exps, pno, pk, info, note


def signature(exps: list[Exposure]) -> str:
    import hashlib
    import json

    return hashlib.sha256(json.dumps(sorted(e.key for e in exps)).encode()).hexdigest()[:16]


def plan_input(index: Index, p: dict[str, Any]) -> dict[str, Any]:
    """What a plan needs from the exposure index (a query; no numpy work): the passes, the visit times of the searched pass, the count."""
    from engine.passes import split_visits

    exps, pno, pk, info, note = selection(index, p)
    out: dict[str, Any] = {"passes": info, "pass": pk, "exposures": len(exps), "note": note}
    if not exps:
        return {"early": {**out, "status": "no-coverage"}}
    if len(info) < 2:
        return {"early": {**out, "status": "one-pass"}}
    sel = [i for i in range(len(exps)) if pno[i] == pk]
    visits = split_visits([exps[i].mjd0 for i in sel], [exps[i].det for i in sel])
    tv = sorted(float(np.mean([exps[sel[a]].mjd0 for a in v])) for v in visits if len(v) >= px.MIN_VISIT_EXPOSURES)
    return {"out": out, "tv": tv, "n_exposures": len(exps)}


def plan_result(inp: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    """The hypotheses the search would scan and the time it takes (a worker process)."""
    if "early" in inp:
        early: dict[str, Any] = inp["early"]
        return early
    out, tv = inp["out"], np.array(inp["tv"])
    if len(tv) < 4:
        return {**out, "status": "few-visits", "visits": len(tv)}
    Rv = np.array([eorbit.earth(t) for t in tv])
    i0 = int(np.argmin(np.abs(tv - 0.5 * (tv.min() + tv.max()))))
    T = edist.Tangent(p["ra"], p["dec"])
    dmin = edist.fit_dmin(p["dmin"], p["dmax"], tv, Rv, i0, T, mu_scale=p["mu_scale"])
    if dmin is None:
        return {**out, "status": "too-many-hypotheses", "visits": len(tv)}
    nh = edist.estimate_hypotheses(dmin, p["dmax"], tv, Rv, i0, T, mu_scale=p["mu_scale"])
    est = int(nh * SEC_PER_HYPOTHESIS + SEC_PER_EXPOSURE * inp["n_exposures"] + 30)
    return {**out, "status": "plan", "visits": len(tv), "hypotheses": int(nh), "dmin_used": round(float(dmin), 1), "estimate_s": est}


def plan(index: Index, p: dict[str, Any]) -> dict[str, Any]:
    """A quick look without reading an exposure: the passes, the visits of the searched pass, the hypotheses the search would scan and
    the time it takes (in this process: for tools and tests; the service uses `plan_async`)."""
    return plan_result(plan_input(index, p), p)


async def plan_async(index: Index, p: dict[str, Any]) -> dict[str, Any]:
    """The same, the index query here and the numbers in a worker process."""
    result: dict[str, Any] = await cpu.run(plan_result, plan_input(index, p), p)
    if result.get("status") == "plan":
        # the scan's blocks run side by side on the pool: its part of the estimate is the core time over the workers, plus the finding
        # and tuning of the candidates after it
        scan = result["hypotheses"] * SEC_PER_HYPOTHESIS_CORE / max(1, cpu.workers())
        result["estimate_s"] = int(scan + SEC_PER_EXPOSURE * result.get("exposures", 0) + AFTER_SCAN_S)
    return result


async def track_photometry(
    ctx: Context,
    fr: FileReader,
    meter: px.PointMeter,
    exps: list[Exposure],
    pno: Any,
    pk: int,
    loaded: list[int],
    sc: dict[int, Any],
    tracks: list[dict[str, Any]],
    T: edist.Tangent,
    R0: Any,
    t0: float,
) -> dict[str, Any]:
    """PSF photometry along every track in the exposures of the pass minus the same sky places in the exposures of the other passes (the
    five nearest in
    wavelength, same detector) -> {"own": {candidate: stats}, "ctl": {candidate: [stats]}}."""
    mid = {i: 0.5 * (exps[i].mjd0 + exps[i].mjd1) for i in loaded}
    on_ids = [i for i in loaded if pno[i] == pk]
    ref_ids = [i for i in loaded if pno[i] != pk]
    pos: dict[tuple[int, int], tuple[float, float]] = {}
    for ti, tr in enumerate(tracks):
        for i in on_ids:
            Re = eorbit.earth(mid[i]) + np.asarray(sc[i], float)
            pu, pv = edist.parallax(tr["d"], Re[None], R0, T)
            a, d = T.uv2sky(tr["u0"] + float(pu[0]) + tr["mu"][0] * (mid[i] - t0), tr["v0"] + float(pv[0]) + tr["mu"][1] * (mid[i] - t0))
            pos[(ti, i)] = (float(a), float(d))
    by_det_on: dict[int, list[tuple[int, int]]] = {}
    for ti, i in pos:
        by_det_on.setdefault(exps[i].det, []).append((ti, i))
    on_m: dict[tuple[int, int], tuple[float, float, float]] = {}
    ref_m: dict[tuple[int, int], list[tuple[float, float, float]]] = {}
    sem = asyncio.Semaphore(12)
    done = 0
    ids_all = on_ids + ref_ids

    async def work(i: int) -> None:
        nonlocal done
        async with sem:
            if pno[i] == pk:
                targets = [(ti, i) for ti in range(len(tracks)) if (ti, i) in pos]
            else:
                targets = by_det_on.get(exps[i].det, [])
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
        ctx.phase(f"photometry of {len(tracks)} tracks: exposure {done} of {len(ids_all)}", done, len(ids_all))

    await asyncio.gather(*(work(i) for i in ids_all))
    await ctx.checkpoint()
    own: dict[int, dict[str, Any]] = {}
    ctl: dict[int, list[dict[str, Any]]] = {}
    for ti, tr in enumerate(tracks):
        rows: list[tuple[float, int, float, float, float]] = []
        for i in on_ids:
            m = on_m.get((ti, i))
            if m is None:
                continue
            off = px.off_median(ref_m.get((ti, i), []), m[0])
            if off is None:
                continue
            rows.append((mid[i], exps[i].det, m[0], m[1] - off, m[2]))
        rec = px.track_stats(rows)
        if tr["ctl"]:
            ctl.setdefault(tr["c"], []).append(rec)
        else:
            own[tr["c"]] = rec
    return {"own": own, "ctl": ctl}


def _f(x: Any, nd: int = 4) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


async def run_distant(
    ctx: Context, index: Index, fr: FileReader, store: Store, wl: WavelengthMaps | None, psfs: PsfProvider, params: dict[str, Any]
) -> dict[str, Any]:
    key = ctx.job.key
    store.begin(KIND, key, params)
    p = dict(params)
    res: dict[str, Any] = {"version": KIND, "notes": [], "status": "running", "params": params}

    def finish(status: str) -> dict[str, Any]:
        res["status"] = status
        res["honest"] = px.honest_words("distant", tuple(p["det"]))
        store.write_meta(KIND, key, res)
        store.complete(key)
        return {"status": status}

    ctx.phase("finding the exposures")
    exps, pno, pk, info, note = selection(index, p)
    res["passes"], res["pass"] = info, pk
    if note:
        res["notes"].append(note)
    if not exps:
        return finish("no-coverage")
    if len(info) < 2:
        res["notes"].append(
            "the field has one pass only: there is no static sky to subtract (the search needs at least two passes, three are better)"
        )
        return finish("one-pass")
    grid = px.grid_for(p["ra"], p["dec"], p["fov"])
    N = grid.n
    T = edist.Tangent(p["ra"], p["dec"])
    res["grid"] = {"ra0": p["ra"], "dec0": p["dec"], "n": N, "step": edist.S}
    pv = await px.build_visits(ctx, fr, wl, exps, pno, grid, "Find Planet X")
    if pv.failed:
        res["notes"].append(px.skipped_note(pv.failed, len(exps)))
    ctx.phase("static sky and differences")
    DV, notes = await ctx.compute(edist.static_and_differences, pv.vis, pk)
    res["notes"] += notes
    res["n_visits"], res["n_visits_all"] = len(DV), len(pv.vis)
    if len(DV) < 4:
        res["notes"].append(f"fewer than four usable visits in this pass ({len(DV)})")
        return finish("few-visits")
    res["masked_share"] = round(float(np.mean([v["masked"] for v in DV])), 3)
    tv = np.array([v["t"] for v in DV])
    Rv = np.array([v["R"] for v in DV])
    i0 = int(np.argmin(np.abs(tv - 0.5 * (tv.min() + tv.max()))))
    dmin_used = edist.fit_dmin(p["dmin"], p["dmax"], tv, Rv, i0, T, mu_scale=p["mu_scale"])
    if dmin_used is None:
        res["notes"].append("the pass is too long for a search of this distance range to fit: narrow it")
        return finish("too-many-hypotheses")
    res["dmin_asked"], res["dmin_used"] = p["dmin"], round(float(dmin_used), 1)
    if dmin_used > p["dmin"] * 1.001:
        res["notes"].append(
            f"the nearest distance was raised from {p['dmin']:g} to {dmin_used:.0f} au so that the search fits "
            f"(a pass of {float(np.ptp(tv)):.0f} days needs more hypotheses the nearer it looks)"
        )
    p["dmin"] = float(dmin_used)
    ctx.phase("scanning distance and own motion")

    try:
        R = await scan_blocks(ctx, DV, T, N, p["dmin"], p["dmax"], p["mu_scale"])
    except edist.TooManyHypotheses as e:
        res["notes"].append(f"about {e.args[0]} hypotheses (the limit is {edist.HMAX}): raise the nearest distance")
        return finish("too-many-hypotheses")
    await ctx.checkpoint()
    t0, R0 = float(R["tv"][R["i0"]]), R["Rv"][R["i0"]]
    res.update(
        n_hypotheses=len(R["H"]),
        scan_secs=round(R["secs"], 1),
        t0=round(t0, 4),
        span_days=round(float(np.ptp(R["tv"])), 2),
    )
    ctx.phase("finding and tuning the candidates")
    C, thr, null, untuned = await ctx.compute(scan_candidates, R, DV, N, T, R0, t0, p["mu_scale"])
    res.update(threshold=round(float(thr), 2), null=[round(q[2], 2) for q in null[:10]])
    res["notes"] += ["a candidate could not be tuned"] * untuned
    measured = C[:MAX_PHOTOMETRY_CANDIDATES]
    tracks: list[dict[str, Any]] = []
    for ci, c in enumerate(measured):
        for du, dv in ((0, 0), (60, 0), (-60, 0), (0, 60), (0, -60)):
            tracks.append(
                {
                    "c": ci,
                    "ctl": (du, dv) != (0, 0),
                    "u0": (c["x"] - N / 2) * edist.S + du,
                    "v0": (c["y"] - N / 2) * edist.S + dv,
                    "d": c["d"],
                    "mu": c["mu"],
                }
            )
    phot: dict[str, Any] = {}
    if tracks:
        meter = px.PointMeter(fr, psfs, wl)
        phot = await track_photometry(ctx, fr, meter, exps, pno, pk, pv.loaded, pv.sc, tracks, T, R0, t0)
    nctl = [0, 0]
    cands = []
    bright = await bright_fast_stars(ctx, T, N, exps)
    over_star = 0
    for ci, c in enumerate(C):
        own = (phot.get("own") or {}).get(ci, {})
        bydet = own.get("bydet", {})
        sn = own.get("sn")
        ok = bool(
            sn is not None
            and np.isfinite(sn)
            and sn >= edist.SN_MIN
            and all(f > 0 for f, n in bydet.values() if n >= 4)
            and c["path"] >= 12.0
            and not leans_to_an_edge(
                c["d_lo"], c["d_hi"], p["dmin"], p["dmax"]
            )  # a static source (the signal grows to an edge), not a lead
        )
        ctl = []
        for v in (phot.get("ctl") or {}).get(ci, []):
            if v.get("sn") is not None and np.isfinite(v["sn"]):
                ctl.append(round(float(v["sn"]), 1))
                nctl[0] += 1
                nctl[1] += bool(v["sn"] >= edist.SN_MIN)
        ra0, dec0 = T.uv2sky((c["x"] - N / 2) * edist.S, (c["y"] - N / 2) * edist.S)
        if any(math.hypot((ra0 - a) * math.cos(math.radians(dec0)), dec0 - d) * 3600.0 < STAR_TRACK_ARCSEC for a, d in bright):
            over_star += 1  # a saturated, fast star's own track leaves residuals along it: not a distant body
            continue
        ds, kn = c["knob"]
        cands.append(
            {
                "x": c["x"],
                "y": c["y"],
                "sn": round(c["sn"], 1),
                "d": round(c["d"], 1),
                "d_lo": round(c["d_lo"], 1),
                "d_hi": round(c["d_hi"], 1),
                # the knob (S/N against distance) is still high at the edge of the range searched: the distance is not bounded there
                "edge": leans_to_an_edge(c["d_lo"], c["d_hi"], p["dmin"], p["dmax"]),
                "mu": [round(c["mu"][0], 3), round(c["mu"][1], 3)],
                "ra": round(float(ra0), 6),
                "dec": round(float(dec0), 6),
                "path_arcsec": round(c["path"], 1),
                "flux_mjy": _f(own.get("F")),
                "err_mjy": _f(own.get("err")),
                "phot_sn": _f(sn, 1),
                "bydet": {str(k): [round(f, 4), n] for k, (f, n) in bydet.items()},
                "control_sn": ctl,
                "ok": ok,
                "kind": "mover" if c["path"] >= 12.0 else "static place brighter in this pass",
                "knob": {"d": [round(float(x), 1) for x in ds], "sn": [round(float(x), 2) for x in kn]},
            }
        )
    if over_star:
        res["notes"].append(f"{over_star} peak(s) stood over the own track of a bright, fast star and left out")
    res["candidates"] = cands
    res["candidates_measured"] = len(measured)
    res["control"] = {"measured": nctl[0], "reach_sn5": nctl[1]}
    ctx.phase("the arrays of the distance slider")
    res["slider"], blob = await ctx.compute(slider_arrays, R, DV, N, T, R0, t0, p["dmin"], p["dmax"], p["mu_scale"])
    store.write_part(KIND, key, "bin", blob)
    res["bin_url"] = f"/api/v1/distant/{key}/bin"
    return finish("ok")


STAR_TRACK_ARCSEC = 20.0  # a peak this close to a saturated fast star's place (at any visit) is that star's own residual


async def bright_fast_stars(ctx: Context, T: edist.Tangent, N: int, exps: list[Exposure]) -> list[tuple[float, float]]:
    """The places (ra, dec) of the bright, fast Gaia stars of the field at the first, middle and last exposure of the pass (a saturated
    mover leaves residuals along its own track in the differences; nothing to do with a body at 250-2000 au)."""
    from service.movers import _carried, gaia_fast_rows

    ra_c, dec_c = T.uv2sky(0.0, 0.0)
    radius = N * edist.S / 3600.0
    try:
        stars = await ctx.compute(gaia_fast_rows, float(ra_c), float(dec_c), radius * 0.8)
    except Exception:  # Gaia not reachable: no star is claimed
        return []
    mjds = sorted(e.mjd0 for e in exps)
    picks = [mjds[0], mjds[len(mjds) // 2], mjds[-1]] if mjds else []
    return [_carried(st, m) for st in stars for m in picks]


async def scan_blocks(
    ctx: Context, DV: list[dict[str, Any]], T: edist.Tangent, N: int, dmin: float, dmax: float, mu_scale: float
) -> dict[str, Any]:
    """The distance scan: the hypotheses are cut into blocks and every block is a call to a worker process, so the blocks run side by
    side on all the workers; the arrays are written once and read in place by every block. The progress and the time left follow the
    blocks done."""
    work = tempfile.mkdtemp(prefix="distant_")
    try:
        prep = await ctx.compute(edist.search_prepare, DV, T, N, dmin, dmax, work, edist.STEP, mu_scale, edist.HMAX, cpu.workers())
        cuts = prep["cuts"]
        blocks = [(int(a), int(b)) for a, b in itertools.pairwise(cuts) if b > a]
        t0 = time.monotonic()
        acc = edist.search_accumulator(prep)
        done = 0
        left0 = prep["est_s"] / max(1, min(cpu.workers(), len(blocks)))
        ctx.phase(f"scanning distance and own motion: {len(blocks)} blocks, about {left0:.0f} s", 0, len(blocks))

        async def one(a: int, b: int) -> None:
            await ctx.checkpoint()  # a background scan gives its workers back block by block while a person's job runs
            nonlocal done
            edist.search_fold(acc, await ctx.compute(edist.search_block, work, a, b, prep["wsum"]))  # folded in at once: never all held
            done += 1
            secs = time.monotonic() - t0
            left = secs / done * (len(blocks) - done)
            ctx.phase(
                f"scanning distance and own motion: block {done} of {len(blocks)} ({secs:.0f} s, about {left:.0f} s left)",
                done,
                len(blocks),
            )

        await asyncio.gather(*(one(a, b) for a, b in blocks))
        await ctx.checkpoint()
        merged: dict[str, Any] = edist.search_finish(prep, acc, time.monotonic() - t0)
        return merged
    finally:
        shutil.rmtree(work, ignore_errors=True)


MU_GATE = 5016.0  # the own motion a bound orbit can have at distance d is MU_GATE * mu_scale / d^1.5 arcsec/day (the slider's limit)


def inside_gate(mu: tuple[float, float], d: float, mu_scale: float) -> bool:
    """Whether an own motion (arcsec/day) is one a bound orbit at distance d (au) can have (a 5 % margin for the grid)."""
    return bool(math.hypot(*mu) <= 1.05 * MU_GATE * mu_scale / d**1.5)


def scan_candidates(
    R: dict[str, Any], DV: list[dict[str, Any]], N: int, T: edist.Tangent, R0: Any, t0: float, mu_scale: float = 1.0
) -> tuple[list[dict[str, Any]], float, list[Any], int]:
    """The candidates of a scan, tuned, with the distance range of each (the knob) and the length of its path (a worker process).
    -> (candidates, threshold, the negated sum's best, how many could not be tuned)."""
    C, thr, null = edist.candidates(R)
    untuned = 0
    for c in C:
        before = (c["x"], c["y"], c["mu"])
        try:
            edist.refine(c, R, DV, N)
        except Exception:  # a candidate that cannot be tuned keeps its grid place
            untuned += 1
        # the tuning moves the own motion freely: a result outside what a bound orbit of this distance can have is not a result (the gate
        # the slider shows), the grid hypothesis is kept
        if not inside_gate(c["mu"], c["d"], mu_scale):
            c["x"], c["y"], c["mu"] = before
            c.pop("refined", None)
            c.pop("sn_unclipped", None)
    for c in C:
        ds, kn = edist.knob(R, DV, c["x"], c["y"])
        inside = ds[kn >= kn.max() - 1.0]
        c["d_lo"], c["d_hi"] = float(inside.min()), float(inside.max())
        c["knob"] = (ds, kn)
        pu, pvv = edist.parallax(c["d"], R["Rv"], R0, T)
        uu, vv = pu + c["mu"][0] * (R["tv"] - t0), pvv + c["mu"][1] * (R["tv"] - t0)
        c["path"] = float(np.hypot(uu[-1] - uu[0], vv[-1] - vv[0]))
    return C, float(thr), null, untuned


def slider_arrays(
    R: dict[str, Any], DV: list[dict[str, Any]], N: int, T: edist.Tangent, R0: Any, t0: float, dmin: float, dmax: float, mu_scale: float
) -> tuple[dict[str, Any], bytes]:
    """The slider: the offsets by distance, the weights, and the binary part (the differences and the maps) (a worker process)."""
    dgrid = np.geomspace(dmin, dmax, 48)
    offs = []
    for d in dgrid:
        pu, pvv = edist.parallax(float(d), R["Rv"], R0, T)
        offs.append([[round(float(a) / edist.S, 2), round(float(b) / edist.S, 2)] for a, b in zip(pu, pvv, strict=True)])
    w = R["w"]
    slider = {
        "n": N,
        "step": edist.S,
        "distances": [round(float(d), 2) for d in dgrid],
        "offsets_px": offs,
        "dt": [round(float(x), 4) for x in (R["tv"] - t0)],
        "t": [round(float(x), 4) for x in R["tv"]],
        "weights": [round(float(x), 4) for x in (R["wa"] / np.sqrt(w.sum()))],
        "w2": [round(float(x), 4) for x in w],
        "mu_max": [round(MU_GATE * mu_scale / float(d) ** 1.5, 3) for d in dgrid],
        "detector": [int(v["k"]) + 1 for v in DV],
        "sg": [round(v["sg"], 4) for v in DV],
    }
    blob = bytearray()
    for v in DV:
        z = np.where(v["good"], np.clip(np.round(128 + 25 * v["d"] / max(v["sg"], 1e-9)), 2, 254), 0).astype(np.uint8)
        blob += z.tobytes()
    blob += np.clip(np.round(R["best"] * 255 / 12.0), 0, 255).astype(np.uint8).tobytes()
    blob += np.clip(np.round(R["neg"] * 255 / 12.0), 0, 255).astype(np.uint8).tobytes()
    return slider, bytes(blob)


def estimate_text(p: dict[str, Any]) -> str:
    return f"about {math.ceil(p.get('estimate_s', 0) / 60)} min"
