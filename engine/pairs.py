# ruff: noqa: E501
# mypy: ignore-errors
"""Pairs that change in place (docs/plan/02-methods/2.5-searches.md): two stars closer than the PSF can follow whose brightness ratio changes from pass to pass.

A pair of sources 3.5–12″ apart (too close for the search between passes, which follows peaks) is measured in the white stack of every pass: the amplitudes of two
Gaussian components at FIXED places and a flat background (linear least squares), with their covariance (two overlapping PSFs are anti-correlated, which makes the
ratio less certain than the two errors alone say). A pair is a find when
    - both components are measured in every pass with flux/error at least `MIN_SN` each time (neither is absent in a pass),
    - each reaches `MAX_SN` in at least one pass, and
    - the ln of the ratio of the two changes between passes by at least `CHANGE_SIGMA` of its own error.
The chance control: the same measurement and test on pairs of which neither component changes (steady pairs); the share that passes times the number of
candidate pairs (a component changes at 2.5σ or more) is the number of finds the test makes by chance per field.

Real pixels only: nothing is filled; the amplitudes are fitted to the stacks the page shows.
"""

from __future__ import annotations

import base64
import math
from collections.abc import Sequence
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import binary_dilation, binary_erosion, gaussian_filter, map_coordinates, maximum_filter
from scipy.optimize import minimize
from scipy.stats import chi2

FWHM_PSF = 7.0  # arcsec: the instrument's star
FWHM_STACK = 10.0  # arcsec: the width of a star in the stacks (the fit's Gaussian: the PSF plus what stacking adds)
THRESHOLD = 8.0  # robust σ of the smoothed stack for a source
NMS_FWHM = 0.8  # half-width of the non-maximum suppression in FWHM: close neighbours stay apart
EDGE_ARCSEC = 20.0
SEP_MIN, SEP_MAX = 3.5, 12.0  # arcsec
MIN_SN, MAX_SN = 10.0, 15.0
CHANGE_SIGMA = 3.5
COMPONENT_CHANGE_SIGMA = 2.5  # a component whose own flux changes by this many σ makes its pair a candidate
MAX_FINDS = 12
CHANCE_TESTS = 150
MAX_SOURCES = 4000
NATIVE_PIXEL_ARCSEC = 6.15

F64 = NDArray[np.float64]


def find_sources(
    white: NDArray[np.floating], sat: NDArray[np.floating], valid: NDArray[np.bool_], step: float
) -> list[tuple[float, float, float]]:
    """Sources of one pass stack: rows (x, y, S/N) in grid points — peaks of the stack smoothed to a point source, above the median by `THRESHOLD` robust σ, only where
    `valid`, none inside a saturated zone (its halo is not a pair)."""
    sg = FWHM_PSF / 2.355 / step
    img = np.where(np.isfinite(white), white, 0.0).astype(np.float64)
    sm = gaussian_filter(img, sg * 0.7)
    ref = sm[valid] if valid.any() else sm
    med = float(np.median(ref))
    nz = 1.4826 * float(np.median(np.abs(ref - med))) or 1e-9
    box = int(2 * round(NMS_FWHM * FWHM_PSF / step) + 1)
    pk = (sm == maximum_filter(sm, box)) & (sm > med + THRESHOLD * nz) & valid
    native = NATIVE_PIXEL_ARCSEC / step
    satz = binary_dilation(sat > 0.5, iterations=max(1, round(3 * native))) if (sat > 0.5).any() else np.zeros_like(valid)
    pk &= ~satz
    ys, xs = np.nonzero(pk)
    out = []
    for y, x in zip(ys, xs, strict=True):
        dx = dy = 0.0
        if 0 < x < sm.shape[1] - 1 and 0 < y < sm.shape[0] - 1:
            lf, c, r = sm[y, x - 1], sm[y, x], sm[y, x + 1]
            u, d = sm[y - 1, x], sm[y + 1, x]
            den_x, den_y = lf - 2 * c + r, u - 2 * c + d
            dx = float(np.clip(0.5 * (lf - r) / den_x, -0.5, 0.5)) if den_x < 0 else 0.0
            dy = float(np.clip(0.5 * (u - d) / den_y, -0.5, 0.5)) if den_y < 0 else 0.0
        out.append((float(x + dx), float(y + dy), float((sm[y, x] - med) / nz)))
    out.sort(key=lambda s: -s[2])
    return out[:MAX_SOURCES]


def merge_sources(
    per_pass: Sequence[list[tuple[float, float, float]]], step: float, tol_arcsec: float = 2.5
) -> list[tuple[float, float, float]]:
    """Sources of all passes as one list: a source of another pass within `tol_arcsec` of a stronger one is the same source."""
    allp = sorted((s for p in per_pass for s in p), key=lambda s: -s[2])
    out: list[tuple[float, float, float]] = []
    if not allp:
        return out
    tol = tol_arcsec / step
    cell = max(tol, 1.0)
    grid: dict[tuple[int, int], list[int]] = {}
    for s in allp:
        gx, gy = int(s[0] // cell), int(s[1] // cell)
        if any(
            math.hypot(out[k][0] - s[0], out[k][1] - s[1]) < tol
            for a in (-1, 0, 1)
            for b in (-1, 0, 1)
            for k in grid.get((gx + a, gy + b), [])
        ):
            continue
        grid.setdefault((gx, gy), []).append(len(out))
        out.append(s)
    return out


def close_pairs(src: Sequence[tuple[float, float, float]], step: float) -> list[tuple[int, int]]:
    """Index pairs of sources 3.5–12″ apart (the western one first: the lower x)."""
    if len(src) < 2:
        return []
    xy = np.array([[s[0], s[1]] for s in src])
    out = []
    for i in range(len(src)):
        d = np.hypot(xy[:, 0] - xy[i, 0], xy[:, 1] - xy[i, 1]) * step
        for j in np.where((d >= SEP_MIN) & (d <= SEP_MAX) & (np.arange(len(src)) > i))[0]:
            a, b = (i, int(j)) if xy[i, 0] <= xy[j, 0] else (int(j), i)
            out.append((a, b))
    return out


def fit_pair(img: NDArray[np.floating], xa: float, ya: float, xb: float, yb: float, step: float) -> dict[str, float] | None:
    """Amplitudes (a, b) of two Gaussian components of width `FWHM_STACK` at the grid places (xa, ya), (xb, yb) and a flat background, fitted to the pixels of `img`
    (NaN skipped) within a window around the pair's middle. -> a, b, bg, ea, eb, cov_ab, chi2, n; None when too few pixels."""
    n = img.shape[0]
    cx, cy = 0.5 * (xa + xb), 0.5 * (ya + yb)
    sg = FWHM_STACK / 2.355 / step
    h = max(16.0, 1.6 * math.hypot(xa - xb, ya - yb) * step + 2 * FWHM_STACK) / step
    x0, x1, y0, y1 = int(max(0, cx - h)), int(min(n, cx + h + 1)), int(max(0, cy - h)), int(min(n, cy + h + 1))
    yy, xx = np.mgrid[y0:y1, x0:x1]
    z = img[y0:y1, x0:x1]
    ok = np.isfinite(z)
    if ok.sum() < 30:
        return None
    ga = np.exp(-((xx - xa) ** 2 + (yy - ya) ** 2) / (2 * sg**2))
    gb = np.exp(-((xx - xb) ** 2 + (yy - yb) ** 2) / (2 * sg**2))
    m = np.stack([ga[ok], gb[ok], np.ones(int(ok.sum()))], 1)
    d = z[ok].astype(np.float64)
    try:
        co = np.linalg.lstsq(m, d, rcond=None)[0]
        r = d - m @ co
        s2 = float(np.sum(r**2) / max(len(d) - 3, 1))
        cov = np.linalg.inv(m.T @ m) * s2
    except np.linalg.LinAlgError:
        return None
    return {
        "a": float(co[0]),
        "b": float(co[1]),
        "bg": float(co[2]),
        "ea": math.sqrt(max(cov[0, 0], 0.0)),
        "eb": math.sqrt(max(cov[1, 1], 0.0)),
        "cov_ab": float(cov[0, 1]),
        "chi2": s2,
        "n": float(ok.sum()),
    }


def refine_pair(
    img: NDArray[np.floating], pa: tuple[float, float], pb: tuple[float, float], step: float
) -> tuple[tuple[float, float], tuple[float, float]]:
    """The places of the two components that fit `img` (the mean of the passes) best: the peaks of a blend are pulled together, so the places start at the peaks and are moved
    (Nelder-Mead on the four coordinates, amplitudes and background solved linearly at every step). The places stay fixed for every pass afterwards. The start is kept when
    the fit does not improve, when a component moves more than 1.5 FWHM or when the two come closer than `SEP_MIN` / 2 or go beyond `SEP_MAX` x 1.5."""

    def cost(v: NDArray[np.float64]) -> float:
        f = fit_pair(img, v[0], v[1], v[2], v[3], step)
        return float("inf") if f is None else f["chi2"]

    x0 = np.array([pa[0], pa[1], pb[0], pb[1]], float)
    c0 = cost(x0)
    if not math.isfinite(c0):
        return pa, pb
    r = minimize(
        cost,
        x0,
        method="Nelder-Mead",
        options={"initial_simplex": np.vstack([x0, *(x0 + 1.0 * np.eye(4))]), "xatol": 0.05, "fatol": 1e-9, "maxiter": 200},
    )
    v = r.x
    lim = 1.5 * FWHM_STACK / step
    sep = math.hypot(v[0] - v[2], v[1] - v[3]) * step
    if r.fun >= c0 or max(abs(v - x0)) > lim or not (SEP_MIN / 2 <= sep <= SEP_MAX * 1.5):
        return pa, pb
    return (float(v[0]), float(v[1])), (float(v[2]), float(v[3]))


def measure_pair(
    whites: Sequence[NDArray[np.floating]],
    dets: Sequence[dict[str, NDArray[np.floating]]],
    pa: tuple[float, float],
    pb: tuple[float, float],
    step: float,
    min_change: float = 0.10,
    spread_max: float = 0.15,
) -> dict[str, Any]:
    """The pair at fixed places in every pass and every detector: flux_white [[a, b, ea, eb, cov] per pass], ratio, flux_detector {name: [[a, b, ea, eb] per pass]},
    dln_ratio {name: [ln(R_p / R_0) per pass]}, and the verdict: 'component varies (same change in every detector)' when the ratio changes by more than `min_change` (ln)
    by about the same amount in every detector that has both passes (spread below `spread_max`), 'change depends on the detector' when it changes but differs between
    detectors (the passes' wavelength mix differs), 'steady' when it does not change."""
    fw: list[list[float] | None] = []
    ratio: list[float | None] = []
    for w in whites:
        f = fit_pair(np.asarray(w, float), pa[0], pa[1], pb[0], pb[1], step)
        fw.append(None if f is None else [round(f["a"], 4), round(f["b"], 4), round(f["ea"], 4), round(f["eb"], 4), round(f["cov_ab"], 6)])
        ratio.append(None if f is None or f["a"] <= 0 or f["b"] <= 0 else round(f["b"] / f["a"], 3))
    names = sorted({k for d in dets for k in d})
    fd: dict[str, list[list[float] | None]] = {}
    dl: dict[str, list[float | None]] = {}
    for nme in names:
        rows: list[list[float] | None] = []
        rr: list[float | None] = []
        for d in dets:
            st = d.get(nme)
            f = None if st is None else fit_pair(np.asarray(st, float), pa[0], pa[1], pb[0], pb[1], step)
            rows.append(None if f is None else [round(f["a"], 4), round(f["b"], 4), round(f["ea"], 4), round(f["eb"], 4)])
            rr.append(None if f is None or f["a"] <= 0 or f["b"] <= 0 else f["b"] / f["a"])
        fd[nme] = rows
        base = next((r for r in rr if r), None)
        dl[nme] = [None if (r is None or base is None) else round(math.log(r / base), 3) for r in rr]
    ch: list[tuple[int, float, float]] = []
    for e in range(1, len(whites)):
        vals = [v[e] for v in dl.values() if v[e] is not None and v[0] is not None]
        if len(vals) >= 2:
            ch.append((e, float(np.mean(vals)), float(np.std(vals))))
    if not ch:
        return {
            "flux_white": fw,
            "ratio": ratio,
            "flux_detector": fd,
            "dln_ratio": dl,
            "verdict": "not enough detectors",
            "spread": None,
            "mean_change": None,
        }
    big = max(ch, key=lambda c: abs(c[1]))
    spread = max(c[2] for c in ch)
    if abs(big[1]) < min_change and spread <= spread_max:
        verdict = "steady"
    elif spread <= spread_max:
        verdict = "component varies (same change in every detector)"
    else:
        verdict = "change depends on the detector (a variable component with a colour, or a different wavelength mix in the passes: compare the two spectra pass by pass)"
    return {
        "flux_white": fw,
        "ratio": ratio,
        "flux_detector": fd,
        "dln_ratio": dl,
        "verdict": verdict,
        "spread": round(spread, 3),
        "mean_change": round(big[1], 3),
    }


def change_sigma(flux_white: Sequence[Sequence[float] | None]) -> tuple[float, bool]:
    """(the largest change of ln(B/A) between passes in σ of its error, whether both components pass the S/N tests). The error includes the covariance of the amplitudes."""
    ok_p = [p for p, f in enumerate(flux_white) if f is not None and f[2] > 0 and f[3] > 0 and f[0] > 0 and f[1] > 0]
    if len(ok_p) < 2 or len(ok_p) < len(flux_white):
        return 0.0, False
    sn = [[flux_white[p][k] / flux_white[p][2 + k] for p in ok_p] for k in (0, 1)]
    passes_tests = not any(min(x) < MIN_SN or max(x) < MAX_SN for x in sn)
    lr, sg = [], []
    for p in ok_p:
        a, b, ea, eb = flux_white[p][:4]
        cov = flux_white[p][4] if len(flux_white[p]) > 4 else 0.0
        lr.append(math.log(b / a))
        sg.append(math.sqrt(max((ea / a) ** 2 + (eb / b) ** 2 - 2.0 * cov / (a * b), 1e-12)))
    z = max(abs(lr[i] - lr[0]) / math.hypot(sg[i], sg[0]) for i in range(1, len(ok_p)))
    return z, passes_tests


def pair_is_real(flux_white: Sequence[Sequence[float] | None]) -> bool:
    z, tests = change_sigma(flux_white)
    return tests and z >= CHANGE_SIGMA


def component_changes(flux_white: Sequence[Sequence[float] | None]) -> bool:
    """Does either component's own flux change between passes by at least `COMPONENT_CHANGE_SIGMA` σ (the pair is then a candidate)?"""
    ok = [f for f in flux_white if f is not None and f[0] > 0 and f[1] > 0 and f[2] > 0 and f[3] > 0]
    if len(ok) < 2:
        return False
    for k in (0, 1):
        v0, e0 = ok[0][k], ok[0][2 + k]
        for f in ok[1:]:
            if abs(math.log(f[k] / v0)) / math.hypot(f[2 + k] / f[k], e0 / v0) >= COMPONENT_CHANGE_SIGMA:
                return True
    return False


def crops(whites: Sequence[NDArray[np.floating] | None], pos: Sequence[tuple[float, float]], step: float, size: int = 64) -> dict[str, Any]:
    """The same piece of sky in every pass around the pair as uint8 (0 = no data, 1..255 an asinh stretch common to the passes), base64 per pass."""
    p = np.array(pos, float)
    cx, cy = float(p[:, 0].mean()), float(p[:, 1].mean())
    ext = float(max(np.ptp(p[:, 0]), np.ptp(p[:, 1])))
    half = max(18.0, 0.65 * ext + 14.0)
    jj, ii = np.mgrid[0:size, 0:size]
    xs = cx + (ii - (size - 1) / 2) / (size - 1) * 2 * half
    ys = cy + (jj - (size - 1) / 2) / (size - 1) * 2 * half  # row 0 is north
    cs: list[NDArray[np.float32] | None] = []
    for w in whites:
        cs.append(
            None
            if w is None
            else np.asarray(map_coordinates(np.asarray(w, float), [ys, xs], order=1, mode="constant", cval=np.nan), np.float32)
        )
    fin = np.concatenate([c[np.isfinite(c)] for c in cs if c is not None] or [np.zeros(1, np.float32)])
    lo, hi = (float(np.percentile(fin, 2)), float(np.percentile(fin, 99.7))) if fin.size > 20 else (0.0, 1.0)
    hi = max(hi, lo + 1e-6)
    data: list[str | None] = []
    for c in cs:
        if c is None:
            data.append(None)
            continue
        v = np.clip((np.nan_to_num(c, nan=lo) - lo) / (hi - lo), 0, 1)
        u = np.where(np.isfinite(c), 1 + np.round(np.arcsinh(v / 0.08) / np.arcsinh(1 / 0.08) * 254), 0).astype(np.uint8)
        data.append(base64.b64encode(u.tobytes()).decode())
    return {"box_arcsec": round(2 * half * step, 1), "size": size, "data": data}


def poisson_upper95(k: int, n: int) -> float:
    return float(chi2.ppf(0.95, 2 * (k + 1)) / 2.0) / max(n, 1)


def analyse(
    whites: Sequence[NDArray[np.floating]],
    sats: Sequence[NDArray[np.floating]],
    dets: Sequence[dict[str, NDArray[np.floating]]],
    step: float,
    ntest: int = CHANCE_TESTS,
    seed: int = 11,
) -> dict[str, Any]:
    """The whole pair search of a finished field: every close pair of sources measured in every pass; the finds (pair_is_real, strongest first, at most 12) with their
    measurements and crops; and the chance control. Positions are field-grid points (x column, y row; row 0 north)."""
    full = np.all([np.isfinite(w) for w in whites], axis=0)
    valid = binary_erosion(full, iterations=max(1, round(EDGE_ARCSEC / step)))
    per_pass = [find_sources(w, s, valid, step) for w, s in zip(whites, sats, strict=True)]
    src = merge_sources(per_pass, step)
    pairs = close_pairs(src, step)
    mean_img = np.where(full, np.mean([np.nan_to_num(np.asarray(w, float)) for w in whites], axis=0), np.nan)
    places: dict[tuple[int, int], tuple[tuple[float, float], tuple[float, float]]] = {
        (a, b): refine_pair(mean_img, (src[a][0], src[a][1]), (src[b][0], src[b][1]), step) for a, b in pairs
    }
    cand: list[tuple[int, int]] = []
    steady: list[tuple[int, int]] = []
    finds: list[tuple[float, int, int, dict[str, Any]]] = []
    for a, b in pairs:
        pa, pb = places[(a, b)]
        fw = []
        for w in whites:
            f = fit_pair(np.asarray(w, float), pa[0], pa[1], pb[0], pb[1], step)
            fw.append(None if f is None else [f["a"], f["b"], f["ea"], f["eb"], f["cov_ab"]])
        if component_changes(fw):
            cand.append((a, b))
        else:
            steady.append((a, b))
        if pair_is_real(fw):
            z, _ = change_sigma(fw)
            finds.append((z, a, b, {}))
    finds.sort(key=lambda t: -t[0])
    out: list[dict[str, Any]] = []
    for z, a, b, _ in finds[:MAX_FINDS]:
        pa, pb = places[(a, b)]
        rep = measure_pair(whites, dets, pa, pb, step)
        rep.update(
            {
                "pa": pa,
                "pb": pb,
                "separation_arcsec": round(math.hypot(pa[0] - pb[0], pa[1] - pb[1]) * step, 1),
                "change_sigma": round(z, 2),
                "snr": [round(src[a][2], 1), round(src[b][2], 1)],
                "crops": crops(whites, [pa, pb], step),
            }
        )
        out.append(rep)
    rng = np.random.default_rng(seed)
    chance: dict[str, Any] = {
        "candidates": len(cand),
        "steady_pairs": len(steady),
        "tested": 0,
        "passed": 0,
        "rate": None,
        "expected": None,
        "expected_upper95": None,
    }
    if len(steady) < 10:
        chance["expected_upper95"] = float(len(cand))
    else:
        pick = [steady[k] for k in rng.permutation(len(steady))[:ntest]]
        ok = 0
        for a, b in pick:
            fw = []
            for w in whites:
                pa, pb = places[(a, b)]
                f = fit_pair(np.asarray(w, float), pa[0], pa[1], pb[0], pb[1], step)
                fw.append(None if f is None else [f["a"], f["b"], f["ea"], f["eb"], f["cov_ab"]])
            ok += 1 if pair_is_real(fw) else 0
        n = len(pick)
        up = poisson_upper95(ok, n)
        chance.update(
            tested=n,
            passed=ok,
            rate=round(ok / n, 4),
            expected=round(ok / n * len(cand), 3),
            expected_upper95=round(up * len(cand), 2),
            rate_upper95=round(up, 4),
        )
    return {
        "finds": out,
        "chance_pairs": chance,
        "sources": len(src),
        "close_pairs": len(pairs),
        "per_pass_sources": [len(p) for p in per_pass],
    }
