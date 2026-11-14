from __future__ import annotations

import asyncio

import pytest

from service.warm import WarmRunner, place_at_epoch, warm_set


def test_barnards_star_at_epoch_2025_9_is_where_the_lab_opens_it() -> None:
    t = {"ra_deg": 269.4520769586187, "dec_deg": 4.693364966576667, "epoch": 2000.0, "pmra_mas_yr": -801.551, "pmdec_mas_yr": 10362.394}
    ra, dec = place_at_epoch(t, 2025.9)
    assert ra == pytest.approx(269.44629, abs=2e-5)  # 17h57m47.11s
    assert dec == pytest.approx(4.76792, abs=2e-5)  # +04°46′04.5″


def test_warm_set_has_the_five_targets_at_five_sizes_and_the_demo_fields() -> None:
    w = warm_set()
    assert len([f for f in w if f.label in ("Barnard's Star", "R Draconis")]) == 10
    assert len(w) == 25 + 3


def test_runner_counts_and_retries_failures() -> None:
    w = warm_set()[:3]
    calls: list[str] = []

    async def ensure(f):  # type: ignore[no-untyped-def]
        calls.append(f.label)
        if f.fov == 5:
            raise RuntimeError("S3 did not answer")
        return True

    r = WarmRunner(w)
    asyncio.run(r.run(ensure))
    snap = r.snapshot()
    assert snap["done"] == 2 and snap["state"] == "incomplete" and list(snap["failed"].values()) == ["RuntimeError: S3 did not answer"]
