"""A Find Planet X lead that is a known body says so: Sedna's real blind lead against a fake JPL."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from service import fpx_known
from service.upstream import jpl


@pytest.fixture(autouse=True)
def _fresh_memo() -> None:
    fpx_known._SB_MEMO.clear()


# the blind lead of the live run: its places at each pass's own date and Sedna's JPL places there (0.8″, 0.5″, 0.8″ away)
PLACES = [
    {"ra": 61.77844, "dec": 8.83671, "mjd": 60909.848, "mjd0": 60905.56, "mjd1": 60933.48},
    {"ra": 60.71254, "dec": 8.60824, "mjd": 61082.27, "mjd0": 61065.72, "mjd1": 61092.54},
    {"ra": 62.38896, "dec": 8.9754, "mjd": 61272.847, "mjd0": 61267.78, "mjd1": 61284.42},
]
JPL = {60909.848: (61.77866, 8.83668), 61082.27: (60.71266, 8.60838), 61272.847: (62.38874, 8.97551)}


def fake_jpl(far: bool = False) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if "sb_ident" in str(request.url):
            fields = [
                "Object name",
                'Dist. from center RA (")',
                'Dist. from center Dec (")',
                "Visual magnitude (V)",
                'RA rate ("/h)',
                'Dec rate ("/h)',
            ]
            rows = [
                ["90377 Sedna (2003 VB12)", "3.0", "-2.0", "20.9", "0.20", "-0.05"],
                ["12345 Fast (1999 AB)", "10", "5", "17.0", "40", "10"],  # a main-belt body: not slow
            ]
            return httpx.Response(200, json={"fields_first": fields, "data_first_pass": rows})
        q = dict(request.url.params)
        jds = [float(x.strip("'")) for x in q["TLIST"].split()]
        lines = []
        for jd in jds:
            mjd = jd - 2400000.5
            key = min(JPL, key=lambda m: abs(m - mjd))
            ra = JPL[key][0] + (mjd - key) * 0.0017  # Sedna drifts about 6 arcsec a day in the search's frame
            dec = JPL[key][1]
            r = 40.0 if far else 83.0
            lines.append(f"2026-Feb-11 06:28, , , {ra}, {dec}, 20.9, n.a., {r}, 1.0, {r - 1.0}, 0.1,")
        return httpx.Response(200, json={"result": "x\n$$SOE\n" + "\n".join(lines) + "\n$$EOE\n"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_the_blind_sedna_lead_is_named() -> None:
    out = asyncio.run(fpx_known.identify(fake_jpl(), PLACES, 83.0, 83.0))
    assert out["state"] == "ok" and out["name"].startswith("90377 Sedna")
    assert max(out["seps"]) < 2.0 and out["r_au"] == 83.0
    assert out["words"].startswith("this is Sedna (JPL), found blind")


def test_a_body_at_another_distance_is_not_the_lead() -> None:
    out = asyncio.run(fpx_known.identify(fake_jpl(far=True), PLACES, 83.0, 83.0))  # same places, but JPL's body is at 40 au
    assert out["state"] == "none"


def test_a_lead_without_exact_dates_is_matched_over_the_days_of_each_pass() -> None:
    legacy = [{k: v for k, v in p.items() if k != "mjd"} for p in PLACES]  # a result made before the dates were kept
    out = asyncio.run(fpx_known.identify(fake_jpl(), legacy, 83.0, 83.0))
    assert out["state"] == "ok" and max(out["seps"]) < 6.0  # one-day steps: the nearest day is within a few arcseconds


def test_jpl_down_is_unavailable_not_none() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="Proxy Error")

    out = asyncio.run(fpx_known.identify(httpx.AsyncClient(transport=httpx.MockTransport(boom)), PLACES, 83.0, 83.0))
    assert out == {"state": "unavailable"}
    json.dumps(out)


class FakeStore:
    def __init__(self, meta: dict[str, Any]) -> None:
        self.meta = meta

    def listing(self, kinds: tuple[str, ...]) -> list[dict[str, object]]:
        return [{"key": "k" * 24, "kind": "deep", "complete": True, "params": {}}]

    def read_meta(self, kind: str, key: str) -> dict[str, Any]:
        return self.meta


def test_the_sweep_identifies_a_finished_result_ahead_of_time_and_keeps_the_answer(tmp_path: Path) -> None:
    meta: dict[str, Any] = {
        "status": "ok",
        "plan": {"passes": [{"pass_mjd0": p["mjd0"], "pass_mjd1": p["mjd1"]} for p in PLACES]},  # as the live deep results say it
        "candidates": [
            {
                "ok": True,
                "d": 83.0,
                "ra": 60.7125,
                "dec": 8.6082,
                "by_pass": [{"ra": p["ra"], "dec": p["dec"], "mjd": p["mjd"]} for p in PLACES],
            }
        ],
    }
    idn = fpx_known.FpxIdentifier(FakeStore(meta), tmp_path, client=fake_jpl())

    async def go() -> None:
        first = idn.annotate("deep", "k" * 24, meta)  # nothing kept yet: pending, and the sweep is asked to run
        assert first["candidates"][0]["known"] == {"state": "pending"} and idn.kick_event.is_set()
        assert await idn.sweep() == 1
        again = idn.annotate("deep", "k" * 24, meta)
        k = again["candidates"][0]["known"]
        assert k["state"] == "ok" and k["words"].startswith("this is Sedna (JPL), found blind")
        assert await idn.sweep() == 0  # kept: not asked of JPL again

    asyncio.run(go())
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_a_failed_answer_is_not_kept(tmp_path: Path) -> None:
    meta: dict[str, Any] = {
        "status": "ok",
        "passes": [{"pass_mjd0": p["mjd0"], "pass_mjd1": p["mjd1"]} for p in PLACES],
        "candidates": [{"ok": True, "d": 83.0, "by_pass": [{"ra": p["ra"], "dec": p["dec"], "mjd": p["mjd"]} for p in PLACES]}],
    }

    def boom(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="Proxy Error")

    fpx_known._SB_MEMO.clear()
    idn = fpx_known.FpxIdentifier(FakeStore(meta), tmp_path, client=httpx.AsyncClient(transport=httpx.MockTransport(boom)))
    assert asyncio.run(idn.sweep()) == 0 and not list(tmp_path.glob("*.json"))


def test_findings_peaks_that_are_known_bodies_say_so(tmp_path: Path) -> None:
    meta: dict[str, Any] = {
        "status": "ok",
        "passes": [{"pass_mjd0": p["mjd0"], "pass_mjd1": p["mjd1"]} for p in PLACES],
        "candidates": [
            {
                "ok": True,
                "d": 83.0,
                "ra": 60.712549,
                "dec": 8.608244,
                "by_pass": [{"ra": p["ra"], "dec": p["dec"], "mjd": p["mjd"]} for p in PLACES],
            }
        ],
    }
    idn = fpx_known.FpxIdentifier(FakeStore(meta), tmp_path, client=fake_jpl())
    asyncio.run(idn.sweep())
    body: dict[str, Any] = {
        "counts": {"peak": {"all": 1, "unexplained": 0, "unchecked": 1, "known": 0}},
        "finds": [
            {
                "id": "peak:1",
                "kind": "peak",
                "ra": 60.712549,
                "dec": 8.608244,
                "status": "unchecked",
                "verdict": "",
                "scans": ["k" * 24],
                "data": {"search": "deep", "peak": {"d": 83.0}},
            }
        ],
    }
    out = idn.enrich_findings(body)
    f = out["finds"][0]
    assert f["status"] == "known" and f["verdict"].startswith("This is Sedna (JPL), found blind")
    assert f["data"]["peak"]["known"]["name"].startswith("90377 Sedna")
    assert out["counts"]["peak"] == {"all": 1, "unexplained": 0, "unchecked": 0, "known": 1}


def test_an_answer_got_is_kept_while_another_lead_waits_for_jpl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jpl, "GAP_S", 0.0)
    by_pass = [{"ra": p["ra"], "dec": p["dec"], "mjd": p["mjd"]} for p in PLACES]
    meta: dict[str, Any] = {
        "status": "ok",
        "passes": [{"pass_mjd0": p["mjd0"], "pass_mjd1": p["mjd1"]} for p in PLACES],
        "candidates": [
            {"d": 83.0, "ra": 1.0, "dec": 1.0, "ok": True, "by_pass": by_pass},
            {"d": 83.0, "ra": 2.0, "dec": 2.0, "ok": True, "by_pass": by_pass},
        ],
    }
    good = fake_jpl()
    calls = {"sb": 0}

    async def flaky(request: httpx.Request) -> httpx.Response:
        if "sb_ident" in str(request.url):
            calls["sb"] += 1
            if calls["sb"] == 1:
                return httpx.Response(502, text="Proxy Error")  # JPL's gateway fails the first lead once
        return await good._transport.handle_async_request(request)  # type: ignore[attr-defined]

    fpx_known._SB_MEMO.clear()
    idn = fpx_known.FpxIdentifier(FakeStore(meta), tmp_path, client=httpx.AsyncClient(transport=httpx.MockTransport(flaky)))

    async def go() -> None:
        assert await idn.sweep() == 0  # the second lead answered, the first did not
        kept = idn.cached("deep", "k" * 24)
        assert kept is not None and set(kept) == {1} and kept[1]["state"] == "ok"
        idn.failed_at.clear()
        n_sb = calls["sb"]
        assert await idn.sweep() == 1  # only the first lead is asked again
        assert calls["sb"] <= n_sb + 1  # (the answer for these places may already be remembered)
        kept = idn.cached("deep", "k" * 24)
        assert kept is not None and set(kept) == {0, 1}

    asyncio.run(go())
