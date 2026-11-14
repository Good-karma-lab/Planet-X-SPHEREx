from __future__ import annotations

import numpy as np
import pytest

from engine.clean import clipped_median, pixel_mask, subtract_background
from engine.spx.flags import BUILTIN
from engine.stack import resample_then_average, sigma_native, stack_with_rejection
from tests.synth_sky import render

STEP = 1.5375
N = 120


def fwhm(img: np.ndarray, x: int, y: int) -> float:
    """FWHM along x through (x, y), half-maximum crossings interpolated linearly (grid pixels)."""
    row = np.nan_to_num(img[y, x - 15 : x + 16])
    half = row.max() / 2
    k = int(row.argmax())
    left = k - 1
    while left > 0 and row[left] >= half:
        left -= 1
    right = k + 1
    while right < row.size - 1 and row[right] >= half:
        right += 1
    xl = left + (half - row[left]) / (row[left + 1] - row[left])
    xr = right - 1 + (row[right - 1] - half) / (row[right - 1] - row[right])
    return float(xr - xl)


def field(n_exp: int, seed: int = 0, stars=((60.0, 60.0, 50.0), (30.0, 85.0, 8.0))) -> list:  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(seed)
    return [render(list(stars), 34, STEP, N, rng) for _ in range(n_exp)]


def test_kernel_rule() -> None:
    assert sigma_native(16) == 0.35 and sigma_native(15) == 0.50


def test_splat_is_sharper_than_resample_then_average() -> None:
    exps = field(24)
    st = stack_with_rejection(exps, N, STEP, test=False).stack
    naive = resample_then_average(exps, N)
    assert fwhm(st, 60, 60) < fwhm(naive, 60, 60)


def test_flux_is_kept() -> None:
    exps = field(20)
    st = stack_with_rejection(exps, N, STEP, test=False).stack
    # surface brightness × grid-pixel area / native-pixel area recovers the injected flux (per native pixel units)
    box = st[45:76, 45:76]
    total = np.nansum(box) * (STEP / 6.15) ** 2
    assert total == pytest.approx(50.0, rel=0.08)


def test_single_exposure_spike_is_rejected_and_star_cores_are_not() -> None:
    exps = field(20, seed=3)
    e = exps[7]
    far = np.argmin((e.u - 20) ** 2 + (e.v - 20) ** 2)  # empty sky
    e.val[far] += 30.0
    res = stack_with_rejection(exps, N, STEP, test=True)
    assert not exps[7].keep[far]
    for x in exps:  # no pixel at the bright star's core is rejected
        core = (x.u - 60) ** 2 + (x.v - 60) ** 2 < 4.0**2
        assert x.keep[core].all()
    assert res.rejected <= 5
    # the spike leaves no trace in the stack
    assert abs(np.nan_to_num(res.stack[18:23, 18:23])).max() < 0.5


def test_a_spike_shielded_by_a_neighbouring_hit_is_still_rejected_online() -> None:
    exps = field(20, seed=5)
    for k, amp in ((3, 100.0), (4, 7.0)):  # two hits at nearly the same place in two exposures
        e = exps[k]
        i = np.argmin((e.u - 95) ** 2 + (e.v - 30) ** 2)
        e.val[i] += amp
    stack_with_rejection(exps, N, STEP, test=True)
    for k in (3, 4):
        e = exps[k]
        i = np.argmin((e.u - 95) ** 2 + (e.v - 30) ** 2)
        assert not e.keep[i]


def test_background_and_mask() -> None:
    rng = np.random.default_rng(1)
    vals = rng.normal(5.0, 0.1, (50, 50)).astype(np.float32)
    vals[10, 10] = 500.0
    ok = np.ones_like(vals, bool)
    assert clipped_median(vals, ok) == pytest.approx(5.0, abs=0.02)
    lam = np.tile(np.linspace(1.0, 1.2, 50), (50, 1))
    vals2 = vals + np.where((lam > 1.07) & (lam < 1.09), 1.4, 0.0).astype(np.float32)  # an airglow stripe
    out = subtract_background(vals2, ok, lam, lambda_bins=40)
    assert abs(float(np.median(out[(lam > 1.07) & (lam < 1.09)]))) < 0.05
    flags = np.zeros((50, 50), np.int32)
    flags[5, 5] = 1 << BUILTIN["HOT"]
    flags[22, 22] = 1 << BUILTIN["TRANSIENT"]  # 4.2 px from the target: kept
    flags[5, 40] = 1 << BUILTIN["TRANSIENT"]  # far from the target: masked
    flags[25, 25] = 1 << BUILTIN["OVERFLOW"]
    r = np.hypot(*np.mgrid[-25:25, -25:25]).astype(float)
    usable, sat = pixel_mask(flags, vals, BUILTIN, r_target_px=r)
    assert not usable[5, 5]  # BAD_ALWAYS
    assert usable[22, 22] and not usable[5, 40]
    assert sat[25, 25] and not usable[25, 25]  # saturated core near the target
