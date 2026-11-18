"""The film search's on − off reductions run in a worker process (service/workers/filmverify.py reduce_all): the same answers as
onoff.reduce place by place, and arguments that go to a worker process (picklable)."""

from __future__ import annotations

import pickle

import numpy as np

from engine import onoff
from service.workers.filmverify import reduce_all


def test_reduce_all_is_reduce_place_by_place_and_pickles() -> None:
    rng = np.random.default_rng(3)

    def pts(n: int, flux: float) -> list[onoff.Point]:
        return [onoff.Point(float(flux + rng.normal(0, 0.1)), 0.1, float(1.0 + 0.01 * i), True) for i in range(n)]

    on = {0: pts(6, 2.0), 1: pts(2, 1.0), 2: pts(6, 0.0)}
    ref = {0: pts(40, 0.0), 1: pts(40, 0.0), 2: pts(40, 0.0)}
    nmin = [3, 3, 3]
    want = [onoff.reduce(on[k], ref[k], nmin[k]) for k in range(3)]
    args = pickle.loads(pickle.dumps((on, ref, nmin)))
    assert reduce_all(*args) == want and want[1] is None and want[0] is not None
