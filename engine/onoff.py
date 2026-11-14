"""On − off photometry of a candidate mover (docs/plan/02-methods/2.3-views.md): is there light at this place in the exposures of its
own visit that
the same place does not show in the exposures of the same detector taken when the body was elsewhere?

`on`: the PSF flux at the candidate's place in every exposure of its visit. `off`: the median of the nine fluxes nearest in wavelength
(within
12 %) at the same place in the exposures of the same detector at least 0.2 days from the visit. The difference of each on-exposure
against its
own `off`, averaged over the visit, is the candidate's flux; verified = every exposure positive and the flux at `NSIG` sigma together.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

NREF = 9
MAX_DLAM = 0.12
MIN_REF = 4
NSIG = 4.0  # a candidate counts as verified alone at this significance with every exposure positive
PRE_NSIG = 2.0  # a candidate is linked into tracks at this significance (a TRACK must then reach 5 sigma together)
TRACK_NSIG = 5.0
REF_GAP_DAYS = 0.2
MIN_PSF_GOOD = 0.8


@dataclass(frozen=True)
class Point:
    flux: float  # mJy
    err: float
    lam: float  # µm
    ok: bool


def off_level(on: Point, refs: list[Point]) -> tuple[float, float] | None:
    """The reference flux and its error for one on-exposure: the median of the `NREF` references nearest in wavelength (none farther
    than 12 %);
    None when fewer than `MIN_REF` references qualify."""
    ok = [q for q in refs if q.ok and np.isfinite(q.flux)]
    ref: list[float] = []
    for q in sorted(ok, key=lambda q: abs(q.lam - on.lam))[: 3 * NREF]:
        if abs(q.lam - on.lam) < MAX_DLAM * on.lam:
            ref.append(q.flux)
        if len(ref) == NREF:
            break
    if len(ref) < MIN_REF:
        return None
    a = np.array(ref)
    off = float(np.median(a))
    return off, float(1.2533 * 1.4826 * np.median(np.abs(a - off)) / np.sqrt(len(a)))


def reduce(on: list[Point], refs: list[Point], nmin: int) -> dict[str, Any] | None:
    """The significance of one candidate, or None when it cannot be measured (too few exposures, too few references)."""
    on = [p for p in on if p.ok and np.isfinite(p.flux)]
    if len(on) < nmin:
        return None
    ds: list[tuple[float, float, float]] = []
    for p in on:
        lv = off_level(p, refs)
        if lv is not None:
            ds.append((p.flux - lv[0], float(np.hypot(p.err, lv[1])), lv[1]))
    if len(ds) < nmin:
        return None
    d = np.array([x[0] for x in ds])
    em = float(
        np.sqrt(
            max(np.std(d, ddof=1) if len(d) > 1 else 0.0, float(np.median([x[1] for x in ds]))) ** 2 / len(d)
            + float(np.median([x[2] for x in ds])) ** 2
        )
    )
    sig = float(d.mean() / em) if em > 0 else 0.0
    allpos = bool((d > 0).all())
    return {"sig": sig, "flux": float(d.mean()), "err": em, "allpos": allpos, "non": len(d), "ok": bool(allpos and sig >= NSIG)}
