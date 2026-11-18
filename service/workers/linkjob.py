# ruff: noqa: N806
# mypy: disable-error-code="no-untyped-call,no-any-return"
"""Find Planet X, link and jump.

link   the candidates of two or three finished one-pass results (`distant` products, by key) are linked by ONE heliocentric orbit
       through their tracks: between two passes a distant body jumps by twice its parallax, so two candidates belong together if one
       orbit passes through both; the fit gives the distance and a third pass tests it (engine/link.py)
jump   where a candidate of one pass is in the next pass, as a line of places by distance (the body seen at the candidate's place with
       no radial speed and the apparent rate it had, split into parallax and own motion for each distance, carried over), and which
       candidates of the one-pass results already on disk lie within a corridor of that line

Both read the results of `distant` products; neither reads an exposure.
"""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from engine import distant as edist
from engine import link as elink
from engine import orbit as eorbit
from service.jobs.scheduler import Context
from service.products.store import Store
from service.products.versions import VERSIONS
from service.workers import distantjob
from service.workers import planetx as px
from service.workers.planetx import honest_words

KIND = "link"


def distant_result(store: Store, key: str) -> dict[str, Any] | None:
    """The result of a finished one-pass search by its key, or None."""
    if not key.isalnum() or not store.is_complete(key):
        return None
    meta = store.read_meta(distantjob.KIND, key)
    return meta if meta and meta.get("status") == "ok" else None


def all_distant_results(store: Store) -> list[tuple[str, dict[str, Any]]]:
    """Every finished one-pass result in the store (current version)."""
    root = store.root / "products" / distantjob.KIND / VERSIONS[distantjob.KIND]
    out = []
    for meta_path in sorted(root.glob("*/*/meta.json")) if root.is_dir() else []:
        key = meta_path.parent.name
        if store.is_complete(key):
            try:
                m = json.loads(Path(meta_path).read_text())
            except (OSError, ValueError):
                continue
            if m.get("status") == "ok":
                out.append((key, m))
    return out


def link_params(keys: list[str], tol: float, every: bool) -> dict[str, Any]:
    return {"keys": keys, "tol": round(tol, 1), "all": bool(every)}


def _num(v: Any) -> float | None:
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else round(float(v), 3)


LINK_TRIALS = 5  # the chance control: the second pass's candidates at chance places of their field, linked the same way
LINK_PAIRS_MAX = 9  # measured only up to this many pairs (a chance pair's fit takes ~4 s: it never converges): 3 x 3 confirmed candidates


def _moved(pts: list[Any], du: float, dv: float) -> list[Any]:
    """A candidate's track points moved by (du, dv) arcsec on the sky (east, north), times and observer kept."""
    out = []
    for tm, rr, ra, dec in pts:
        out.append((tm, rr, ra + du / 3600.0 / max(math.cos(math.radians(dec)), 1e-3), dec + dv / 3600.0))
    return out


def chance_links(passes: list[list[Any]], halves: list[float], tol: float, trials: int = LINK_TRIALS, seed: int = 7) -> dict[str, Any]:
    """What chance alone links: the candidates of the second pass are put at uniform chance places of their field (±`halves[1]`
    arcsec from where they are, their tracks' shape kept), the first and third passes stay, and the passes are linked the same way.
    -> {trials, pairs, one_body (chance links per trial, misfit <= tol and bound), confirmed (chance links the third pass confirms)};
    with more than LINK_PAIRS_MAX pairs it is not measured and says so."""
    a, b = passes[0], passes[1]
    pairs = len(a) * len(b)
    if pairs > LINK_PAIRS_MAX:
        return {
            "trials": 0,
            "pairs": pairs,
            "note": f"not measured: {pairs} pairs (it is measured up to {LINK_PAIRS_MAX}, the confirmed candidates)",
        }
    rng = np.random.default_rng(seed)
    one = conf = 0
    for _ in range(trials):
        moved = [(info, _moved(pts, *rng.uniform(-halves[1], halves[1], 2))) for info, pts in b]
        got = elink.link_passes([a, moved, *passes[2:]], tol=tol)
        one += sum(1 for lk in got if lk["ok"])
        conf += sum(1 for lk in got if lk.get("ok3"))
    return {"trials": trials, "pairs": pairs, "one_body": round(one / trials, 3), "confirmed": round(conf / trials, 3)}


def link_results(
    results: dict[str, dict[str, Any] | None], params: dict[str, Any], progress: Any = None, cancel: Any = None
) -> dict[str, Any]:
    """The link of the candidates of the passes named by `params`, given their one-pass results (by key; None where there is none)
    -> the result dict (status ok / missing / nothing-to-link). Runs in a worker process."""
    res: dict[str, Any] = {"status": "running", "notes": [], "passes": [], "links": [], "params": params}
    passes = []
    for k in params["keys"]:
        r = results.get(k)
        if r is None:
            res["status"] = "missing"
            res["notes"].append(f"the one-pass result {k} is not there (or has not finished): run the distance search for it first")
            return res
        if not r.get("grid") or r.get("t0") is None or r.get("span_days") is None:
            res["status"] = "missing"
            res["notes"].append(f"the one-pass result {k} has no grid or reference time")
            return res
        cands = [c for c in (r.get("candidates") or []) if params["all"] or c.get("ok")]
        pts = []
        for ci, c in enumerate(cands):
            info = {
                "key": k,
                "i": ci,
                "sn": c.get("sn"),
                "phot_sn": c.get("phot_sn"),
                "d": c.get("d"),
                "d_lo": c.get("d_lo"),
                "d_hi": c.get("d_hi"),
                "ra": c.get("ra"),
                "dec": c.get("dec"),
                "flux_mjy": c.get("flux_mjy"),
            }
            pts.append((info, elink.candidate_points(c, r["grid"], r["t0"], r["span_days"])))
        half = float(r["grid"].get("n") or 0) * edist.S / 2.0  # arcsec: half the field of the pass's search
        passes.append((float(r["t0"]), k, pts, len(cands), half))
    passes.sort(key=lambda x: x[0])
    res["passes"] = [{"key": k, "t0": round(t0, 3), "candidates": n} for t0, k, _, n, _ in passes]
    if any(n == 0 for _, _, _, n, _ in passes[:2]):
        res["status"] = "nothing-to-link"
        res["notes"].append("a pass has no confirmed candidate: nothing to link")
        return res
    links = elink.link_passes([x[2] for x in passes], tol=params["tol"], progress=progress)
    out = []
    for lk in links:
        o: dict[str, Any] = {
            "a": lk["a"],
            "b": lk["b"],
            "one_body": bool(lk["ok"]),
            "two_passes": {k: _num(v) for k, v in lk["fit2"].items() if k != "plane"},
        }
        if "fit3" in lk:
            o.update(
                c=lk["c"],
                predicted_third=[round(lk["pred"][0], 5), round(lk["pred"][1], 5)],
                predicted_sep_arcsec=round(lk["pred_sep"], 1),
                confirmed=bool(lk["ok3"]),
                three_passes={k: _num(v) for k, v in lk["fit3"].items() if k != "plane"},
            )
        out.append(o)
    res["links"] = out[:40]
    best = next((o for o in out if o.get("confirmed")), None) or next((o for o in out if o["one_body"]), None)
    if best:
        f = best.get("three_passes") or best["two_passes"]
        res["best"] = {
            "passes": 3 if best.get("three_passes") else 2,
            "distance_au": f["dist"],
            "distance_err_au": f["dist_err"],
            "a_au": f["a"],
            "e": f["e"],
            "misfit_arcsec": f["rms"],
            "confirmed": bool(best.get("confirmed")),
            "a": best["a"],
            "b": best["b"],
            "c": best.get("c"),
        }
    # what chance links with the same candidates (the second pass moved to chance places): the real links are read against it
    res["chance"] = chance_links([x[2] for x in passes], [x[4] for x in passes], params["tol"])
    res["tolerance_arcsec"] = params["tol"]
    res["honest"] = honest_words("link")
    res["status"] = "ok"
    return res


async def run_link(ctx: Context, store: Store, params: dict[str, Any]) -> dict[str, Any]:
    key = ctx.job.key
    store.begin(KIND, key, params)
    ctx.phase("fitting orbits through the tracks of the passes")

    def prog(i: float, n: float) -> None:
        i, n = int(i), int(n)
        ctx.phase(f"fitting orbits through the tracks of the passes: pair {i} of {n}", i, max(n, 1))

    results = {k: await asyncio.to_thread(distant_result, store, k) for k in params["keys"]}  # the reading is here, the fits in a worker
    res = await px.tracked(ctx, link_results, results, params, on_progress=prog)
    store.write_meta(KIND, key, res)
    store.complete(key)
    return {"status": res["status"]}


def jump_line(
    r: dict[str, Any], c: dict[str, Any], t2: float, dmin: float | None = None, dmax: float | None = None, n: int = 40
) -> list[dict[str, float]]:
    """The places of candidate `c` of the one-pass result `r` in the pass whose reference time is `t2`, for n distances inside the
    candidate's interval (or dmin..dmax) -> [{d, ra, dec}].

    The body is taken at the candidate's place at the pass's reference time with zero radial speed. What the pass measured is the
    apparent rate on the sky at that time (the parallax rate of the candidate's distance plus its own motion); for another distance the
    own motion is what is left after that distance's parallax rate, and it is carried over t2 − t1."""
    g = r["grid"]
    T = edist.Tangent(g["ra0"], g["dec0"])
    N = g["n"]
    u0, v0 = (c["x"] - N / 2) * edist.S, (c["y"] - N / 2) * edist.S
    n1 = T.n0 + (-u0 / edist.ARC) * T.e + (v0 / edist.ARC) * T.n
    n1 = n1 / np.linalg.norm(n1)
    t1 = float(r["t0"])
    R1, R2 = eorbit.earth(t1), eorbit.earth(t2)
    h = 3.0
    Rh = np.array([eorbit.earth(t1 - h), eorbit.earth(t1 + h)])

    def parallax_rate(dd: float) -> Any:
        pu, pv = edist.parallax(dd, Rh, R1, T)
        return np.array([(pu[1] - pu[0]) / (2 * h), (pv[1] - pv[0]) / (2 * h)])

    apparent = parallax_rate(float(c["d"])) + np.asarray(c["mu"], float)
    lo = float(dmin if dmin is not None else (c.get("d_lo") or c["d"] / 1.5))
    hi = float(dmax if dmax is not None else (c.get("d_hi") or c["d"] * 1.5))
    out = []
    for d in np.geomspace(max(lo, 5.0), max(hi, lo * 1.05), n):
        dd = float(d)
        mu = apparent - parallax_rate(dd)
        b0 = R1 @ n1
        rho = -b0 + math.sqrt(max(b0 * b0 - R1 @ R1 + (dd * eorbit.AU) ** 2, 0.0))
        X1 = R1 + rho * n1
        w = X1 - R2
        w = w / np.linalg.norm(w)
        pu, pv = T.vec2uv(w)
        pu, pv = float(pu) + float(mu[0]) * (t2 - t1), float(pv) + float(mu[1]) * (t2 - t1)
        ra, dec = T.uv2sky(pu, pv)
        out.append({"d": round(dd, 2), "ra": round(float(ra), 5), "dec": round(float(dec), 5)})
    return out


JUMP_FIELD_FOV = 18.0  # arcmin: the one-pass search fields that cover the corridor (the distance search's default size)
JUMP_FIELDS_MAX = 6  # a check starts at most this many


def jump_fields(line: list[dict[str, float]], t2: float, near: list[dict[str, Any]], fov: float = JUMP_FIELD_FOV) -> list[dict[str, Any]]:
    """The one-pass search fields that cover the line of places in the other pass: tiles of `fov` arcmin centred on points of the line,
    one every 0.8 fov along it (they overlap), each with the distance range of the line inside it, and whether a finished one-pass
    result of that pass (`near`: grid centres and sizes) already covers it. -> [{ra, dec, fov, mjd, dmin, dmax, covered}]."""
    if not line:
        return []
    pts = np.array([[x["ra"], x["dec"]] for x in line])
    cosd = math.cos(math.radians(float(pts[:, 1].mean())))
    along = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(pts[:, 0]) * cosd, np.diff(pts[:, 1])) * 60.0)])
    out: list[dict[str, Any]] = []
    at = 0.0
    while True:
        k = int(np.argmin(np.abs(along - at)))
        ra, dec = float(pts[k, 0]), float(pts[k, 1])
        inside = [x["d"] for x in line if math.hypot((x["ra"] - ra) * cosd, x["dec"] - dec) * 60.0 <= fov / 2.0]
        dmin, dmax = (min(inside), max(inside)) if inside else (line[k]["d"], line[k]["d"])
        dmax = max(dmax, dmin * 1.2)
        covered = any(math.hypot((g["ra0"] - ra) * cosd, g["dec0"] - dec) * 60.0 <= max(0.0, g["fov"] / 2.0 - fov / 4.0) for g in near)
        out.append({"ra": round(ra, 5), "dec": round(dec, 5), "fov": fov, "mjd": round(t2, 3), "dmin": round(max(dmin, 5.0), 1),
                    "dmax": round(dmax, 1), "covered": covered})  # fmt: skip
        if at >= along[-1]:
            break
        at = min(at + 0.8 * fov, float(along[-1]))
    return out


def run_jump(
    store: Store, key: str, ci: int, t2: float, corridor: float, dmin: float | None = None, dmax: float | None = None
) -> dict[str, Any] | None:
    """The line of places of candidate `ci` of the one-pass result `key` at time t2, the corridor, and the candidates of the other
    finished one-pass
    results (reference time within 40 days of t2) that lie within `corridor` arcmin of the line."""
    r = distant_result(store, key)
    if r is None or not r.get("candidates") or ci >= len(r["candidates"]):
        return None
    c = r["candidates"][ci]
    line = jump_line(r, c, t2, dmin, dmax)
    la = np.array([[x["ra"], x["dec"]] for x in line])
    found = []
    near: list[dict[str, Any]] = []
    for k2, r2 in all_distant_results(store):
        if k2 == key or r2.get("t0") is None or abs(float(r2["t0"]) - t2) > 40:
            continue
        g2 = r2.get("grid") or {}
        if g2.get("ra0") is not None:
            near.append({"ra0": g2["ra0"], "dec0": g2["dec0"], "fov": float(g2.get("n") or 0) * edist.S / 60.0})
        for i2, c2 in enumerate(r2.get("candidates") or []):
            dd = np.hypot((la[:, 0] - c2["ra"]) * math.cos(math.radians(c2["dec"])), la[:, 1] - c2["dec"]) * 60.0
            m = int(np.argmin(dd))
            if dd[m] <= corridor:
                found.append(
                    {
                        "key": k2,
                        "i": i2,
                        "sn": c2.get("sn"),
                        "phot_sn": c2.get("phot_sn"),
                        "ok": bool(c2.get("ok")),
                        "d": c2.get("d"),
                        "ra": c2["ra"],
                        "dec": c2["dec"],
                        "off_line_arcmin": round(float(dd[m]), 2),
                        "line_distance_au": line[m]["d"],
                        "own_distance_in_line_interval": bool(line[0]["d"] <= (c2.get("d") or 0) <= line[-1]["d"]),
                    }
                )
    seen: set[tuple[float, float, Any]] = set()
    uniq = []
    for x in sorted(found, key=lambda x: x["off_line_arcmin"]):
        k_ = (round(x["ra"], 4), round(x["dec"], 4), x.get("sn"))
        if k_ not in seen:
            seen.add(k_)
            uniq.append(x)
    length = float(np.hypot((la[0, 0] - la[-1, 0]) * math.cos(math.radians(la[:, 1].mean())), la[0, 1] - la[-1, 1]) * 60.0)
    return {
        "key": key,
        "candidate": ci,
        "t1": r["t0"],
        "t2": t2,
        "line": line,
        "corridor_arcmin": corridor,
        "line_length_arcmin": round(length, 1),
        "in_corridor": uniq,
        # the corridor as one-pass searches of the other pass: an empty corridor means something only where every field was searched
        "fields": jump_fields(line, t2, near),
        "honest": honest_words("jump"),
    }
