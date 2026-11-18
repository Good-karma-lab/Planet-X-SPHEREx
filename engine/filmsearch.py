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
from collections.abc import Awaitable, Callable, Sequence
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
RMS_MAX = 2.0
NMIN_FOUND = 4
ORBIT_FOUND_MIN = 3  # an orbit track of verified candidates needs this many visits where one was found (the chance control sets the total)
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
    rows = []
    for x, y in zip(xs, ys, strict=True):
        # the place to a fraction of a grid point: the centroid of the positive part of the 3 x 3 motion map around the peak
        win = np.clip(
            np.where(good[max(y - 1, 0) : y + 2, max(x - 1, 0) : x + 2], z[max(y - 1, 0) : y + 2, max(x - 1, 0) : x + 2], 0.0), 0.0, None
        )
        yy, xx = np.mgrid[max(y - 1, 0) : y + 2, max(x - 1, 0) : x + 2]
        w = float(win.sum())
        cx, cy = (float((win * xx).sum() / w), float((win * yy).sum() / w)) if w > 0 else (float(x), float(y))
        rows.append([cx, cy, z[y, x], sm[y, x]])
    return np.array(rows, float).reshape(-1, 4)


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


ORBIT_TRIALS = 12
SHORT_NVIS = 3  # a track this short is drawn only when it is stronger than every track of its length that chance made in the trials
VERIFY_MAX = 5000


def _zm(z: Any) -> NDArray[np.float32]:
    """The motion maps: an array, or the path of the .npy file that holds them (read in place, shared by the worker processes)."""
    return np.load(z, mmap_mode="r") if isinstance(z, str) else z  # type: ignore[no-any-return]


def save_zmaps(frames: Sequence[Frame], n: int, path: str) -> F64:
    """Write the motion maps of the frames to `path` (.npy) so that the stages can read them without carrying them around.
    -> frame times."""
    z = np.stack([f.motion if f.motion is not None else np.full((n, n), np.nan, np.float32) for f in frames])
    np.save(path, z)
    return np.array([f.mjd for f in frames])


def orbit_inputs(
    cands: Sequence[Candidate], verified: dict[int, dict[str, Any]], obs: dict[int, tuple[float, F64]], ra0: float, dec0: float
) -> dict[str, Any]:
    """The candidates the orbit linking works on: those that on − off photometry found positive in every exposure of their visit (at least
    PRE_NSIG sigma), with their place, time and the observer's place."""
    from engine import onoff

    ok = [c for c, v in verified.items() if v["allpos"] and v["sig"] >= onoff.PRE_NSIG and cands[c].frame in obs]
    inp: dict[str, Any] = {"ok": ok, "ra0": ra0, "dec0": dec0}
    if len(ok) >= 3:
        sel = [cands[c] for c in ok]
        inp["vis"] = np.array([q.frame for q in sel])
        inp["t"] = np.array([obs[q.frame][0] for q in sel])
        inp["rr"] = np.array([obs[q.frame][1] for q in sel])
        inp["u"] = -np.array([q.east for q in sel])  # the orbit module counts u to the west
        inp["v"] = np.array([q.north for q in sel])
        inp["sig"] = np.array([float(verified[c]["sig"]) for c in ok])  # the on - off significance: the strength of a track
    return inp


def orbit_link(inp: dict[str, Any]) -> list[dict[str, Any]]:
    """Orbit tracks through the linked candidates (one stage; the model's own numbers, see engine/orbit.py)."""
    from engine import orbit as _orbit

    eorbit: Any = _orbit  # the orbit model is numeric code without annotations
    if len(inp["ok"]) < 3:
        return []
    plane = eorbit.Plane(inp["ra0"], inp["dec0"])
    out: list[dict[str, Any]] = eorbit.link(inp["u"], inp["v"], inp["vis"], inp["t"], inp["rr"], plane)
    return out


def orbit_trial(inp: dict[str, Any], k: int) -> list[float]:
    """One trial of the control: the same candidates at chance places, linked the same way. -> how many chance tracks of at least n visits
    (n = 0 .. 10), then (index 11) the strongest SHORT_NVIS-visit chance track's together-significance (0 when there is none): the
    candidates keep their own significance when they are moved, so a chance track is as strong as real candidates make it."""
    from engine import orbit as _orbit

    eorbit: Any = _orbit
    ch: list[float] = [0.0] * 12
    sig = inp.get("sig")
    for r in eorbit.control_trial(inp["u"], inp["v"], inp["vis"], inp["t"], inp["rr"], inp["ra0"], inp["dec0"], 1000 + k):
        for j in range(min(r["nvis"], 10) + 1):
            ch[j] += 1
        if r["nvis"] == SHORT_NVIS and sig is not None:
            zs = sig[np.asarray(r["idx"], int)]
            ch[11] = max(ch[11], float(np.sum(zs) / np.sqrt(len(zs))))
    return ch


def orbit_assemble(
    inp: dict[str, Any],
    tr: list[dict[str, Any]],
    counts: Sequence[Sequence[float]],
    cands: Sequence[Candidate],
    verified: dict[int, dict[str, Any]],
    obs: dict[int, tuple[float, F64]],
    frame_t: F64,
    zmaps: Any,
    step: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The tracks that chance does not make: the raw orbit tracks followed into the visits where the orbit predicts them (read on the
    motion maps), kept when they reach what the control (`counts`: one list per trial) shows chance does not. -> (tracks, control)."""
    from engine import onoff
    from engine import orbit as _orbit

    eorbit: Any = _orbit
    ok = inp["ok"]
    trials = len(counts)
    ctl: dict[str, Any] = {"trials": trials, "min_visits_drawn": None, "chance": {}, "verified_candidates": len(ok)}
    if len(ok) < 3:
        return [], ctl
    arr = np.array([list(c)[:11] for c in counts], float) if trials else np.zeros((0, 11))
    ch = arr.sum(axis=0) / max(trials, 1) if trials else np.zeros(11)
    okn = [k for k in range(3, len(ch)) if ch[k] < CHANCE_MAX]
    nv_min = max(okn[0] if okn else len(ch), NMIN_FOUND)
    # a SHORT_NVIS-visit track stronger than every chance track of that length in all the trials: chance made none like it (0 of
    # `trials`, below CHANCE_MAX); it is drawn though shorter than nv_min (7948 Whitaker leaves the field after 3 visits at 7-42 sigma)
    strengths = [float(c[11]) for c in counts if len(c) > 11]  # trials of before this rule carry none: no short tracks then
    short_bar = max(strengths) if trials and len(strengths) == trials else None
    ctl.update(min_visits_drawn=nv_min, chance={str(k): round(float(ch[k]), 3) for k in range(3, len(ch)) if ch[k] > 0 or k == nv_min})
    if short_bar is not None and nv_min > SHORT_NVIS and trials * CHANCE_MAX >= 1:
        ctl["short"] = {"visits": SHORT_NVIS, "bar_z_together": round(short_bar, 1), "trials": trials}
    zmaps = _zm(zmaps)
    plane = eorbit.Plane(inp["ra0"], inp["dec0"])
    n = zmaps.shape[1]
    cc = (n - 1) / 2.0
    out: list[dict[str, Any]] = []
    for r in tr:
        m = [ok[q] for q in r["idx"]]
        zs = [verified[c]["sig"] for c in m]
        zt = float(np.sum(zs) / np.sqrt(len(zs)))
        if zt < onoff.TRACK_NSIG:
            continue
        have = sorted({cands[c].frame for c in m})
        lo, hi = min(cands[c].t for c in m) - REC_PAD, max(cands[c].t for c in m) + REC_PAD
        rec: list[tuple[int, float, float, float]] = []
        rad = round(np.ceil(REC_R))
        for k in range(zmaps.shape[0]):
            if k in have or k not in obs or not (lo <= frame_t[k] <= hi):
                continue
            uu, vv = eorbit.sky(r["state"], np.array([obs[k][0]]), obs[k][1][None], plane)
            x, y = cc + float(uu[0]) / step, cc - float(vv[0]) / step
            ix, iy = round(x), round(y)
            if not (rad <= ix < n - rad and rad <= iy < n - rad):
                continue
            win = zmaps[k, iy - rad : iy + rad + 1, ix - rad : ix + rad + 1]
            if np.isfinite(win).any() and np.nanmax(win) >= REC_Z:
                rec.append((k, float(x), float(y), float(np.nanmax(win))))
        short_ok = "short" in ctl and len(have) + len(rec) == SHORT_NVIS and zt > ctl["short"]["bar_z_together"]
        if (len(have) + len(rec) < nv_min and not short_ok) or len(have) < ORBIT_FOUND_MIN:
            continue
        pts = [
            {"f": cands[c].frame, "x": round(cands[c].x, 2), "y": round(cands[c].y, 2), "z": round(cands[c].sn, 1), "how": "found"}
            for c in m
        ]
        pts += [{"f": k, "x": round(x, 2), "y": round(y, 2), "z": round(z, 1), "how": "recovered"} for k, x, y, z in rec]
        pts.sort(key=lambda p: frame_t[p["f"]])
        out.append(
            {
                "n": len(have) + len(rec),
                "n_found": len(have),
                "n_recovered": len(rec),
                "hours": round(r["hours"], 2),
                "rate": round(r["rate"], 2),
                "pa": round(r["pa"], 1),
                "rms": round(r["rms"], 2),
                "method": "orbit",
                "verified": True,
                "z_together": round(zt, 1),
                **({"short": True} if len(have) + len(rec) < nv_min else {}),  # drawn by its strength, not its length
                "dist_au": round(r["dist"], 2),
                "dist_range_au": [round(r["dist_lo"], 2), round(r["dist_hi"], 2)],
                "pts": pts,
            }
        )
    return out, ctl


def line_inputs(cands: Sequence[Candidate]) -> dict[str, Any]:
    """The strongest candidates for the straight-line linking: place, time and visit."""
    top = list(cands[:LINK_MAX])
    return {
        "t": np.array([q.t for q in top]),
        "u": np.array([q.east for q in top]),
        "v": np.array([q.north for q in top]),
        "vis": np.array([q.frame for q in top], float),
    }


def line_link(lin: dict[str, Any], frame_t: F64, zmaps: Any, step: float) -> list[dict[str, Any]]:
    """Straight-line tracks through the candidates, recovered on the motion maps."""
    return tracks_with_recovery(lin["t"], lin["u"], lin["v"], lin["vis"], frame_t, _zm(zmaps), step)


def line_trial(lin: dict[str, Any], half: float, frame_t: F64, zmaps: Any, step: float, k: int) -> list[int]:
    """One trial of the straight-line control: the candidates at chance places, linked and recovered like the real tracks.
    -> how many chance
    tracks of at least n visits (n = 0 .. 10)."""
    rng = np.random.default_rng(1 + k)
    uu, vv = rng.uniform(-half, half, len(lin["t"])), rng.uniform(-half, half, len(lin["t"]))
    c = [0] * 11
    for r in tracks_with_recovery(lin["t"], uu, vv, lin["vis"], frame_t, _zm(zmaps), step):
        if r["nvis"] >= NMIN_FOUND and r["rms"] <= RMS_MAX:
            for j in range(min(r["score"], 10) + 1):
                c[j] += 1
    return c


def assemble_search(
    cands: Sequence[Candidate],
    lines: list[dict[str, Any]],
    line_counts: Sequence[Sequence[int]],
    tracks: list[dict[str, Any]],
    ctl: dict[str, Any] | None,
    frame_t: F64,
    verification: dict[str, Any] | None,
    trials: int,
) -> dict[str, Any]:
    """The result of a film search: the orbit tracks, then the straight-line tracks that no orbit track took over (not verified), with the
    two controls."""
    out: dict[str, Any] = {
        "candidates": len(cands),
        "tracks": [],
        "control": ctl or {"trials": trials, "min_visits_drawn": None, "chance": {}},
    }
    if verification is not None:
        out["verification"] = verification
    top = list(cands[:LINK_MAX])
    ch = np.sum(np.array(line_counts, float), axis=0) / max(len(line_counts), 1) if line_counts else np.zeros(11)
    okn = [k for k in range(NMIN_FOUND, len(ch)) if ch[k] < CHANCE_MAX]
    nv_line = okn[0] if okn else len(ch)
    line_chance = {str(k): round(float(ch[k]), 3) for k in range(NMIN_FOUND, len(ch)) if ch[k] > 0 or k == nv_line}
    out["control"]["line_chance"] = line_chance
    if out["control"]["min_visits_drawn"] is None:  # no orbit search (no verification): the straight-line control is the control
        out["control"].update(min_visits_drawn=nv_line, chance=line_chance)
    taken = {(p["f"], round(p["x"], 1), round(p["y"], 1)) for tr in tracks for p in tr["pts"] if p["how"] == "found"}
    for tr in lines:
        if tr["nvis"] < NMIN_FOUND or tr["score"] < nv_line or tr["rms"] > RMS_MAX:
            continue
        pts = [
            {"f": top[q].frame, "x": round(top[q].x, 2), "y": round(top[q].y, 2), "z": round(top[q].sn, 1), "how": "found"}
            for q in tr["idx"]
        ]
        if {(p["f"], round(p["x"], 1), round(p["y"], 1)) for p in pts} & taken:
            continue
        pts += [{"f": int(k), "x": round(x, 2), "y": round(y, 2), "z": round(z, 1), "how": "recovered"} for k, x, y, z in tr["rec"]]
        pts.sort(key=lambda p: frame_t[p["f"]])
        zf = [p["z"] for p in pts if p["how"] == "found"]
        tracks.append(
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
                "z_together": round(float(np.sum(zf) / np.sqrt(len(zf))), 1),
                "pts": pts,
            }
        )
    out["tracks"] = tracks
    return out


async def search_staged(
    run: Callable[..., Awaitable[Any]],
    frames: Sequence[Frame],
    cands: Sequence[Candidate],
    step_arcsec: float,
    n: int,
    verified: dict[int, dict[str, Any]] | None = None,
    obs: dict[int, tuple[float, F64]] | None = None,
    ra0: float = 0.0,
    dec0: float = 0.0,
    trials: int = CONTROL_TRIALS,
    progress: Callable[[str, int, int], None] | None = None,
) -> dict[str, Any]:
    """The whole search of a film in stages, each a call `run(fn, *args)` of a module-level function (in the service a worker process: the
    stages are a few seconds each and the control trials run side by side). `run` may also just call fn."""
    import asyncio
    import shutil
    import tempfile

    out: dict[str, Any] = {"candidates": len(cands), "tracks": [], "control": {"trials": trials, "min_visits_drawn": None, "chance": {}}}
    if len(cands) < 3:
        return out

    def say(msg: str, i: int = 0, total: int = 0) -> None:
        if progress is not None:
            progress(msg, i, total)

    work = tempfile.mkdtemp(prefix="filmsearch_")
    try:
        zpath = f"{work}/zmaps.npy"
        say("keeping the motion maps")
        frame_t = await run(save_zmaps, frames, n, zpath)
        tracks: list[dict[str, Any]] = []
        ctl: dict[str, Any] | None = None
        verification: dict[str, Any] | None = None
        if verified is not None and obs is not None:
            inp = await run(orbit_inputs, cands, verified, obs, ra0, dec0)
            say("linking the verified candidates by an orbit")
            raw = await run(orbit_link, inp)
            counts: list[list[float]] = []
            if len(inp["ok"]) >= 3:
                done = 0

                async def one(k: int) -> list[float]:
                    nonlocal done
                    r: list[float] = await run(orbit_trial, inp, k)
                    done += 1
                    say("control: the same candidates at chance places", done, ORBIT_TRIALS)
                    return r

                counts = list(await asyncio.gather(*(one(k) for k in range(ORBIT_TRIALS))))
            say("following the tracks into the other visits")
            tracks, ctl = await run(orbit_assemble, inp, raw, counts, cands, verified, obs, frame_t, zpath, step_arcsec)
            verification = {
                "measured": len(verified),
                "verified_alone": sum(1 for v in verified.values() if v["ok"]),
                "linked": ctl["verified_candidates"],
            }
        lin = await run(line_inputs, cands)
        say("straight-line tracks through the strongest candidates")
        lines = await run(line_link, lin, frame_t, zpath, step_arcsec)
        half = n * step_arcsec / 2.0
        done_l = 0

        async def one_line(k: int) -> list[int]:
            nonlocal done_l
            r: list[int] = await run(line_trial, lin, half, frame_t, zpath, step_arcsec, k)
            done_l += 1
            say("control: straight lines at chance places", done_l, trials)
            return r

        line_counts = list(await asyncio.gather(*(one_line(k) for k in range(trials))))
        final: dict[str, Any] = await run(assemble_search, cands, lines, line_counts, tracks, ctl, frame_t, verification, trials)
        return final
    finally:
        shutil.rmtree(work, ignore_errors=True)


def search(
    frames: Sequence[Frame],
    cands: Sequence[Candidate],
    step_arcsec: float,
    n: int,
    verified: dict[int, dict[str, Any]] | None = None,
    obs: dict[int, tuple[float, F64]] | None = None,
    ra0: float = 0.0,
    dec0: float = 0.0,
    trials: int = CONTROL_TRIALS,
) -> dict[str, Any]:
    """The whole search of a film in this process (tools and tests; the service runs `search_staged` in worker processes). Track points are
    in the film's grid (x column, y row, row 0 north)."""
    import asyncio

    async def inline(fn: Callable[..., Any], *args: Any) -> Any:
        return fn(*args)

    result: dict[str, Any] = asyncio.run(search_staged(inline, frames, cands, step_arcsec, n, verified, obs, ra0, dec0, trials))
    return result


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
