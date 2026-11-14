"""Search between passes: sources that move in a straight line over the seasons (docs/plan/02-methods/2.5-searches.md 2.5.1–2.5.2).

Input: the white stack of every pass (field grid, NaN where the pass has no data) and its saturated share. Nothing about any object is
given to the search; a catalogue check comes afterwards (service/movers.py).

    sources per pass  peaks of the stack smoothed to a point source (FWHM 7″) in a 3·FWHM box, above the local background by 8 MAD;
                      a saturated zone (at least one detector pixel) is one source at its centre; only where every pass has data and
                      20″ in from the edge
    stationary        a source with a source within 2.5″ in every other pass
    tracks            the first and the last pass fix the motion (3–120 ″/yr); a source within 2.5″ of the predicted place, with
                      brightness within a factor 2.5, in at least one pass between makes a track of ≥ 3 passes; the motion is refitted
                      on the members; tracks through the same sources are merged

Positions are in field-grid pixels (x to the west, y to the south); motions are reported east and north in ″/yr.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import binary_dilation, center_of_mass, gaussian_filter, label, maximum_filter, median_filter, zoom
from scipy.spatial import cKDTree

from engine.grid import NATIVE_PIXEL_ARCSEC

FWHM_ARCSEC = 7.0
BOX_FWHM = 3.0
NSIG = 8.0
EDGE_ARCSEC = 20.0
MATCH_ARCSEC = 2.5
V_MIN, V_MAX = 3.0, 120.0  # ″/yr
FLUX_FACTOR = 2.5
WEAK_SNR = 15.0
MAX_SOURCES = 4000


@dataclass
class Source:
    x: float
    y: float
    flux: float
    snr: float
    saturated: bool


@dataclass
class Track:
    passes: list[int]
    xy: list[tuple[float, float]]  # grid position in each member pass
    snr: list[float]
    saturated: bool
    mjd: list[float]
    v_east: float = 0.0  # ″/yr
    v_north: float = 0.0
    rms_arcsec: float = 0.0
    ref_mjd: float = 0.0  # epoch of (x0, y0)
    x0: float = 0.0
    y0: float = 0.0
    weak: bool = False

    @property
    def speed(self) -> float:
        return float(np.hypot(self.v_east, self.v_north))

    @property
    def pa(self) -> float:
        """Direction of motion, degrees east of north."""
        return float(np.degrees(np.arctan2(self.v_east, self.v_north)) % 360.0)


@dataclass
class Result:
    sources_per_pass: list[int]
    stationary: int
    tracks: list[Track] = field(default_factory=list)
    weak: int = 0
    still_sources: list[Source] = field(default_factory=list)  # the stationary sources of the first pass (the anomaly search's input)


def _smooth(img: NDArray[np.floating], sigma: float) -> NDArray[np.float64]:
    """Gaussian smoothing that ignores missing pixels (normalised by the smoothed coverage)."""
    ok = np.isfinite(img)
    num = gaussian_filter(np.where(ok, img, 0.0).astype(np.float64), sigma)
    den = gaussian_filter(ok.astype(np.float64), sigma)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = num / den
    out[den < 0.3] = np.nan
    return np.asarray(out, dtype=np.float64)


def _background(sm: NDArray[np.float64], block: int) -> NDArray[np.float64]:
    """Local background: the median in blocks of `block` pixels, smoothed back to full size."""
    n = sm.shape[0]
    nb = max(1, n // block)
    filled = np.where(np.isfinite(sm), sm, np.nanmedian(sm))
    small = np.array(
        [[np.median(filled[i * n // nb : (i + 1) * n // nb, j * n // nb : (j + 1) * n // nb]) for j in range(nb)] for i in range(nb)]
    )
    small = median_filter(small, size=3, mode="nearest")
    return np.asarray(zoom(small, n / nb, order=1, mode="nearest"))[:n, :n]


def find_sources(white: NDArray[np.floating], sat: NDArray[np.floating], step_arcsec: float) -> list[Source]:
    sigma = FWHM_ARCSEC / 2.3548 / step_arcsec
    sm = _smooth(white, sigma)
    bg = _background(sm, max(16, round(60.0 / step_arcsec)))
    resid = sm - bg
    good = np.isfinite(resid)
    if good.sum() < 100:
        return []
    mad = 1.4826 * float(np.median(np.abs(resid[good] - np.median(resid[good])))) or 1e-9
    box = max(3, round(BOX_FWHM * FWHM_ARCSEC / step_arcsec) | 1)
    r0 = np.where(good, resid, -np.inf)
    peaks = (r0 == maximum_filter(r0, size=box, mode="nearest")) & (r0 > NSIG * mad)
    # saturated zones: one source each, at the zone's centre
    native = NATIVE_PIXEL_ARCSEC / step_arcsec
    zones, nz = label(sat > 0.5)
    out: list[Source] = []
    if nz:
        sizes = np.bincount(zones.ravel())
        keep = [z for z in range(1, nz + 1) if sizes[z] >= native * native * 0.5]
        if keep:
            cover = binary_dilation(np.isin(zones, keep), iterations=round(2 * native))
            peaks &= ~cover
            # weighted by the saturated share: the core, saturated in every exposure, counts more than the rim
            for cy, cx in center_of_mass(np.clip(sat, 0, 1), zones, keep):
                out.append(Source(float(cx), float(cy), float("inf"), float("inf"), True))
    ys, xs = np.nonzero(peaks)
    for y, x in zip(ys, xs, strict=True):
        dx = dy = 0.0
        if 0 < x < sm.shape[1] - 1 and 0 < y < sm.shape[0] - 1:
            lf, c, r = resid[y, x - 1], resid[y, x], resid[y, x + 1]
            u, d = resid[y - 1, x], resid[y + 1, x]
            if np.isfinite([lf, c, r, u, d]).all():
                den_x, den_y = lf - 2 * c + r, u - 2 * c + d
                dx = float(np.clip(0.5 * (lf - r) / den_x, -0.5, 0.5)) if den_x < 0 else 0.0
                dy = float(np.clip(0.5 * (u - d) / den_y, -0.5, 0.5)) if den_y < 0 else 0.0
        out.append(Source(float(x + dx), float(y + dy), float(resid[y, x]), float(resid[y, x] / mad), False))
    out.sort(key=lambda s: (not s.saturated, -s.snr))
    return out[:MAX_SOURCES]


def _fit_line(t: NDArray[np.float64], p: NDArray[np.float64]) -> tuple[float, float, float]:
    """Least-squares p = a + b·t; (a, b, rms)."""
    design = np.vstack([np.ones_like(t), t]).T
    coef, *_ = np.linalg.lstsq(design, p, rcond=None)
    rms = float(np.sqrt(np.mean((p - design @ coef) ** 2)))
    return float(coef[0]), float(coef[1]), rms


def search(whites: list[NDArray[np.floating]], sats: list[NDArray[np.floating]], mjds: list[float], step_arcsec: float) -> Result:
    npass = len(whites)
    n = whites[0].shape[0]
    covered = np.logical_and.reduce([np.isfinite(w) for w in whites])
    edge = int(np.ceil(EDGE_ARCSEC / step_arcsec))
    inner = np.zeros_like(covered)
    inner[edge : n - edge, edge : n - edge] = True
    usable = covered & inner
    srcs: list[list[Source]] = []
    for w, s in zip(whites, sats, strict=True):
        found = find_sources(w, s, step_arcsec)
        srcs.append([q for q in found if usable[min(n - 1, max(0, round(q.y))), min(n - 1, max(0, round(q.x)))]])
    if npass < 3:
        return Result([len(s) for s in srcs], 0)
    years = (np.asarray(mjds, float) - mjds[0]) / 365.25
    match_px = MATCH_ARCSEC / step_arcsec
    xy = [np.array([[q.x, q.y] for q in s]) if s else np.zeros((0, 2)) for s in srcs]
    trees = [cKDTree(a) if len(a) else None for a in xy]

    def near(k: int, p: NDArray[np.float64], r: float) -> list[int]:
        t = trees[k]
        return [] if t is None else list(t.query_ball_point(p, r))

    stationary = [np.zeros(len(s), bool) for s in srcs]
    for k in range(npass):
        for i, p in enumerate(xy[k]):
            stationary[k][i] = all(near(j, p, match_px) for j in range(npass) if j != k)
    first, last = 0, npass - 1
    span = years[last] - years[first]
    tracks: list[Track] = []
    if span > 0.2:
        for i, a in enumerate(xy[first]):
            if stationary[first][i]:
                continue
            rmax = V_MAX * span / step_arcsec
            for j in near(last, a, rmax):
                if stationary[last][j]:
                    continue
                b = xy[last][j]
                v = np.hypot(*(b - a)) * step_arcsec / span
                if not V_MIN <= v <= V_MAX:
                    continue
                sa, sb = srcs[first][i], srcs[last][j]
                if sa.saturated != sb.saturated:
                    continue
                members = {first: i, last: j}
                for k in range(1, npass - 1):
                    f = (years[k] - years[first]) / span
                    pred = a + f * (b - a)
                    best, bd = None, match_px
                    for m in near(k, pred, match_px):
                        q = srcs[k][m]
                        if q.saturated != sa.saturated:
                            continue
                        if not sa.saturated and not (1 / FLUX_FACTOR < q.flux / max(sa.flux, 1e-12) < FLUX_FACTOR):
                            continue
                        d = float(np.hypot(*(xy[k][m] - pred)))
                        if d < bd:
                            best, bd = m, d
                    if best is not None:
                        members[k] = best
                if len(members) < 3:
                    continue
                ks = sorted(members)
                pts = np.array([xy[k][members[k]] for k in ks])
                t = years[ks]
                x0, vx, rx = _fit_line(t, pts[:, 0])
                y0, vy, ry = _fit_line(t, pts[:, 1])
                snrs = [srcs[k][members[k]].snr for k in ks]
                tr = Track(
                    passes=ks,
                    xy=[(float(p[0]), float(p[1])) for p in pts],
                    snr=[float(s) for s in snrs],
                    saturated=sa.saturated,
                    mjd=[float(mjds[k]) for k in ks],
                    v_east=float(-vx * step_arcsec),
                    v_north=float(-vy * step_arcsec),
                    rms_arcsec=float(np.hypot(rx, ry) * step_arcsec),
                    ref_mjd=float(mjds[0]),
                    x0=x0,
                    y0=y0,
                )
                tr.weak = len(ks) == 3 and min(snrs) < WEAK_SNR
                tracks.append(tr)
    # tracks through the same sources: keep the one with more passes, then the smaller scatter
    tracks.sort(key=lambda t: (-len(t.passes), t.rms_arcsec))
    kept: list[Track] = []
    used: set[tuple[int, float, float]] = set()
    for cand in tracks:
        ids = {(k, round(p[0], 2), round(p[1], 2)) for k, p in zip(cand.passes, cand.xy, strict=True)}
        if ids & used:
            continue
        used |= ids
        kept.append(cand)
    weak = sum(1 for t in kept if t.weak)
    still = [q for q, st in zip(srcs[0], stationary[0], strict=True) if st]
    return Result([len(s) for s in srcs], len(still), [t for t in kept if not t.weak], weak, still)
