from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient
from numpy.typing import NDArray

from engine import pairs as ep
from service import pairs as sp
from service.products.sxp import encode
from service.routes_pairs import make_pairs_router

STEP = 2.0
N = 160
FWHM = ep.FWHM_STACK


def star(x: float, y: float, amp: float) -> NDArray[np.float64]:
    yy, xx = np.mgrid[0:N, 0:N]
    sg = FWHM / 2.355 / STEP
    return np.asarray(amp * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sg**2)), np.float64)


def stack(rng: np.random.Generator, comps: list[tuple[float, float, float]], noise: float = 0.4) -> NDArray[np.float32]:
    img = rng.normal(0.0, noise, (N, N))
    for x, y, a in comps:
        img = img + star(x, y, a)
    return img.astype(np.float32)


PA, PB = (70.0, 80.0), (75.0, 80.0)  # 10″ apart at 2″ a pixel: resolved by the finder as two peaks
FAR = [(20.0, 25.0, 40.0), (125.0, 120.0, 55.0)]


def field(ratios: list[float], seed: int = 3, a: float = 60.0) -> tuple[list[NDArray[np.float32]], list[NDArray[np.float32]]]:
    rng = np.random.default_rng(seed)
    whites = [stack(rng, [(*PA, a), (*PB, a * r), *FAR]) for r in ratios]
    return whites, [np.zeros((N, N), np.float32) for _ in ratios]


def test_fit_pair_amplitudes_and_anticorrelation() -> None:
    whites, _ = field([0.5])
    f = ep.fit_pair(whites[0], *PA, *PB, STEP)
    assert f is not None
    assert abs(f["a"] - 60.0) < 3.0 and abs(f["b"] - 30.0) < 3.0
    assert f["cov_ab"] < 0  # two overlapping stars trade flux
    assert f["ea"] > 0 and f["eb"] > 0


def test_changing_pair_is_real_steady_is_not() -> None:
    whites, _ = field([0.5, 1.0, 2.0])
    fw = []
    for w in whites:
        f = ep.fit_pair(w, *PA, *PB, STEP)
        assert f is not None
        fw.append([f["a"], f["b"], f["ea"], f["eb"], f["cov_ab"]])
    assert ep.pair_is_real(fw)
    assert ep.component_changes(fw)
    steady, _ = field([0.8, 0.8, 0.8], seed=5)
    fs = []
    for w in steady:
        f = ep.fit_pair(w, *PA, *PB, STEP)
        assert f is not None
        fs.append([f["a"], f["b"], f["ea"], f["eb"], f["cov_ab"]])
    assert not ep.pair_is_real(fs)


def test_refine_pair_moves_pulled_in_peaks_apart() -> None:
    whites, _ = field([0.7])
    pa, pb = ep.refine_pair(whites[0], (PA[0] + 1.0, PA[1]), (PB[0] - 1.0, PB[1]), STEP)
    assert abs(pa[0] - PA[0]) < 0.4 and abs(pb[0] - PB[0]) < 0.4 and abs(pa[1] - PA[1]) < 0.4


def test_a_component_absent_in_one_pass_is_not_a_pair() -> None:
    rng = np.random.default_rng(9)
    whites = [stack(rng, [(*PA, 60.0), (*PB, 50.0), *FAR]), stack(rng, [(*PA, 60.0), *FAR]), stack(rng, [(*PA, 60.0), (*PB, 50.0), *FAR])]
    fw = []
    for w in whites:
        f = ep.fit_pair(w, *PA, *PB, STEP)
        assert f is not None
        fw.append([f["a"], f["b"], f["ea"], f["eb"], f["cov_ab"]])
    assert not ep.pair_is_real(fw)  # its second component is at S/N ~0 in one pass


def test_change_sigma_uses_the_covariance() -> None:
    # the same amplitudes and errors: a positive covariance shrinks the ratio's error, a negative one widens it
    base = [[10.0, 5.0, 0.3, 0.3, 0.0], [10.0, 7.0, 0.3, 0.3, 0.0]]
    neg = [[10.0, 5.0, 0.3, 0.3, -0.05], [10.0, 7.0, 0.3, 0.3, -0.05]]
    z0, _ = ep.change_sigma(base)
    z1, _ = ep.change_sigma(neg)
    assert z1 < z0


def test_analyse_finds_the_changing_pair_and_counts_chance() -> None:
    whites, sats = field([0.5, 1.0, 2.0])
    out = ep.analyse(whites, sats, [{"D1": w, "D2": w * 1.0} for w in whites], STEP, ntest=20)
    assert len(out["finds"]) == 1
    f = out["finds"][0]
    assert abs(f["separation_arcsec"] - 10.0) < 2.0
    assert f["change_sigma"] >= ep.CHANGE_SIGMA
    assert len(f["crops"]["data"]) == 3 and f["crops"]["size"] == 64
    assert f["verdict"].startswith("component varies")
    assert out["chance_pairs"]["candidates"] >= 1
    # without the change there is nothing to find
    steady, sats2 = field([0.8, 0.8, 0.8], seed=5)
    assert ep.analyse(steady, sats2, [{} for _ in steady], STEP, ntest=20)["finds"] == []


def test_saturated_zone_hides_sources() -> None:
    whites, _ = field([1.0, 1.0])
    sat = np.zeros((N, N), np.float32)
    sat[70:90, 60:85] = 1.0
    valid = np.ones((N, N), bool)
    src = ep.find_sources(whites[0], sat, valid, STEP)
    assert all(not (60 <= s[0] <= 85 and 70 <= s[1] <= 90) for s in src)
    assert len(src) >= 2


class FakeStore:
    def __init__(self, parts: dict[str, bytes], meta: dict[str, Any]) -> None:
        self.parts, self.meta = parts, meta

    def read_part(self, kind: str, key: str, name: str) -> bytes | None:
        return self.parts.get(name)

    def read_meta(self, kind: str, key: str) -> dict[str, Any] | None:
        return self.meta

    def is_complete(self, key: str) -> bool:
        return True


def fake_field(ratios: list[float]) -> FakeStore:
    whites, _ = field(ratios)
    parts = {}
    for k, w in enumerate(whites):
        dets = np.stack([w, w, w, w, w, w]).astype(np.float16)
        parts[f"dets_p{k}.sxp"] = encode({"dets": dets, "noise": np.ones_like(dets)}, {"pass": k})
        parts[f"picture_p{k}.sxp"] = encode({"rgb": np.zeros((3, N, N), np.float16), "sat": np.zeros((N, N), np.uint8)}, {"pass": k})
    ra0, dec0 = 150.0, 10.0
    meta = {
        "grid": {"ra0": ra0, "dec0": dec0, "step_arcsec": STEP, "n": N},
        "cdet": [1, 2, 3],
        "passes": [{"index": k, "mjd_mid": 60800.0 + 100 * k} for k in range(len(ratios))],
    }
    return FakeStore(parts, meta)


class FakeCatalogue:
    def __init__(self, ok: bool) -> None:
        self.ok = ok
        self.asked = 0

    async def context(self, ra: float, dec: float, epoch: float, radius: float) -> dict[str, Any]:
        self.asked += 1
        if not self.ok:
            return {"simbad": {"state": "unavailable"}, "gaia": {"state": "none"}}
        return {"simbad": {"state": "ok", "name": "V* test", "otype": "TTS", "sep": 0.4, "extra": 1}, "gaia": {"state": "none"}}


def test_service_search_identify_and_persist(tmp_path: Path) -> None:
    store = fake_field([0.5, 1.0, 2.0])
    cat = FakeCatalogue(ok=False)
    pairs = sp.Pairs(store, tmp_path, cat)  # type: ignore[arg-type]

    async def go() -> sp.PairRun:
        run = pairs.get_or_start("fk")
        for _ in range(200):
            if run.state != "running":
                break
            await asyncio.sleep(0.05)
        return run

    run = asyncio.run(go())
    assert run.state == "done"
    rec = run.result["pairs"][0]
    assert rec["kind"] == "pair" and len(rec["components"]) == 2
    c0 = rec["components"][0]
    assert len(c0["flux_per_pass"]) == 3 and set(c0["flux_per_detector"]) >= {"D1", "D2"}
    assert 149.9 < c0["ra"] < 150.1 and 9.9 < c0["dec"] < 10.1
    assert run.result["identified"] is False and not list(tmp_path.glob("pairs/*.json"))  # a catalogue that did not answer is not kept
    assert pairs.view(run)["mjd"] == [60800.0, 60900.0, 61000.0]
    cat.ok = True

    async def again() -> None:
        await pairs._identify_all(run)

    asyncio.run(again())
    assert run.result["identified"] is True
    assert rec["components"][0]["simbad"] == {"state": "ok", "name": "V* test", "otype": "TTS", "sep": 0.4}
    saved = list(tmp_path.glob("pairs/*.json"))
    assert len(saved) == 1 and json.loads(saved[0].read_text())["pairs"][0]["kind"] == "pair"


def test_route_returns_the_movers_body_plus_pairs(tmp_path: Path, monkeypatch: Any) -> None:
    store = fake_field([0.5, 1.0, 2.0])
    pairs = sp.Pairs(store, tmp_path, FakeCatalogue(ok=True))  # type: ignore[arg-type]
    movers_result = {"tracks": [], "chance": {"trials": 40, "mean": 0.2, "any": 0.1}, "stationary": 5}
    movers = SimpleNamespace(
        get_or_start=lambda key: "run", view=lambda run: {"key": "m", "state": "done", "error": "", "result": dict(movers_result)}
    )
    st = SimpleNamespace(index=SimpleNamespace(overlapping=lambda *a, **k: []), store=store, movers=movers, pairs=pairs)
    app = FastAPI()
    app.include_router(make_pairs_router(st))
    with TestClient(app) as c:
        first = c.get("/api/v1/movers", params={"ra": 150.0, "dec": 10.0, "fov": 5}).json()
        assert first["state"] == "done" and first["result"]["stationary"] == 5
        assert first["chance"] == {"mean": 0.2, "trials": 40} and first["mjd"] == [60800.0, 60900.0, 61000.0]
        assert first["pairs_state"] in ("running", "done")
        for _ in range(100):
            body = c.get("/api/v1/movers", params={"ra": 150.0, "dec": 10.0, "fov": 5}).json()
            if body["pairs_state"] == "done":
                break
            import time

            time.sleep(0.05)
        assert body["pairs_state"] == "done" and len(body["pairs"]) == 1 and body["chance_pairs"]["candidates"] >= 1
        assert body["result"]["pairs"] == body["pairs"] and body["result"]["mjd"] == body["mjd"]
