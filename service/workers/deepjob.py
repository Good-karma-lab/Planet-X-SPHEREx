# ruff: noqa: N806, N803
# mypy: disable-error-code="no-untyped-call,no-any-return"
"""Find Planet X, the sum of passes: the visits of several passes added along ONE heliocentric orbit.

One pass has a noise ceiling; a body under the ceiling of every pass can rise above the ceiling of the SUM of passes (the two weeks of a
pass are
added along the orbit, then the passes are added with the parallax between them). Every pass has a field of its own (a body 80 au away
moves about a
degree between passes by parallax): `fields` = ra,dec,mjd for each pass names the field centre and the middle date of the pass.

The job: the visit differences of every field (as in the one-pass search), the geometry, the scan over (distance, own motion) in worker
PROCESSES
(engine/deepsum.py: a coarse cell of own motion fixes the track shape inside every pass, a fine step moves the passes against each other),
the peaks of the sum and
of the NEGATED sum (the negated peaks set the threshold), the tuning of the peaks on the visit images, and PSF photometry along every
orbit in every pass
minus the same places in the other passes (on − off), with control tracks one arcminute aside for the confirmed ones.

Product `deep`: meta.json = the result.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import multiprocessing as mp
import os
import shutil
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np

from engine import deepsum as edeep
from engine import distant as edist
from engine import orbit as eorbit
from engine.passes import split_visits
from indexer.store import Exposure, Index
from service.jobs.scheduler import Context
from service.products.store import Store
from service.reader.calib import WavelengthMaps
from service.reader.files import FileReader
from service.reader.psfs import PsfProvider
from service.workers import distantjob
from service.workers import planetx as px

KIND = "deep"
HYPOTHESES_MAX = 40_000_000
SEC_PER_HYPOTHESIS = 3.5e-4  # whole job on six worker processes of a busy machine: 4.4e6 orbits and 212 exposures took 1639 s
SEC_PER_EXPOSURE = 0.6
MAX_FIELDS = 6
WORKERS = int(os.environ.get("SKY_DEEP_WORKERS", "6"))
WORKER_NICE = int(os.environ.get("SKY_DEEP_NICE", "10"))
AU_KM = 1.495978707e8


def auto_fields(ra0: float, dec0: float, m0: float, mjds: list[float], d_au: float) -> list[list[float]]:
    """A guess for the centres of the other passes: the place seen from the Earth at m0 is taken at heliocentric distance d_au and carried
    on a circular prograde orbit in the plane of the ecliptic, then seen again from the Earth at the date of every pass. A real orbit
    differs (a body near perihelion moves a third faster, which puts it ~13 arcmin from the guess after a year), so the page shows these
    centres and lets them be changed. -> [[ra, dec, mjd]]"""
    a, d = math.radians(ra0), math.radians(dec0)
    u = np.array([math.cos(d) * math.cos(a), math.cos(d) * math.sin(a), math.sin(d)])
    R0 = eorbit.earth(m0) / AU_KM
    b = float(u @ R0)
    t = -b + math.sqrt(max(b * b - (float(R0 @ R0) - d_au * d_au), 0.0))
    X = R0 + t * u
    eps = math.radians(23.4393)
    pole = np.array([0.0, -math.sin(eps), math.cos(eps)])
    vh = np.cross(pole, X)
    V = vh / np.linalg.norm(vh) * 0.01720209895 / math.sqrt(d_au)  # au per day
    out = []
    for m in mjds:
        w = X + V * (m - m0) - eorbit.earth(m) / AU_KM
        w = w / np.linalg.norm(w)
        out.append([round(math.degrees(math.atan2(w[1], w[0])) % 360, 5), round(math.degrees(math.asin(w[2])), 5), round(float(m), 3)])
    return out


def deep_params(
    fields: list[list[float]], fov: float, dmin: float, dmax: float, det: tuple[int, ...], keep: float, nmax: int
) -> dict[str, Any]:
    return {
        "fields": [[round(a % 360.0, 5), round(d, 5), round(m, 3)] for a, d, m in fields],
        "fov": round(fov, 2),
        "dmin": round(dmin, 1),
        "dmax": round(dmax, 1),
        "det": list(det),
        "keep": round(keep, 1),
        "nmax": int(nmax),
    }


def selections(index: Index, p: dict[str, Any]) -> list[tuple[list[Exposure], Any, int, list[dict[str, Any]], str | None]]:
    """For every field: the capped exposures, their pass numbers, the pass nearest to the field's date, the passes and the cap note."""
    out = []
    for a, d, m in p["fields"]:
        out.append(distantjob.selection(index, {"ra": a, "dec": d, "fov": p["fov"], "det": p["det"], "mjd": m}))
    return out


def signature(sels: list[Any]) -> str:
    return distantjob.signature([e for s in sels for e in s[0]])


def plan_geometry(index: Index, p: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    """A quick look without reading an exposure: the visits of the pass of every field (their times, the Earth for the telescope), the
    geometry, the number of hypotheses and the time. -> ((G, D, cell, fine) | None, plan dict)"""
    P: list[dict[str, Any]] = []
    info: list[dict[str, Any]] = []
    for fi, ((a, d, _m), (exps, pno, pk, passes, _note)) in enumerate(zip(p["fields"], selections(index, p), strict=True)):
        if not exps or pk < 0:
            return None, {"status": "no-coverage", "error": f"no exposure overlaps field {fi + 1}"}
        sel = [i for i in range(len(exps)) if pno[i] == pk]
        visits = split_visits([exps[i].mjd0 for i in sel], [exps[i].det for i in sel])
        tv = np.array(sorted(float(np.mean([exps[sel[x]].mjd0 for x in v])) for v in visits if len(v) >= px.MIN_VISIT_EXPOSURES))
        if len(tv) < 4:
            return None, {"status": "few-visits", "error": f"field {fi + 1}: fewer than four visits in its pass ({len(tv)})"}
        n = px.grid_for(a, d, p["fov"]).n
        if n > 800:
            return None, {"status": "too-large", "error": "the field is too large"}
        i0 = int(np.argmin(np.abs(tv - 0.5 * (tv.min() + tv.max()))))
        P.append(
            {
                "T": edist.Tangent(a, d),
                "N": n,
                "tv": tv,
                "Rv": np.array([eorbit.earth(t) for t in tv]),
                "i0": i0,
                "W": 1.0,
                "span": float(np.abs(tv - tv[i0]).max()),
                "n_exposures": len(sel),
                "n_visits": len(tv),
                "mjd0": float(min(exps[i].mjd0 for i in sel)),
                "mjd1": float(max(exps[i].mjd1 for i in sel)),
            }
        )
        info.append(
            {
                "field": fi + 1,
                "ra": a,
                "dec": d,
                "pass": pk,
                "passes_in_field": len(passes),
                "pass_mjd0": round(P[-1]["mjd0"], 2),
                "pass_mjd1": round(P[-1]["mjd1"], 2),
                "exposures": len(sel),
                "visits": len(tv),
            }
        )
    order = sorted(range(len(P)), key=lambda k: P[k]["tv"][P[k]["i0"]])
    P = [P[k] for k in order]
    G = edeep.Geometry(P)
    D, cs, fstep = edeep.distances(G, p["dmin"], p["dmax"])
    n_r = P[G.r]["N"]
    nh = edeep.scan(G, {"box": (0, n_r, 0, n_r), "anchor": (0.0, 0.0), "d": D, "cs": cs, "f": fstep, "count": True})
    total_exp = int(sum(q["n_exposures"] for q in P))
    return (G, D, cs, fstep), {
        "status": "plan",
        "passes": [info[k] for k in order],
        "distances": len(D),
        "dmin": p["dmin"],
        "dmax": p["dmax"],
        "cells": int(nh["nc"]),
        "hypotheses": int(nh["nh"]),
        "exposures": total_exp,
        "limit": HYPOTHESES_MAX,
        "estimate_s": int(nh["nh"] * SEC_PER_HYPOTHESIS + SEC_PER_EXPOSURE * total_exp + 60),
    }


def plan(index: Index, p: dict[str, Any]) -> dict[str, Any]:
    """The answer of `plan=1`: {status: 'plan', plan{hypotheses, exposures, ...}, fields, estimate_s, limit} (no exposure is read)."""
    geo, pl = plan_geometry(index, p)
    if geo is None:
        return {"status": pl["status"], "plan": pl, "fields": p["fields"], "estimate_s": None, "limit": HYPOTHESES_MAX}
    status = "plan" if pl["hypotheses"] <= HYPOTHESES_MAX else "too-many-hypotheses"
    return {"status": status, "plan": pl, "fields": p["fields"], "estimate_s": pl["estimate_s"], "limit": HYPOTHESES_MAX}


def _init_worker(path: str, nice: int) -> None:
    """A scan worker runs at a lower priority: the service and the people at the page come first."""
    with contextlib.suppress(OSError):
        os.nice(nice)
    edeep.init_worker(path)


def _count(G: Any, D: np.ndarray, cs: float, fstep: float, n_r: int) -> list[int]:
    return [
        int(edeep.scan(G, {"box": (0, n_r, 0, n_r), "anchor": (0.0, 0.0), "d": [d], "cs": cs, "f": fstep, "count": True})["nh"]) for d in D
    ]


def deep_sep(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Distance on the sky in arcseconds between two (ra, dec) in degrees."""
    return float(math.hypot((a[0] - b[0]) * math.cos(math.radians(a[1])), a[1] - b[1]) * 3600.0)


def _f(x: Any, nd: int = 4) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


async def run_deep(
    ctx: Context, index: Index, fr: FileReader, store: Store, wl: WavelengthMaps | None, psfs: PsfProvider, params: dict[str, Any]
) -> dict[str, Any]:
    key = ctx.job.key
    store.begin(KIND, key, params)
    p = params
    nf = len(p["fields"])
    res: dict[str, Any] = {"version": KIND, "notes": [], "status": "running", "params": params, "fields": p["fields"]}

    def finish(status: str) -> dict[str, Any]:
        res["status"] = status
        res["honest"] = px.honest_words("deep", tuple(p["det"]))
        store.write_meta(KIND, key, res)
        store.complete(key)
        return {"status": status}

    ctx.phase("planning")
    geo, pl = await asyncio.to_thread(plan_geometry, index, p)
    res["plan"] = pl
    if geo is None:
        res["notes"].append(pl.get("error", ""))
        return finish(pl.get("status", "no-plan"))
    if pl["hypotheses"] > HYPOTHESES_MAX:
        res["notes"].append(f"{pl['hypotheses']} hypotheses (the limit is {HYPOTHESES_MAX}): narrow the distance range")
        return finish("too-many-hypotheses")
    sels = selections(index, p)
    notes_cap = [s[4] for s in sels if s[4]]
    res["notes"] += notes_cap
    # the visit differences of every field
    FD: list[dict[str, Any]] = []
    for fi, ((a, d, _m), (exps, pno, pk, _info, _note)) in enumerate(zip(p["fields"], sels, strict=True)):
        grid = px.grid_for(a, d, p["fov"])
        pv = await px.build_visits(ctx, fr, wl, exps, pno, grid, f"Find Planet X, field {fi + 1} of {nf}")
        if pv.failed:
            res["notes"].append(f"field {fi + 1}: {pv.failed} exposures were not read")
        ctx.phase(f"field {fi + 1} of {nf}: static sky and differences")
        DV, notes = await asyncio.to_thread(edist.static_and_differences, pv.vis, pk)
        res["notes"] += [f"field {fi + 1}: {n_}" for n_ in notes]
        if len(DV) < 4:
            res["notes"].append(f"field {fi + 1}: fewer than four usable visits in its pass")
            return finish("few-visits")
        w = np.array([1.0 / v["sg"] ** 2 for v in DV])
        tv = np.array([v["t"] for v in DV])
        i0 = int(np.argmin(np.abs(tv - 0.5 * (tv.min() + tv.max()))))
        P_ = {
            "T": edist.Tangent(a, d),
            "N": grid.n,
            "tv": tv,
            "Rv": np.array([v["R"] for v in DV]),
            "i0": i0,
            "W": float(w.sum()),
            "span": float(np.abs(tv - tv[i0]).max()),
            "Z": np.stack(
                [
                    np.array([v["d"] * wi for v, wi in zip(DV, w, strict=True)], "f4"),
                    np.array([v["good"] * wi for v, wi in zip(DV, w, strict=True)], "f4"),
                ]
            ),
        }
        FD.append({"P": P_, "exps": exps, "pno": pno, "pk": pk, "loaded": pv.loaded, "sc": pv.sc, "f": (a, d), "mid": float(tv[i0])})
        del pv, DV
    FD.sort(key=lambda x: x["mid"])
    G = edeep.Geometry([x["P"] for x in FD])
    D, cs, fstep = edeep.distances(G, p["dmin"], p["dmax"])
    N = G.P[G.r]["N"]
    ctx.phase("counting the orbits that land inside every field")
    cnt = await asyncio.to_thread(_count, G, D, cs, fstep, N)
    Dw = [dd for dd, n_ in zip(D, cnt, strict=True) if n_]
    if not Dw:
        res["notes"].append("no orbit of this distance range lands inside every field: the fields of the passes do not match")
        return finish("no-orbits")
    wk = np.array([n_ for n_ in cnt if n_], "f8")
    nj = max(WORKERS * 6, 2)
    cut = np.searchsorted(np.cumsum(wk), np.linspace(0, wk.sum(), nj + 1)[1:-1])
    jobs = [
        {"box": (0, N, 0, N), "anchor": (0.0, 0.0), "d": part, "cs": cs, "f": fstep} for part in np.split(np.array(Dw), cut) if len(part)
    ]
    res.update(
        distances=[round(float(Dw[0]), 1), round(float(Dw[-1]), 1), len(Dw)],
        hypotheses=int(wk.sum()),
        cell_arcsec_day=round(float(cs), 4),
        fine_arcsec_day=round(float(fstep), 5),
    )
    M = {k: np.zeros((N, N), "f4") for k in ("best", "bd", "bu", "bv", "neg", "nd", "nu", "nv")}
    ctx.phase(f"summing along the orbits: {int(wk.sum())} hypotheses", 0, len(jobs))
    tmp_root = Path(store.root) / "tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="deep_", dir=tmp_root))
    zpaths = [str(tmp / f"z{k}.npy") for k in range(len(G.P))]
    gpath = str(tmp / "geometry.pkl")
    ex: ProcessPoolExecutor | None = None
    t_scan = asyncio.get_running_loop().time()
    try:
        await asyncio.to_thread(edeep.save_geometry, G, gpath, zpaths)
        ex = ProcessPoolExecutor(
            max_workers=WORKERS, mp_context=mp.get_context("spawn"), initializer=_init_worker, initargs=(gpath, WORKER_NICE)
        )
        pending = {asyncio.wrap_future(ex.submit(edeep.scan_job, jb)) for jb in jobs}
        n_done = 0
        while pending:
            await ctx.checkpoint()
            finished, pending = await asyncio.wait(pending, timeout=3.0, return_when=asyncio.FIRST_COMPLETED)
            for fu in finished:
                R_ = fu.result()
                for keys in (("best", "bd", "bu", "bv"), ("neg", "nd", "nu", "nv")):
                    m = R_[keys[0]] > M[keys[0]]
                    for k in keys:
                        M[k][m] = R_[k][m]
                n_done += 1
            secs = asyncio.get_running_loop().time() - t_scan
            ctx.phase(f"summing along the orbits: job {n_done} of {len(jobs)} ({secs:.0f} s)", n_done, len(jobs))
    finally:
        if ex is not None:
            procs = list(getattr(ex, "_processes", {}).values())
            ex.shutdown(wait=False, cancel_futures=True)
            for proc in procs:  # a cancelled or failed job must not leave scan workers behind
                with contextlib.suppress(Exception):
                    proc.terminate()
        shutil.rmtree(tmp, ignore_errors=True)
    res["scan_secs"] = round(asyncio.get_running_loop().time() - t_scan, 1)
    best, neg = M["best"], M["neg"]
    null = edist.peaks(neg, edist.SN_MIN)
    ceil = null[0][2] if null else 0.0
    thr = max(edist.SN_MIN, 1.05 * ceil)
    res.update(ceiling=round(float(ceil), 2), threshold=round(float(thr), 2), sum_peak=round(float(best.max()), 2))
    # the peaks of the sum and of the negated sum
    C: list[dict[str, Any]] = []
    for sign, (A, dd, uu, vv) in ((1, (best, M["bd"], M["bu"], M["bv"])), (-1, (neg, M["nd"], M["nu"], M["nv"]))):
        got: list[dict[str, Any]] = []
        for x, y, s_ in edist.peaks(A, p["keep"]):
            if any(math.hypot(x - c["x"], y - c["y"]) * edist.S < 30.0 for c in got):
                continue
            got.append({"x": x, "y": y, "sn": s_, "d": float(dd[y, x]), "mu": (float(uu[y, x]), float(vv[y, x])), "sign": sign})
            if len(got) >= p["nmax"]:
                break
        C += got
    ctx.phase(f"tuning {len(C)} peaks", 0, len(C))
    sem = asyncio.Semaphore(4)
    tuned = 0

    async def tune(c: dict[str, Any]) -> None:
        nonlocal tuned
        async with sem:
            try:
                x, y, d, mu, snt = await asyncio.to_thread(edeep.refine, G, c["x"], c["y"], c["d"], c["mu"], c["sign"])
                c.update(xr=x, yr=y, dr=d, mur=mu, sn_tuned=snt)
            except Exception:  # a peak that cannot be tuned keeps its grid place
                c.update(xr=c["x"], yr=c["y"], dr=c["d"], mur=c["mu"], sn_tuned=None)
        tuned += 1
        ctx.phase(f"tuning {len(C)} peaks", tuned, len(C))

    await asyncio.gather(*(tune(c) for c in C))
    await ctx.checkpoint()
    res["n_peaks"], res["n_peaks_negated"] = sum(1 for c in C if c["sign"] > 0), sum(1 for c in C if c["sign"] < 0)
    # photometry along the orbits
    orbs = [edeep.Orbit(G, ((c["xr"] - N / 2) * edist.S, (c["yr"] - N / 2) * edist.S), c["dr"], c["mur"]) for c in C]
    meter = px.PointMeter(fr, psfs, wl)

    async def run_tracks(tracks: list[tuple[int, float, float]], label: str) -> dict[int, dict[str, Any]]:
        """tracks = (candidate, du, dv); -> per track: the passes' records and the combined flux (sign applied)"""
        per: list[list[dict[str, Any]]] = []
        for k, fd in enumerate(FD):
            exps, pno, pk, sc = fd["exps"], fd["pno"], fd["pk"], fd["sc"]
            pos: dict[tuple[int, int], tuple[float, float]] = {}
            for ti, (ci, du, dv) in enumerate(tracks):
                for i in fd["loaded"]:
                    if pno[i] != pk or i not in sc:
                        continue
                    mid_i = 0.5 * (exps[i].mjd0 + exps[i].mjd1)
                    ra, dec = orbs[ci].sky(mid_i, eorbit.earth(mid_i) + np.asarray(sc[i], float))
                    if du or dv:
                        ra, dec = (float(x) for x in edist.Tangent(ra, dec).uv2sky(du, dv))
                    pos[(ti, i)] = (ra, dec)
            stats = await px.on_off_tracks(ctx, meter, exps, pno, pk, fd["loaded"], pos, len(tracks), f"{label}, field {k + 1} of {nf}")
            recs = []
            for ti, (ci, _du, _dv) in enumerate(tracks):
                s_ = stats[ti]
                n_pts = sum(n for _, n in s_["bydet"].values())
                at = orbs[ci].sky(fd["mid"], eorbit.earth(fd["mid"]))
                recs.append({"flux": s_["F"], "err": s_["err"], "sn": s_["sn"], "n": n_pts, "at": at})
            per.append(recs)
        out: dict[int, dict[str, Any]] = {}
        for ti, (ci, _du, _dv) in enumerate(tracks):
            ps_ = [per[k][ti] for k in range(len(FD))]
            sign = C[ci]["sign"]
            for q in ps_:
                if q["flux"] is not None:
                    q["flux"] *= sign
                    q["sn"] *= sign
            g = [q for q in ps_ if q["flux"] is not None]
            if g:
                w_ = np.array([1.0 / q["err"] ** 2 for q in g])
                F = float((w_ * [q["flux"] for q in g]).sum() / w_.sum())
                err = float(1.0 / np.sqrt(w_.sum()))
                rec = {
                    "passes": ps_,
                    "flux": F,
                    "err": err,
                    "psn": F / err,
                    "all": bool(len(g) == len(ps_) and all(q["flux"] > 0 for q in g)),
                }
            else:
                rec = {"passes": ps_, "flux": None, "err": None, "psn": float("nan"), "all": False}
            ra_, dec_ = orbs[ci].sky(G.tr, G.Rr)
            rec.update(ra=round(ra_, 6), dec=round(dec_, 6))
            out[ti] = rec
        return out

    own_tracks = [(ci, 0.0, 0.0) for ci in range(len(C))]
    own = await run_tracks(own_tracks, "photometry of the peaks")
    for ci, c in enumerate(C):
        c.update(own[ci])
    pos_c = [c for c in C if c["sign"] > 0]
    neg_c = [c for c in C if c["sign"] < 0]
    nullp = sorted([c["psn"] for c in neg_c if c["all"] and np.isfinite(c["psn"])], reverse=True)
    pthr = max(edist.SN_MIN, 1.05 * nullp[0]) if nullp else edist.SN_MIN
    ok: list[dict[str, Any]] = []
    for c in sorted([c for c in pos_c if c["all"] and np.isfinite(c["psn"]) and c["psn"] >= pthr], key=lambda c: -c["psn"]):
        if any(any(deep_sep(a["at"], b["at"]) < 20 for a, b in zip(c["passes"], o["passes"], strict=True)) for o in ok):
            c["alias"] = True
        else:
            c["ok"] = True
            ok.append(c)
    # the control tracks one arcminute aside, for the confirmed ones (at most four)
    ctl_tracks = [(C.index(c), du, dv) for c in ok[:4] for du, dv in ((60.0, 0.0), (-60.0, 0.0), (0.0, 60.0), (0.0, -60.0))]
    nctl = [0, 0]
    if ctl_tracks:
        ctl = await run_tracks(ctl_tracks, "control tracks")
        for ti, (ci, _du, _dv) in enumerate(ctl_tracks):
            v = ctl[ti]["psn"]
            if np.isfinite(v):
                C[ci].setdefault("ctl", []).append(float(v))
                nctl[0] += 1
                nctl[1] += bool(v >= edist.SN_MIN)
    res["photometry_threshold"] = round(float(pthr), 2)
    res["negated_photometry"] = [round(float(x), 1) for x in nullp[:5]]
    res["control"] = {"measured": nctl[0], "reach_sn5": nctl[1]}
    out_c = []
    for c in sorted(pos_c, key=lambda c: -(c["psn"] if np.isfinite(c["psn"]) else -99.0))[:12]:
        out_c.append(
            {
                "sn": round(c["sn"], 1),
                "sn_tuned": _f(c.get("sn_tuned"), 1),
                "ra": c["ra"],
                "dec": c["dec"],
                "d": round(c["dr"], 1),
                "mu": [round(c["mur"][0], 3), round(c["mur"][1], 3)],
                "flux_mjy": _f(c["flux"]),
                "err_mjy": _f(c["err"]),
                "phot_sn": _f(c["psn"], 1),
                "by_pass": [
                    {"flux_mjy": _f(q["flux"]), "sn": _f(q["sn"], 1), "n": q["n"], "ra": round(q["at"][0], 5), "dec": round(q["at"][1], 5)}
                    for q in c["passes"]
                ],
                "ok": bool(c.get("ok")),
                "alias": bool(c.get("alias")),
                "control_sn": [round(x, 1) for x in c.get("ctl", [])],
            }
        )
    res["candidates"] = out_c
    return finish("ok")
