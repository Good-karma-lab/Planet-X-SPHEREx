# ruff: noqa: N806
# mypy: disable-error-code="no-untyped-call"
from __future__ import annotations

import itertools
from pathlib import Path

import numpy as np

from engine import distant as edist


def synthetic_visits(n: int = 48, nv: int = 8) -> tuple[list[dict[str, object]], edist.Tangent]:
    rng = np.random.default_rng(3)
    T = edist.Tangent(120.0, 10.0)
    from engine import orbit

    t0 = 60900.0
    out = []
    for k in range(nv):
        t = t0 + 2.0 * k
        d = rng.normal(0.0, 1.0, (n, n)).astype("f4")
        out.append(dict(k=0, t=t, R=orbit.earth(t), d=d, good=np.ones((n, n), bool), sg=1.0, s=1.0))
    return out, T


def test_the_blocks_put_together_give_the_search(tmp_path: Path) -> None:
    DV, T = synthetic_visits()
    whole = edist.search(DV, T, 48, 60.0, 90.0, workers=2)
    prep = edist.search_prepare(DV, T, 48, 60.0, 90.0, str(tmp_path))
    cuts = prep["cuts"]
    parts = [edist.search_block(str(tmp_path), int(a), int(b), prep["wsum"]) for a, b in itertools.pairwise(cuts) if b > a]
    got = edist.search_merge(prep, parts[::-1], 0.0)  # the order the blocks finish in does not matter
    assert np.allclose(got["best"], whole["best"]) and np.allclose(got["neg"], whole["neg"])
    assert (got["arg"] == whole["arg"]).mean() > 0.99  # a tie between two hypotheses may fall either way
    assert len(prep["H"]) == len(whole["H"]) and len(parts) <= edist.SEARCH_BLOCKS
