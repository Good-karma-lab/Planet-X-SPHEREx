"""The products of one pass of a field (docs/plan/02-methods/2.2-cleaning-and-stacks.md, 2.2.7–2.2.10).

For one pass: per detector the leave-one-out test and the stack; the per-visit test on fields of 12′ or less; the named-band stacks from
the surviving pixels (each pixel goes into a band only if its own wavelength lies in one of the band's windows); the saturated share over
the detectors of the white frame (CDET); the white frame (mean of the CDET stacks) and the colour channels (3 × 6 matrix); the flag
census. The smoothed sums (NUM, DEN) are kept so that registration can shift them with a cubic spline and divide afterwards.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from engine.bands import Band
from engine.colour import colour_matrix
from engine.grid import NATIVE_PIXEL_ARCSEC, Grid
from engine.register import Registration, shift_sums
from engine.stack import Exposure, deposit, sigma_grid, smooth, stack_with_rejection
from engine.visit_test import MAX_FOV_ARCMIN, visit_test

CENSUS_MIN_SHARE = 0.02
QUICK_PER_DETECTOR = {10.0: 4, 20.0: 3, 1e9: 2}  # fields up to 10′: 4 exposures per pass and detector; up to 20′: 3; above: 2
QUICK_SIGMA_NATIVE = {4: 0.5, 3: 0.7, 2: 1.0}


@dataclass
class NativeExposure:
    """One exposure inside the field, prepared: background subtracted, masks applied, positions on the field grid."""

    key: str
    det: int
    mjd: float
    visit: int
    u: NDArray[np.float64]  # grid column of each usable pixel
    v: NDArray[np.float64]
    val: NDArray[np.float32]
    lam: NDArray[np.float32]  # wavelength of each usable pixel, µm
    sigma: float  # robust single-pixel noise
    sat_u: NDArray[np.float64] = field(default_factory=lambda: np.zeros(0))  # saturated core pixels (no value used)
    sat_v: NDArray[np.float64] = field(default_factory=lambda: np.zeros(0))
    census: Mapping[str, tuple[NDArray[np.float64], NDArray[np.float64]]] = field(default_factory=dict)  # flag -> (u, v) of flagged pixels
    footprint_uv: tuple[NDArray[np.float64], NDArray[np.float64]] | None = None  # all in-footprint pixel positions (for the census)


@dataclass
class PassProducts:
    index: int
    mjd0: float
    mjd1: float
    grid: Grid
    detectors: dict[int, int]  # exposures per detector
    num: dict[str, NDArray[np.float32]]  # layer -> smoothed numerator ("D1".."D6", band keys)
    den: dict[str, NDArray[np.float32]]
    sat_num: NDArray[np.float32]  # smoothed saturated weight over CDET
    rejected: dict[int, int]  # cosmic-ray pixels rejected per detector
    visit_rejected: int
    census: dict[str, NDArray[np.float32]]
    registration: Registration | None = None

    def layer(self, name: str) -> NDArray[np.float32]:
        n, d = self.num.get(name), self.den.get(name)
        if n is None or d is None:
            return np.full((self.grid.n, self.grid.n), np.nan, np.float32)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(d > 1e-6, n / np.maximum(d, 1e-12), np.nan).astype(np.float32)

    def noise(self, name: str) -> NDArray[np.float32]:
        """1/sqrt(Σw) of a layer (weights are 1/σ² of the exposures): the model noise of the stacked pixel, before calibration."""
        d = self.den.get(name)
        if d is None:
            return np.full((self.grid.n, self.grid.n), np.nan, np.float32)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(d > 1e-6, 1.0 / np.sqrt(np.maximum(d, 1e-12)), np.nan).astype(np.float32)

    def white(self, cdet: Sequence[int]) -> NDArray[np.float32]:
        layers = [self.layer(f"D{d}") for d in cdet]
        if not layers:
            return np.full((self.grid.n, self.grid.n), np.nan, np.float32)
        st = np.stack(layers)
        ok = np.isfinite(st).all(axis=0)
        return np.where(ok, np.nan_to_num(st).mean(axis=0), np.nan).astype(np.float32)

    def rgb(self, cdet: Sequence[int]) -> NDArray[np.float32]:
        m = colour_matrix(tuple(cdet))
        st = np.stack([self.layer(f"D{d}") for d in range(1, 7)])
        ok = np.isfinite(st[[d - 1 for d in cdet]]).all(axis=0) if cdet else np.zeros(st.shape[1:], bool)
        out = np.einsum("cd,dyx->cyx", m, np.nan_to_num(st))
        return np.where(ok[None], out, np.nan).astype(np.float32)

    def sat_share(self, cdet: Sequence[int]) -> NDArray[np.float32]:
        den = sum((self.den[f"D{d}"] for d in cdet if f"D{d}" in self.den), np.zeros((self.grid.n, self.grid.n), np.float32))
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(den + self.sat_num > 1e-6, self.sat_num / np.maximum(den + self.sat_num, 1e-12), 0.0).astype(np.float32)

    def register(self, reg: Registration) -> None:
        """Shift every smoothed sum by the registration on pass 0."""
        self.registration = reg
        if reg.dx == 0 and reg.dy == 0:
            return
        for d in (self.num, self.den):
            for k in d:
                d[k] = shift_sums(d[k], reg.dx, reg.dy).astype(np.float32)
        self.sat_num = shift_sums(self.sat_num, reg.dx, reg.dy).astype(np.float32)


def _weight(e: NativeExposure) -> float:
    return 1.0 / max(e.sigma, 1e-6) ** 2


def build_pass(
    index: int,
    exposures: Sequence[NativeExposure],
    grid: Grid,
    bands: Sequence[Band],
    cdet: Sequence[int],
    test: bool = True,
) -> PassProducts:
    n = grid.n
    step = grid.step_arcsec
    by_det: dict[int, list[int]] = defaultdict(list)
    for i, e in enumerate(exposures):
        by_det[e.det].append(i)
    num: dict[str, NDArray[np.float32]] = {}
    den: dict[str, NDArray[np.float32]] = {}
    rejected: dict[int, int] = {}
    stack_exps: dict[int, Exposure] = {}
    visit_dropped = 0
    for det, idx in sorted(by_det.items()):
        exps = [
            Exposure(u=exposures[i].u, v=exposures[i].v, val=exposures[i].val, weight=_weight(exposures[i]), sigma=exposures[i].sigma)
            for i in idx
        ]
        res = stack_with_rejection(exps, n, step, test=test)
        rejected[det] = res.rejected
        if test and grid.fov_arcmin <= MAX_FOV_ARCMIN + 0.5:
            visits: dict[int, list[int]] = defaultdict(list)
            for k, i in enumerate(idx):
                visits[exposures[i].visit].append(k)
            dropped = visit_test(exps, list(visits.values()), n, step)
            visit_dropped += dropped
        for k, i in enumerate(idx):
            stack_exps[i] = exps[k]
        s = sigma_grid(len(idx), step)
        a, b = np.zeros((n, n)), np.zeros((n, n))
        for x in exps:
            da, db = deposit(n, x.u[x.keep], x.v[x.keep], x.val[x.keep], np.full(int(x.keep.sum()), x.weight))
            a += da
            b += db
        num[f"D{det}"], den[f"D{det}"] = smooth(a, s).astype(np.float32), smooth(b, s).astype(np.float32)
    # named bands from the surviving pixels, each by its own wavelength
    for band in bands:
        a, b = np.zeros((n, n)), np.zeros((n, n))
        contributing = 0
        for i, e in enumerate(exposures):
            keep = stack_exps[i].keep
            inb = np.zeros(e.lam.size, bool)
            for lo, hi in band.windows:
                inb |= (e.lam >= lo) & (e.lam < hi)
            sel = keep & inb
            if sel.any():
                contributing += 1
                da, db = deposit(n, e.u[sel], e.v[sel], e.val[sel], np.full(int(sel.sum()), _weight(e)))
                a += da
                b += db
        if contributing:
            s = sigma_grid(contributing, step)
            num[band.key], den[band.key] = smooth(a, s).astype(np.float32), smooth(b, s).astype(np.float32)
    # saturated share over CDET
    sat = np.zeros((n, n))
    for e in exposures:
        if e.det in cdet and e.sat_u.size:
            sat += deposit(n, e.sat_u, e.sat_v, np.ones(e.sat_u.size), np.full(e.sat_u.size, _weight(e)))[1]
    sat_num = smooth(sat, sigma_grid(16, step)).astype(np.float32)
    census = flag_census(exposures, n, step)
    mjd = [e.mjd for e in exposures]
    return PassProducts(
        index=index,
        mjd0=float(min(mjd)),
        mjd1=float(max(mjd)),
        grid=grid,
        detectors={d: len(v) for d, v in by_det.items()},
        num=num,
        den=den,
        sat_num=sat_num,
        rejected=rejected,
        visit_rejected=visit_dropped,
        census=census,
    )


def flag_census(exposures: Sequence[NativeExposure], n: int, step_arcsec: float) -> dict[str, NDArray[np.float32]]:
    """Per flag name, the share of exposures (inside their footprints) that carry it at each grid pixel; names set in at least 2 % of
    exposures somewhere in the field."""
    s = NATIVE_PIXEL_ARCSEC / step_arcsec / 2.0
    total = np.zeros((n, n))
    counts: dict[str, NDArray[np.float64]] = defaultdict(lambda: np.zeros((n, n)))
    for e in exposures:
        if e.footprint_uv is None:
            continue
        total += _presence(n, *e.footprint_uv)
        for name, (u, v) in e.census.items():
            if u.size:
                counts[name] += _presence(n, u, v)
    total = smooth(total, s)
    out = {}
    for name, c in counts.items():
        with np.errstate(invalid="ignore", divide="ignore"):
            share = np.where(total > 0.5, smooth(c, s) / np.maximum(total, 1e-9), 0.0)
        if share.max() >= CENSUS_MIN_SHARE:
            out[name] = np.clip(share, 0, 1).astype(np.float32)
    return out


def _presence(n: int, u: NDArray[np.float64], v: NDArray[np.float64]) -> NDArray[np.float64]:
    """1 at every grid pixel that holds at least one of the positions (an exposure counts once per pixel)."""
    ii = np.clip(np.round(u).astype(np.int64), 0, n - 1)
    jj = np.clip(np.round(v).astype(np.int64), 0, n - 1)
    m = np.zeros(n * n)
    m[jj * n + ii] = 1.0
    return m.reshape(n, n)


def quick_subset(exposures: Sequence[NativeExposure], fov_arcmin: float) -> tuple[list[int], float]:
    """Indices of the quick part (a few exposures per detector, spread in time) and its kernel in detector pixels."""
    per = next(k for lim, k in sorted(QUICK_PER_DETECTOR.items()) if fov_arcmin <= lim)
    by_det: dict[int, list[int]] = defaultdict(list)
    for i, e in enumerate(exposures):
        by_det[e.det].append(i)
    out: list[int] = []
    for idx in by_det.values():
        idx = sorted(idx, key=lambda i: exposures[i].mjd)
        take = np.unique(np.linspace(0, len(idx) - 1, min(per, len(idx))).round().astype(int))
        out += [idx[t] for t in take]
    return sorted(out), QUICK_SIGMA_NATIVE[per]
