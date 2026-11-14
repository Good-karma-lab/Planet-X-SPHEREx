"""Visits and passes (docs/plan/02-methods/2.1-data-and-reading.md, 2.1.7).

Visit: consecutive exposures of one detector less than 0.5 h apart. Pass: exposures separated from the next by more than 60 days start
a new pass, and a pass longer than 90 days is cut, so that a field observed without a break (near the ecliptic poles) still gives several
frames. Any number of passes. A pass is named by its time span and middle MJD, never by an index alone.
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

VISIT_GAP_DAYS = 0.5 / 24.0
PASS_GAP_DAYS = 60.0
PASS_MAX_DAYS = 90.0
MIN_EXPOSURES_PER_DETECTOR = 4  # a detector with fewer exposures in a pass is "absent" in it for the white frame


@dataclass(frozen=True)
class Pass:
    index: int
    mjd0: float
    mjd1: float
    members: tuple[int, ...]  # indices into the exposure list, in time order

    @property
    def mjd_mid(self) -> float:
        return 0.5 * (self.mjd0 + self.mjd1)


def split_passes(mjd: Sequence[float], gap_days: float = PASS_GAP_DAYS, max_days: float = PASS_MAX_DAYS) -> list[Pass]:
    t = np.asarray(mjd, float)
    if t.size == 0:
        return []
    order = np.argsort(t, kind="stable")
    groups: list[list[int]] = [[int(order[0])]]
    start = t[order[0]]
    for prev, cur in itertools.pairwise(order):
        if t[cur] - t[prev] > gap_days or t[cur] - start > max_days:
            groups.append([])
            start = t[cur]
        groups[-1].append(int(cur))
    return [Pass(i, float(t[g].min()), float(t[g].max()), tuple(g)) for i, g in enumerate(groups)]


def split_visits(mjd: Sequence[float], det: Sequence[int], gap_days: float = VISIT_GAP_DAYS) -> list[tuple[int, ...]]:
    """Visits: per detector, runs of exposures with gaps under `gap_days`; returned in time order of their first exposure."""
    t = np.asarray(mjd, float)
    d = np.asarray(det, int)
    visits: list[list[int]] = []
    for k in np.unique(d):
        idx = np.flatnonzero(d == k)
        idx = idx[np.argsort(t[idx], kind="stable")]
        cur = [int(idx[0])]
        for a, b in itertools.pairwise(idx):
            if t[b] - t[a] >= gap_days:
                visits.append(cur)
                cur = []
            cur.append(int(b))
        visits.append(cur)
    visits.sort(key=lambda v: t[v[0]])
    return [tuple(v) for v in visits]


def detectors_per_pass(passes: Sequence[Pass], det: Sequence[int]) -> list[dict[int, int]]:
    d = np.asarray(det, int)
    out = []
    for p in passes:
        c: dict[int, int] = defaultdict(int)
        for i in p.members:
            c[int(d[i])] += 1
        out.append(dict(c))
    return out


def common_detectors(counts: Sequence[dict[int, int]], minimum: int = MIN_EXPOSURES_PER_DETECTOR) -> tuple[int, ...]:
    """CDET: detectors with at least `minimum` exposures in every pass (the white frame and the colour use only these)."""
    if not counts:
        return ()
    dets = set(range(1, 7))
    for c in counts:
        dets &= {k for k, n in c.items() if n >= minimum}
    return tuple(sorted(dets))
