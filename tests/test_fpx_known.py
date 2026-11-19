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
    fpx_known._BIG_MEMO.clear()


# the blind lead of the live run: its places at each pass's own date and Sedna's JPL places there (0.8″, 0.5″, 0.8″ away)
PLACES = [
    {"ra": 61.77844, "dec": 8.83671, "mjd": 60909.848, "mjd0": 60905.56, "mjd1": 60933.48},
    {"ra": 60.71254, "dec": 8.60824, "mjd": 61082.27, "mjd0": 61065.72, "mjd1": 61092.54},
    {"ra": 62.38896, "dec": 8.9754, "mjd": 61272.847, "mjd0": 61267.78, "mjd1": 61284.42},
]
JPL = {60909.848: (61.77866, 8.83668), 61082.27: (60.71266, 8.60838), 61272.847: (62.38874, 8.97551)}


def fake_jpl(far: bool = False, eris: list[dict[str, Any]] | None = None, sb_sedna: bool = True) -> httpx.AsyncClient:
    """JPL with Sedna on the PLACES; `eris`: places of the passes where Eris stands (sbident never names it, as the real one does not);
    every other large body far away."""

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
                *([["90377 Sedna (2003 VB12)", "3.0", "-2.0", "20.9", "0.20", "-0.05"]] if sb_sedna else []),
                ["12345 Fast (1999 AB)", "10", "5", "17.0", "40", "10"],  # a main-belt body: not slow
            ]
            return httpx.Response(200, json={"fields_first": fields, "data_first_pass": rows})
        q = dict(request.url.params)
        cmd = q["COMMAND"]
        jds = [float(x.strip("'")) for x in q["TLIST"].split()]
        lines = []
        for jd in jds:
            mjd = jd - 2400000.5
            key = min(JPL, key=lambda m: abs(m - mjd))
            ra = JPL[key][0] + (mjd - key) * 0.0017  # Sedna drifts about 6 arcsec a day in the search's frame
            dec = JPL[key][1]
            r = 40.0 if far else 83.0
            if "136199" in cmd and eris is not None:  # Eris: at the given passes' places, else 2° away; 95.6 au
                at = min(eris, key=lambda p: abs(p["mjd"] - mjd)) if eris else None
                ra, dec, r = (at["ra"], at["dec"], 95.6) if at and abs(at["mjd"] - mjd) < 30 else (ra + 2.0, dec, 95.6)
            elif "90377" not in cmd:
                ra += 90.0  # the other large bodies: nowhere near
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
    real_sleep = asyncio.sleep

    async def quick(delay: float, *a: Any) -> None:  # the pauses between the three asks
        await real_sleep(0)

    monkeypatch.setattr(fpx_known.asyncio, "sleep", quick)
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
            if calls["sb"] <= 3:
                return httpx.Response(502, text="Proxy Error")  # JPL's gateway fails the first lead all three times it is asked
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


def test_a_peak_at_the_edge_of_the_range_or_in_a_bright_stars_halo_is_a_residual_not_a_lead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result: dict[str, Any] = {"params": {"dmin": 250.0, "dmax": 2000.0}}
    assert fpx_known.edge_reason(result, {"d_lo": 1267.5, "d_hi": 2000.0}) is not None  # WISE 0855: strong all the way to the edge
    assert fpx_known.edge_reason(result, {"d_lo": 600.0, "d_hi": 700.0}) is None
    assert (
        fpx_known.edge_reason({"params": {"dmin": 30.0, "dmax": 300.0}}, {"d_lo": 30.0, "d_hi": 40.0}) is not None
    )  # Barnard: "30 au or farther"
    from service.upstream import gaia_hats

    star = {"ra": 77.0, "dec": 30.5, "pmra": 0.0, "pmdec": 0.0, "phot_g_mean_mag": 10.4}
    monkeypatch.setattr(gaia_hats, "cone", lambda ra, dec, r: [star])
    place = {"ra": 77.0 + 10 / 3600.0, "dec": 30.5, "mjd": 61000.0, "mjd0": 60990.0, "mjd1": 61010.0}  # 10 arcsec from the star
    assert "star" in str(asyncio.run(fpx_known.halo_reason([place])))
    far = dict(place, ra=77.0 + 200 / 3600.0)
    assert asyncio.run(fpx_known.halo_reason([far])) is None
    faint = dict(star, phot_g_mean_mag=15.0)
    monkeypatch.setattr(gaia_hats, "cone", lambda ra, dec, r: [faint])
    assert asyncio.run(fpx_known.halo_reason([place])) is None


def test_the_sweep_keeps_a_residual_and_the_result_stops_calling_it_a_lead(tmp_path: Path) -> None:
    meta: dict[str, Any] = {
        "status": "ok",
        "params": {"dmin": 250.0, "dmax": 2000.0},
        "t0": 61000.0,
        "candidates": [{"ok": True, "d": 1267.5, "d_lo": 1267.5, "d_hi": 2000.0, "ra": 133.74, "dec": -7.35, "sn": 10.3}],
    }

    class OneDistant(FakeStore):
        def listing(self, kinds: tuple[str, ...]) -> list[dict[str, object]]:
            return [{"key": "d" * 24, "kind": "distant", "complete": True, "params": {}}]

    idn = fpx_known.FpxIdentifier(OneDistant(meta), tmp_path, client=fake_jpl())
    assert asyncio.run(idn.sweep()) == 1
    out = idn.annotate("distant", "d" * 24, meta)["candidates"][0]
    assert out["ok"] is False and out["known"]["state"] == "residual" and "edge" in out["residual"]
    body: dict[str, Any] = {
        "counts": {"peak": {"all": 1, "unexplained": 0, "unchecked": 1, "known": 0}},
        "finds": [
            {
                "id": "peak:1",
                "kind": "peak",
                "ra": 133.74,
                "dec": -7.35,
                "status": "unchecked",
                "verdict": "",
                "scans": ["d" * 24],
                "data": {"search": "distant", "peak": {"ok": True}},
            }
        ],
    }
    res = idn.enrich_findings(body)
    assert res["finds"][0]["data"]["peak"]["ok"] is False and res["counts"]["peak"]["unchecked"] == 0


def test_a_gateway_failure_of_jpl_is_asked_again_before_a_lead_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    real_sleep = asyncio.sleep

    async def quick(delay: float, *a: Any) -> None:
        await real_sleep(0)

    monkeypatch.setattr(fpx_known.asyncio, "sleep", quick)
    monkeypatch.setattr(jpl, "GAP_S", 0.0)
    good = fake_jpl()
    calls = {"sb": 0}

    async def once(request: httpx.Request) -> httpx.Response:
        if "sb_ident" in str(request.url):
            calls["sb"] += 1
            if calls["sb"] == 1:
                return httpx.Response(502, text="Proxy Error")
        return await good._transport.handle_async_request(request)  # type: ignore[attr-defined]

    fpx_known._SB_MEMO.clear()
    out = asyncio.run(fpx_known.identify(httpx.AsyncClient(transport=httpx.MockTransport(once)), PLACES, 83.0, 83.0))
    assert out["state"] == "ok" and out["name"].startswith("90377 Sedna") and calls["sb"] == 2


def test_a_kept_none_answer_is_screened_once_for_the_residual_rule(tmp_path: Path) -> None:
    by_pass = [{"ra": p["ra"], "dec": p["dec"], "mjd": p["mjd"]} for p in PLACES]
    meta: dict[str, Any] = {
        "status": "ok",
        "params": {"dmin": 250.0, "dmax": 2000.0},
        "passes": [{"pass_mjd0": p["mjd0"], "pass_mjd1": p["mjd1"]} for p in PLACES],
        "candidates": [{"d": 1267.5, "d_lo": 1267.5, "d_hi": 2000.0, "ra": 1.0, "dec": 1.0, "ok": True, "by_pass": by_pass}],
    }
    idn = fpx_known.FpxIdentifier(FakeStore(meta), tmp_path, client=fake_jpl())
    idn._keep("deep", "k" * 24, {0: {"state": "none", "at": [1.0, 1.0, 1267.5]}})  # kept before the rule existed
    asyncio.run(idn.sweep())
    kept = idn.cached("deep", "k" * 24)
    assert kept is not None and kept[0]["state"] == "residual"


def _echo_places(strong: int) -> list[dict[str, Any]]:
    """Sedna's places in pass 0; the other passes 0.1° off it (noise lined up along an orbit through it); `strong`: the strongest pass."""
    out = [dict(p) for p in PLACES]
    for p in out[1:]:
        p["ra"] += 0.1
    for j, p in enumerate(out):
        p["sn"] = 7.7 if j == strong else 3.5
    return out


def test_a_known_body_in_the_strongest_pass_only_is_its_echo() -> None:
    """peak:75166a5e0d64 (19 Nov 2026): Eris in its first pass, S/N 7.7 of 8.8; the other passes away from Eris."""
    out = asyncio.run(fpx_known.identify(fake_jpl(), _echo_places(0), 81.7, 81.7))
    assert out["state"] == "ok" and out["echo"] is True and out["pass"] == 0 and out["name"].startswith("90377 Sedna")
    assert out["seps"][0] < 2.0 and max(out["seps"]) > fpx_known.MATCH_ARCSEC
    assert out["words"].startswith("an echo of Sedna (JPL), not a new body")


def test_a_known_body_in_a_weaker_pass_only_is_not_an_echo() -> None:
    out = asyncio.run(fpx_known.identify(fake_jpl(), _echo_places(1), 81.7, 81.7))
    assert out["state"] == "none" and out["echo_checked"] is True


def test_a_deep_none_kept_before_the_echo_rule_is_asked_once_more(tmp_path: Path) -> None:
    places = _echo_places(0)
    by_pass = [{"ra": p["ra"], "dec": p["dec"], "mjd": p["mjd"], "sn": p["sn"]} for p in places]
    meta: dict[str, Any] = {
        "status": "ok",
        "params": {"dmin": 30.0, "dmax": 300.0},
        "passes": [{"pass_mjd0": p["mjd0"], "pass_mjd1": p["mjd1"]} for p in places],
        "candidates": [{"d": 81.7, "ra": places[1]["ra"], "dec": places[1]["dec"], "ok": True, "by_pass": by_pass}],
    }
    old = {0: {"state": "none", "screened": True, "at": [places[1]["ra"], places[1]["dec"], 81.7]}}
    assert not fpx_known.settled("deep", old[0]) and not fpx_known.settled("distant", old[0])
    assert fpx_known.settled("distant", {**old[0], "big_checked": True})
    idn = fpx_known.FpxIdentifier(FakeStore(meta), tmp_path, client=fake_jpl())
    idn._keep("deep", "k" * 24, old)
    asyncio.run(idn.sweep())
    kept = idn.cached("deep", "k" * 24)
    assert kept is not None and kept[0]["state"] == "ok" and kept[0]["echo"] is True
    assert ("deep", "k" * 24) in idn.complete


def test_eris_left_out_by_sbident_is_found_by_its_own_ephemeris_and_its_echo_is_known() -> None:
    """peak:75166a5e0d64 (19 Nov 2026): Eris 15″ from the first pass of a deep sum, S/N 7.7 of 8.8; sbident does not list Eris."""
    places = _echo_places(0)
    out = asyncio.run(fpx_known.identify(fake_jpl(eris=[places[0]], sb_sedna=False), places, 81.7, 81.7))
    assert out["state"] == "ok" and out["echo"] is True and out["name"].startswith("136199 Eris")
    assert out["words"].startswith("an echo of Eris (JPL), not a new body")


def test_a_lead_far_from_every_large_body_is_none_and_says_they_were_looked_at() -> None:
    out = asyncio.run(fpx_known.identify(fake_jpl(sb_sedna=False), _echo_places(1), 81.7, 81.7))
    assert out["state"] == "none" and out["big_checked"] is True and out["echo_checked"] is True


def test_a_none_kept_before_the_large_bodies_is_looked_at_for_them_alone(tmp_path: Path) -> None:
    places = _echo_places(0)
    by_pass = [{"ra": p["ra"], "dec": p["dec"], "mjd": p["mjd"], "sn": p["sn"]} for p in places]
    meta: dict[str, Any] = {
        "status": "ok",
        "params": {"dmin": 30.0, "dmax": 300.0},
        "passes": [{"pass_mjd0": p["mjd0"], "pass_mjd1": p["mjd1"]} for p in places],
        "candidates": [{"d": 81.7, "ra": places[1]["ra"], "dec": places[1]["dec"], "ok": True, "by_pass": by_pass}],
    }
    old = {0: {"state": "none", "screened": True, "echo_checked": True, "at": [places[1]["ra"], places[1]["dec"], 81.7]}}
    assert not fpx_known.settled("deep", old[0])
    asked: list[str] = []
    inner = fake_jpl(eris=[places[0]], sb_sedna=False)

    async def handler(request: httpx.Request) -> httpx.Response:
        asked.append("sb" if "sb_ident" in str(request.url) else "hz")
        return await inner._transport.handle_async_request(request)  # type: ignore[attr-defined]

    idn = fpx_known.FpxIdentifier(FakeStore(meta), tmp_path, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    idn._keep("deep", "k" * 24, old)
    asyncio.run(idn.sweep())
    kept = idn.cached("deep", "k" * 24)
    assert kept is not None and kept[0]["state"] == "ok" and kept[0]["echo"] is True and kept[0]["name"].startswith("136199 Eris")
    assert "sb" not in asked  # sbident is not asked again for an old answer


def test_a_lead_after_eight_peaks_that_are_not_leads_is_looked_up(tmp_path: Path) -> None:
    """The second echo of Eris in its scan was its tenth peak: the first eight leads are looked up, not the first eight peaks."""
    by_pass = [{"ra": p["ra"], "dec": p["dec"], "mjd": p["mjd"]} for p in PLACES]
    noise = {"d": 83.0, "ra": 1.0, "dec": 1.0, "ok": False, "by_pass": by_pass}
    meta: dict[str, Any] = {
        "status": "ok",
        "params": {"dmin": 30.0, "dmax": 300.0},
        "passes": [{"pass_mjd0": p["mjd0"], "pass_mjd1": p["mjd1"]} for p in PLACES],
        "candidates": [*([noise] * 9), {"d": 83.0, "ra": PLACES[1]["ra"], "dec": PLACES[1]["dec"], "ok": True, "by_pass": by_pass}],
    }
    assert fpx_known.lead_indices(meta["candidates"]) == [9]
    idn = fpx_known.FpxIdentifier(FakeStore(meta), tmp_path, client=fake_jpl())
    asyncio.run(idn.sweep())
    kept = idn.cached("deep", "k" * 24)
    assert kept is not None and kept[9]["state"] == "ok" and kept[9]["name"].startswith("90377 Sedna")
    assert idn.annotate("deep", "k" * 24, meta)["candidates"][9]["known"]["state"] == "ok"
