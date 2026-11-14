"""The search for unknown movers in the film of one pass: candidates in the motion maps of the visits, straight-line tracks through
them, and
the chance control that says how many tracks of each length noise alone makes (docs/plan/02-methods/2.3-views.md; the lab's film search).

  1. candidates: the peaks of every frame's motion map (z, the static sky and the halos of saturated stars masked) at or above `SN_MIN`
  robust
     sigma, and at least `STAR_K` of what the static sky shows there (an undersampled star never subtracts cleanly); the strongest
     `LINK_MAX`
     take part in the linking
  2. tracks: every pair of candidates of two visits that moves at 3–200 arcsec/hour proposes a straight line; candidates of other visits
  within
     `TOL` arcsec join; a track needs `NMIN_FOUND` visits in which a candidate was FOUND, and is then followed into the visits where the
     line
     predicts it: the motion map there counts when it reaches `REC_Z` within `REC_R` grid points (a body of 6σ is often 4–5σ in one visit)
  3. chance: the same candidates moved to chance places (times and visits kept), linked and recovered in exactly the same way, `trials`
  times; a track
     is drawn only when it is seen in at least as many visits as the shortest length that chance makes less than `CHANCE_MAX` times per
     field

Coordinates: grid column x grows westward and row y southward (row 0 north, column 0 east); on the tangent plane east and north are
positive:
east = (c − x)·step, north = (c − y)·step, c = (n − 1)/2.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import maximum_filter

from engine.film import Frame

SN_MIN = 5.0
STAR_K = 0.74
LINK_MAX = 1000
VMIN, VMAX, TOL, DT_MAX = 3.0, 200.0, 4.0, 5.0
RMS_MAX = 1.5
NMIN_FOUND = 4
REC_Z, REC_R, REC_PAD = 3.5, 1.5, 0.5
CHANCE_MAX = 0.15
CONTROL_TRIALS = 40
MIN_STATIC_FRAMES = 3

F64 = NDArray[np.float64]


@dataclass(frozen=True)
class Candidate:
    t: float  # MJD of the frame
    east: float  # arcsec on the tangent plane
    north: float
    frame: int
    x: float  # grid column / row of the film
    y: float
    sn: float


def static_sky(frames: Sequence[Frame]) -> dict[int, NDArray[np.float32]]:
    """The static sky of every detector: the median of its frames (a place needs at least three frames and 30 % of them), as the film
    builds it."""
    by: dict[int, list[Frame]] = defaultdict(list)
    for f in frames:
        by[f.det].append(f)
    out: dict[int, NDArray[np.float32]] = {}
    for det, fs in by.items():
        stack = np.stack([f.sky for f in fs])
        cover = np.isfinite(stack).sum(axis=0)
        with np.errstate(all="ignore"):
            st = np.nanmedian(stack, axis=0).astype(np.float32)
        st[cover < max(MIN_STATIC_FRAMES, int(np.ceil(0.3 * len(fs))))] = np.nan
        out[det] = st
    return out


def frame_peaks(
    motion: NDArray[np.float32], sky: NDArray[np.float32], static: NDArray[np.float32], sn_min: float = SN_MIN, star_k: float = STAR_K
) -> NDArray[np.float64]:
    """Candidates of one frame: rows (x, y, S/N, flux above the median) in grid points."""
    good = np.isfinite(motion) & np.isfinite(static) & np.isfinite(sky)
    if good.sum() < 1000:
        return np.zeros((0, 4))
    z = np.where(good, motion, -1e9)
    d = np.where(good, sky - static, 0.0)
    md = float(np.median(d[good]))
    sm = np.where(good, d - md, 0.0)
    rs = np.where(good, static, 0.0)
    ref = np.maximum(rs - float(np.median(rs[good])), 0.0)
    pk = (z == maximum_filter(z, 5)) & (z >= sn_min) & (sm >= star_k * ref)
    ys, xs = np.where(pk)
    return np.array([[x, y, z[y, x], sm[y, x]] for x, y in zip(xs, ys, strict=True)], float).reshape(-1, 4)


def find_candidates(frames: Sequence[Frame], step_arcsec: float, n: int) -> list[Candidate]:
    """Every candidate of every frame's motion map, strongest first."""
    st = static_sky(frames)
    c = (n - 1) / 2.0
    out: list[Candidate] = []
    for k, f in enumerate(frames):
        if f.motion is None or f.det not in st:
            continue
        for x, y, sn, _ in frame_peaks(f.motion, f.sky, st[f.det]):
            out.append(Candidate(f.mjd, float((c - x) * step_arcsec), float((c - y) * step_arcsec), k, float(x), float(y), float(sn)))
    out.sort(key=lambda q: -q.sn)
    return out


def link(
    t: F64, u: F64, v: F64, vis: F64, nmin: int = 3, vmin: float = VMIN, vmax: float = VMAX, tol: float = TOL, dt_max: float = DT_MAX
) -> list[dict[str, Any]]:
    """Candidates (time in days, east and north in arcsec, visit number) -> tracks: a straight line at a constant rate through at least
    `nmin`
    visits. -> dicts idx, nvis, hours, rate (arcsec/hour), pa (degrees east of north), rms (arcsec), t0."""
    found: set[frozenset[int]] = set()
    for i in range(len(t)):
        for j in np.where((t > t[i] + 0.01) & (t < t[i] + dt_max) & (vis != vis[i]))[0]:
            rate = np.hypot(u[j] - u[i], v[j] - v[i]) / ((t[j] - t[i]) * 24)
            if not (vmin <= rate <= vmax):
                continue
            a = np.where((np.abs(t - t[i]) < dt_max) & (vis != vis[i]) & (vis != vis[j]))[0]
            f = (t[a] - t[i]) / (t[j] - t[i])
            m = a[np.hypot(u[i] + (u[j] - u[i]) * f - u[a], v[i] + (v[j] - v[i]) * f - v[a]) < tol]
            if len(m) + 2 >= nmin:
                found.add(frozenset([i, int(j)] + [int(q) for q in m]))
    groups: list[set[int]] = []
    for s in sorted(found, key=len, reverse=True):
        for g in groups:
            if len(g & s) >= 2:
                g |= s
                break
        else:
            groups.append(set(s))
    out: list[dict[str, Any]] = []
    for g in groups:
        m = np.array(sorted(g, key=lambda q: t[q]))
        while True:
            tt = (t[m] - t[m].mean()) * 24
            pu, pv = np.polyfit(tt, u[m], 1), np.polyfit(tt, v[m], 1)
            res = np.hypot(u[m] - np.polyval(pu, tt), v[m] - np.polyval(pv, tt))
            j = int(np.argmax(res))
            dup = [q for q in range(len(m)) if (vis[m] == vis[m[q]]).sum() > 1]
            if dup:
                m = np.delete(m, max(dup, key=lambda q: res[q]))
            elif res[j] > 3 * tol and len(m) > 2:
                m = np.delete(m, j)
            else:
                break
            if len(set(vis[m])) < nmin:
                break
        if len(set(vis[m])) < nmin:
            continue
        out.append(
            {
                "idx": [int(q) for q in m],
                "nvis": len(set(vis[m])),
                "hours": float(np.ptp(tt)),
                "rate": float(np.hypot(pu[0], pv[0])),
                "pa": float(np.degrees(np.arctan2(pu[0], pv[0])) % 360),
                "rms": float(np.sqrt(np.mean(res**2))),
                "t0": float(t[m].mean()),
            }
        )
    return sorted(out, key=lambda r: (-r["nvis"], r["rms"]))


def recover(
    tr: dict[str, Any], t: F64, u: F64, v: F64, frame_t: F64, zmaps: NDArray[np.float32], step: float
) -> list[tuple[int, float, float, float]]:
    """The visits of a track's time span where the search had no candidate: the line's predicted place is read in that visit's motion map.
    -> list of (frame, x, y, z) in grid points."""
    m = np.asarray(tr["idx"])
    tt = (t[m] - t[m].mean()) * 24
    pu, pv = np.polyfit(tt, u[m], 1), np.polyfit(tt, v[m], 1)
    have = {int(q) for q in tr.get("vis", [])}
    lo, hi = t[m].min() - REC_PAD, t[m].max() + REC_PAD
    nfr, n, _ = zmaps.shape
    c = (n - 1) / 2.0
    r = int(np.ceil(REC_R))
    out: list[tuple[int, float, float, float]] = []
    for k in range(nfr):
        if k in have or not (lo <= frame_t[k] <= hi):
            continue
        h = (frame_t[k] - t[m].mean()) * 24
        x, y = c - np.polyval(pu, h) / step, c - np.polyval(pv, h) / step
        ix, iy = round(float(x)), round(float(y))
        if not (r <= ix < n - r and r <= iy < n - r):
            continue
        win = zmaps[k, iy - r : iy + r + 1, ix - r : ix + r + 1]
        if np.isfinite(win).any() and np.nanmax(win) >= REC_Z:
            out.append((k, float(x), float(y), float(np.nanmax(win))))
    return out


def tracks_with_recovery(
    t: F64, u: F64, v: F64, vis: F64, frame_t: F64, zmaps: NDArray[np.float32], step: float, nmin: int = 3
) -> list[dict[str, Any]]:
    out = []
    for tr in link(t, u, v, vis, nmin):
        tr["vis"] = sorted({int(vis[q]) for q in tr["idx"]})
        tr["rec"] = recover(tr, t, u, v, frame_t, zmaps, step)
        tr["score"] = len(tr["vis"]) + len(tr["rec"])
        out.append(tr)
    return sorted(out, key=lambda r: (-r["score"], r["rms"]))


def chance_by_visits(
    t: F64,
    u: F64,
    v: F64,
    vis: F64,
    half: float,
    frame_t: F64,
    zmaps: NDArray[np.float32],
    step: float,
    ntrial: int = CONTROL_TRIALS,
    nmax: int = 10,
    seed: int = 1,
) -> NDArray[np.float64]:
    """The control by length: the same candidates at chance places (times and visits kept), linked and recovered like the real tracks.
    -> c where c[n] is the mean number of chance tracks seen in at least n visits per trial (n = 0 .. nmax)."""
    rng = np.random.default_rng(seed)
    c = np.zeros(nmax + 1)
    for _ in range(ntrial):
        uu, vv = rng.uniform(-half, half, len(t)), rng.uniform(-half, half, len(t))
        for r in tracks_with_recovery(t, uu, vv, vis, frame_t, zmaps, step):
            if r["nvis"] >= NMIN_FOUND and r["rms"] <= RMS_MAX:
                c[: min(r["score"], nmax) + 1] += 1
    return c / max(ntrial, 1)


def search(frames: Sequence[Frame], step_arcsec: float, n: int, trials: int = CONTROL_TRIALS) -> dict[str, Any]:
    """The whole search of a film: candidates, tracks drawn, the control. Track points are in the film's grid (x column, y row, row 0
    north)."""
    cands = find_candidates(frames, step_arcsec, n)
    out: dict[str, Any] = {"candidates": len(cands), "tracks": [], "control": {"trials": trials, "min_visits_drawn": None, "chance": {}}}
    if len(cands) < 3:
        return out
    top = cands[:LINK_MAX]
    t = np.array([q.t for q in top])
    u = np.array([q.east for q in top])
    v = np.array([q.north for q in top])
    vis = np.array([q.frame for q in top], float)
    frame_t = np.array([f.mjd for f in frames])
    zmaps = np.stack([f.motion if f.motion is not None else np.full((n, n), np.nan, np.float32) for f in frames])
    tracks = tracks_with_recovery(t, u, v, vis, frame_t, zmaps, step_arcsec)
    ch = chance_by_visits(t, u, v, vis, n * step_arcsec / 2.0, frame_t, zmaps, step_arcsec, trials)
    okn = [k for k in range(NMIN_FOUND, len(ch)) if ch[k] < CHANCE_MAX]
    nv_min = okn[0] if okn else len(ch)
    out["control"] = {
        "trials": trials,
        "min_visits_drawn": nv_min,
        "chance": {str(k): round(float(ch[k]), 3) for k in range(NMIN_FOUND, len(ch)) if ch[k] > 0 or k == nv_min},
    }
    drawn = []
    for tr in tracks:
        if tr["nvis"] < NMIN_FOUND or tr["score"] < nv_min or tr["rms"] > RMS_MAX:
            continue
        pts = [
            {"f": int(top[q].frame), "x": round(top[q].x, 2), "y": round(top[q].y, 2), "z": round(top[q].sn, 1), "how": "found"}
            for q in tr["idx"]
        ]
        pts += [{"f": int(k), "x": round(x, 2), "y": round(y, 2), "z": round(z, 1), "how": "recovered"} for k, x, y, z in tr["rec"]]
        pts.sort(key=lambda p: frame_t[p["f"]])
        z_found = [p["z"] for p in pts if p["how"] == "found"]
        drawn.append(
            {
                "n": int(tr["score"]),
                "n_found": int(tr["nvis"]),
                "n_recovered": len(tr["rec"]),
                "hours": round(tr["hours"], 2),
                "rate": round(tr["rate"], 2),
                "pa": round(tr["pa"], 1),
                "rms": round(tr["rms"], 2),
                "method": "line",
                "verified": False,
                "z_together": round(float(np.sum(z_found) / np.sqrt(len(z_found))), 1),
                "pts": pts,
            }
        )
    out["tracks"] = drawn
    return out


def match_known(
    tracks: Sequence[dict[str, Any]], bodies: Sequence[dict[str, Any]], step_arcsec: float, tol_arcsec: float = 30.0
) -> list[dict[str, Any] | None]:
    """The known body (JPL sb_ident + Horizons, service/bodies.py) that explains each track: its predicted place on the track's frames
    is within `tol_arcsec` of the track's points (median over the frames they share, at least half of the track's points).
    -> per track None or {name, V, sep_arcsec}."""
    out: list[dict[str, Any] | None] = []
    for tr in tracks:
        best: tuple[float, dict[str, Any]] | None = None
        for b in bodies:
            at = {int(q["k"]): q for q in b.get("frames", []) if q.get("inside")}
            d = [float(np.hypot(p["x"] - at[p["f"]]["x"], p["y"] - at[p["f"]]["y"]) * step_arcsec) for p in tr["pts"] if p["f"] in at]
            if len(d) >= max(2, len(tr["pts"]) // 2):
                med = float(np.median(d))
                if med <= tol_arcsec and (best is None or med < best[0]):
                    best = (med, {"name": b["name"], "V": b.get("v"), "sep_arcsec": round(med, 1)})
        out.append(best[1] if best else None)
    return out
