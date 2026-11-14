"""The spectral anomaly map of a field (docs/plan/02-methods/2.6-anomalies-checks-chance.md 2.6.1).

Which sources of a field do not have the spectrum of an ordinary star of that field?

    bin       24 bins in ln λ from 0.75 to 5.0 µm: median and 1.25·MAD/√n; a source needs 60 points and 20 bins
    shape     f / Σf over its bins
    family    med = the median shape of the bright half; pc1, pc2 = the first two principal components of the bulk (temperature and
              reddening move a stellar spectrum along them)
    fit       model = a·(med + c1·pc1 + c2·pc2) on the source's own bins, weighted
    score     √mean(((shape − model) / √(e² + (0.05·model)²))²)      the 5 % floor keeps the systematics of bright stars out
    anomalous score > median + 6 · 1.4826 · MAD of the field's scores; fewer than 12 sources with a spectrum: no ordinary star
    passport  the residual of every bin in σ and the up-to-3 bins that drive the misfit (largest shares of the squared residual)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

NBINS = 24
LAM0, LAM1 = 0.75, 5.0
EDGES = np.exp(np.linspace(np.log(LAM0), np.log(LAM1), NBINS + 1))
CENTRES = np.sqrt(EDGES[:-1] * EDGES[1:])
MIN_POINTS = 60
MIN_BINS = 20
FLOOR = 0.05
NSIG = 6.0
MIN_SOURCES = 12
DRIVERS = 3


@dataclass
class Binned:
    med: NDArray[np.float64]  # NaN where the bin is empty
    err: NDArray[np.float64]
    n: NDArray[np.int64]
    points: int

    @property
    def usable(self) -> bool:
        return self.points >= MIN_POINTS and int(np.isfinite(self.med).sum()) >= MIN_BINS


def bin_spectrum(lam: NDArray[np.floating], flux: NDArray[np.floating]) -> Binned:
    lam = np.asarray(lam, float)
    flux = np.asarray(flux, float)
    ok = np.isfinite(lam) & np.isfinite(flux)
    lam, flux = lam[ok], flux[ok]
    k = np.digitize(lam, EDGES) - 1
    med = np.full(NBINS, np.nan)
    err = np.full(NBINS, np.nan)
    n = np.zeros(NBINS, np.int64)
    for b in range(NBINS):
        f = flux[k == b]
        n[b] = f.size
        if f.size >= 2:
            m = float(np.median(f))
            mad = 1.4826 * float(np.median(np.abs(f - m)))
            med[b] = m
            err[b] = max(1.25 * mad / np.sqrt(f.size), 1e-12)
    return Binned(med, err, n, int(ok.sum()))


@dataclass
class Score:
    score: float
    anomalous: bool
    model: NDArray[np.float64]  # shape units (f / Σf)
    shape: NDArray[np.float64]
    shape_err: NDArray[np.float64]
    resid_z: NDArray[np.float64]
    drivers: list[dict[str, float]] = field(default_factory=list)


@dataclass
class FieldScores:
    scores: list[Score | None]
    median: float = float("nan")
    scatter: float = float("nan")
    threshold: float = float("nan")
    family_size: int = 0
    reason: str = ""  # why there is no map ("" when there is one)


def _shape(b: Binned) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    s = float(np.nansum(b.med))
    if not np.isfinite(s) or s <= 0:
        s = float(np.nansum(np.abs(b.med))) or 1.0
    return b.med / s, b.err / s


def _fill(shape: NDArray[np.float64]) -> NDArray[np.float64]:
    """Missing bins filled by interpolation in ln λ (for the family only)."""
    ok = np.isfinite(shape)
    if ok.all():
        return shape
    x = np.log(CENTRES)
    return np.interp(x, x[ok], shape[ok])


def score_field(binned: list[Binned], brightness: list[float]) -> FieldScores:
    idx = [i for i, b in enumerate(binned) if b.usable]
    out: list[Score | None] = [None] * len(binned)
    if len(idx) < MIN_SOURCES:
        return FieldScores(out, reason=f"{len(idx)} sources with a spectrum: an ordinary star of the field cannot be defined (12 needed)")
    shapes = {i: _shape(binned[i]) for i in idx}
    filled = np.array([_fill(shapes[i][0]) for i in idx])
    filled /= filled.sum(axis=1, keepdims=True)
    order = np.argsort([-brightness[i] for i in idx])
    bright = filled[order[: max(MIN_SOURCES // 2, len(idx) // 2)]]
    med = np.median(bright, axis=0)
    resid = filled - med
    # principal components of the bulk: the half of the sources closest to the median shape
    dist = np.sqrt((resid**2).sum(axis=1))
    bulk = resid[dist <= np.median(dist)]
    _, _, vt = np.linalg.svd(bulk - bulk.mean(axis=0), full_matrices=False)
    basis = np.vstack([med, vt[:2]])
    for i in idx:
        sh, se = shapes[i]
        ok = np.isfinite(sh) & np.isfinite(se)
        coef = np.zeros(3)
        model = med.copy()
        for _ in range(3):
            sig = np.sqrt(se**2 + (FLOOR * np.abs(model)) ** 2)
            w = 1.0 / sig[ok]
            coef, *_ = np.linalg.lstsq((basis[:, ok] * w).T, sh[ok] * w, rcond=None)
            model = coef @ basis
        sig = np.sqrt(se**2 + (FLOOR * np.abs(model)) ** 2)
        z = np.where(ok, (sh - model) / sig, np.nan)
        s = float(np.sqrt(np.nanmean(z**2)))
        share = np.where(ok, z**2, 0.0)
        share = share / share.sum() if share.sum() > 0 else share
        top = np.argsort(-share)[:DRIVERS]
        drivers = [
            {"lo": float(EDGES[b]), "hi": float(EDGES[b + 1]), "z": float(z[b]), "share": float(share[b])} for b in top if share[b] > 0.05
        ]
        out[i] = Score(s, False, model, sh, se, z, drivers)
    vals = np.array([out[i].score for i in idx])  # type: ignore[union-attr]
    m = float(np.median(vals))
    sc = 1.4826 * float(np.median(np.abs(vals - m)))
    thr = m + NSIG * sc
    for i in idx:
        sc_i = out[i]
        assert sc_i is not None
        sc_i.anomalous = sc_i.score > thr
    return FieldScores(out, m, sc, thr, len(idx))
