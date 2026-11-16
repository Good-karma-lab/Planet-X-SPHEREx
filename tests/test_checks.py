from __future__ import annotations

import asyncio
import functools
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from service import checks as ck
from service import known_field as kf
from service.routes_checks import make_checks_router

BARNARD = ["4472832130942575872", 269.44850252543836, 4.739420051112412, -801.551, 10362.394, 546.9759, 8.19]
FAINT = ["111", 269.4600, 4.7300, 1.0, -2.0, 0.5, 15.5]
PASSES = [
    {"index": 0, "mjd0": 60915.0, "mjd1": 60917.0, "mjd_mid": 60916.0},
    {"index": 1, "mjd0": 61118.0, "mjd1": 61120.0, "mjd_mid": 61119.0},
    {"index": 2, "mjd0": 61274.0, "mjd1": 61276.0, "mjd_mid": 61275.0},
]
GRID = {"ra0": 269.4485, "dec0": 4.74, "step_arcsec": 3.075, "n": 400}


def sync(fn: Any) -> Any:
    @functools.wraps(fn)
    def run(*a: Any, **kw: Any) -> None:
        asyncio.run(fn(*a, **kw))

    return run


def star_marks(row: list[Any], shift_arcsec: float = 0.0) -> list[dict[str, Any]]:
    """Marks on a Gaia star at the middle of every pass (optionally moved north by `shift_arcsec`)."""
    a = np.array([row[1:6]], float)
    pr, pd = kf.propagate(a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4], 2016.0, [p["mjd_mid"] for p in PASSES])
    return [
        {"pass": p["index"], "ra": float(pr[0, k]), "dec": float(pd[0, k]) + shift_arcsec / 3600.0, "mjd": p["mjd_mid"]}
        for k, p in enumerate(PASSES)
    ]


def moving_marks(rate_arcsec_yr: float = 3.0, wobble: float = 0.0) -> list[dict[str, Any]]:
    """Marks of a point that moves north from an empty place."""
    out = []
    for k, p in enumerate(PASSES):
        yrs = (p["mjd_mid"] - PASSES[0]["mjd_mid"]) / 365.25
        east = wobble if k == 1 else 0.0
        out.append({"pass": k, "ra": 269.40 + east / 3600.0, "dec": 4.70 + rate_arcsec_yr * yrs / 3600.0, "mjd": p["mjd_mid"]})
    return out


def test_two_passes_and_line() -> None:
    assert ck.check_two_passes(moving_marks())[0] == "pass"
    assert ck.check_two_passes(moving_marks()[:1])[0] == "fail"
    assert ck.check_two_passes([])[0] == "fail"
    assert ck.check_straight_line(moving_marks())[0] == "pass"
    assert ck.check_straight_line(moving_marks()[:2])[0] == "warn"  # two passes always make a line
    assert ck.check_straight_line(moving_marks(wobble=5.0))[0] == "warn"
    assert ck.check_straight_line(moving_marks(wobble=14.0))[0] == "fail"
    assert ck.check_straight_line(moving_marks(rate_arcsec_yr=0.5))[0] == "warn"  # does not move
    _, detail = ck.check_straight_line(moving_marks())
    assert "3.00″/yr toward 0°" in detail


def test_catalogue_check_fails_on_a_star_that_follows_the_marks() -> None:
    part = {"state": "ok", "rows": [BARNARD, FAINT]}
    state, detail = ck.check_catalogue(part, star_marks(BARNARD), 2016.0, ck.gaia_label)
    assert state == "fail" and "4472832130942575872" in detail and "10.39″/yr" in detail
    # marks that move like nothing in the catalogue pass; a star at one mark only warns
    assert ck.check_catalogue(part, moving_marks(), 2016.0, ck.gaia_label)[0] == "pass"
    one = moving_marks()
    one[1] = {**one[1], "ra": FAINT[1], "dec": FAINT[2]}
    state, detail = ck.check_catalogue(part, one, 2016.0, ck.gaia_label)
    assert state == "warn" and "pass 1" in detail
    # a catalogue that did not answer is never a pass
    assert ck.check_catalogue({"state": "unavailable", "error": "ReadTimeout"}, moving_marks(), 2016.0, ck.gaia_label)[0] == "no_answer"


def test_edge_and_bright_star() -> None:
    ok = {"state": "ok", "rows": [FAINT]}
    assert ck.check_edge_bright(moving_marks(), GRID, ok, ok | {"rows": []})[0] == "pass"
    edge = [{**m, "ra": GRID["ra0"] + 0.168} for m in moving_marks()]  # 603″ east of the centre of a 615″ half-field
    assert ck.check_edge_bright(edge, GRID, ok, ok)[0] == "fail"
    bright = {"state": "ok", "rows": [BARNARD]}
    assert ck.check_edge_bright(star_marks(BARNARD), GRID, bright, ok)[0] == "fail"  # on the saturated star
    state, detail = ck.check_edge_bright(star_marks(BARNARD, 40.0), GRID, bright, ok)
    assert state == "warn" and "40″" in detail
    down = {"state": "unavailable"}
    assert ck.check_edge_bright(moving_marks(), GRID, down, down)[0] == "no_answer"


def test_track_miss_and_above_bar() -> None:
    mark = {"ra": 10.0, "dec": 0.0, "pass": 0}
    body = ["9078 (1994 PB2)", 10.0 + 100.0 / 3600.0, 0.0, 18.8, -20.0, 0.0]  # 100″ east at mid-pass, moving west 20″/h
    assert ck.track_miss(body, mark, 60000.0, 60001.0, 60000.5) == pytest.approx(0.0, abs=0.01)  # it crosses the mark 5 h later
    assert ck.track_miss(body, mark, 60000.4, 60000.6, 60000.5) == pytest.approx(52.0, abs=0.1)  # a short pass ends before that
    marks = moving_marks()
    track = {"passes": [0, 1, 2], "ra": [m["ra"] for m in marks], "dec": [m["dec"] for m in marks], "snr": [12.0, 9.5, 10.1]}
    state, detail = ck.check_above_bar(marks, {"tracks": [track]})
    assert state == "pass" and "10–12σ" in detail
    assert ck.check_above_bar(marks, {"tracks": []})[0] == "warn"
    assert ck.check_above_bar(marks, None)[0] == "no_answer"


def test_neowise_path_and_orbit_answers() -> None:
    marks = moving_marks(rate_arcsec_yr=3.0)
    t0 = marks[0]["mjd"]
    along = [{"ra": 269.40, "dec": 4.70 + 3.0 * (m - t0) / 365.25 / 3600.0, "mjd": m} for m in np.arange(57000.0, 60400.0, 183.0)]
    assert ck.neowise_path(marks, along)[0] == "pass"
    fixed = [{"ra": 269.40, "dec": 4.70, "mjd": m} for m in np.arange(57000.0, 60400.0, 183.0)]
    assert ck.neowise_path(marks, fixed)[0] == "fail"
    assert ck.neowise_path(marks, [])[0] == "warn"
    res = {"status": "ok", "frames": 994, "epochs_used": 21, "epochs_unmasked": 13, "years": [2014.1, 2024.1]}
    out = {**res, "flux_mjy": -0.0005, "err_mjy": 0.0031, "would_be_sigma": 11.0, "ruled_out": True, "seen": False}
    state, detail = ck.neowise_orbit_answer(out, {"flux_mjy": 0.034})
    assert state == "fail" and "-0.0005 ± 0.0031 mJy" in detail and "11.0σ" in detail
    assert ck.neowise_orbit_answer({**out, "ruled_out": False, "would_be_sigma": 2.0}, {"flux_mjy": 0.006})[0] == "warn"
    assert ck.neowise_orbit_answer({**out, "ruled_out": False, "seen": True, "flux_mjy": 0.03}, {"flux_mjy": 0.034})[0] == "pass"
    assert ck.neowise_orbit_answer({"status": "no-frames", "frames": 3}, {})[0] == "warn"


class FakeKnown:
    """Stands in for KnownField: the catalogue rows it is given; JPL fails `jpl_fail` times, then answers."""

    def __init__(self, gaia: dict[str, Any], simbad: dict[str, Any], bodies: list[list[Any]], jpl_fail: int = 0) -> None:
        self._gaia, self._simbad, self.bodies, self.jpl_fail = gaia, simbad, bodies, jpl_fail

    async def gaia(self, *a: Any) -> dict[str, Any]:
        return self._gaia

    async def simbad(self, *a: Any) -> dict[str, Any]:
        return self._simbad

    async def jpl_at(self, *a: Any) -> dict[str, Any]:
        if self.jpl_fail:
            self.jpl_fail -= 1
            return {"state": "unavailable", "error": "HTTP 502"}
        return {"state": "ok", "rows": self.bodies}


def make(tmp_path: Path, known: Any, irsa: str = "ra,dec,mjd\n", result: dict[str, Any] | None = None) -> ck.Checks:
    store = SimpleNamespace(read_meta=lambda kind, key: {"passes": PASSES, "grid": GRID} if key == "f1" else None)
    # every pass has one visit of a day, in the exposure index
    index = SimpleNamespace(containing=lambda ra, dec, t0, t1: [SimpleNamespace(mjd0=t0 + 0.5, mjd1=t0 + 1.5)])
    movers = SimpleNamespace(key_of=lambda k: "m" + k)
    st = SimpleNamespace(cfg=SimpleNamespace(store_dir=tmp_path), store=store, movers=movers, index=index)
    if result is not None:
        (tmp_path / "movers").mkdir(exist_ok=True)
        (tmp_path / "movers" / "mf1.json").write_text(json.dumps(result))
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=irsa)))
    return ck.Checks(st, known, client)


async def finished(job: ck.Job) -> dict[str, Any]:
    assert job.task is not None
    await job.task
    return job.view()


def states(view: dict[str, Any]) -> dict[str, str]:
    return {c["id"]: c["state"] for c in view["checks"]}


@sync
async def test_job_on_a_known_star_cannot_be_submitted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ck, "JPL_RETRY_S", 0.0)
    known = FakeKnown({"state": "ok", "rows": [BARNARD, FAINT]}, {"state": "ok", "rows": []}, [])
    marks = [{k: v for k, v in m.items() if k != "mjd"} for m in star_marks(BARNARD)]  # the dates come from the field's passes
    c = make(tmp_path, known, result={"tracks": []})
    job = c.start({"field_key": "f1", "ra": 269.4485, "dec": 4.74, "marks": marks})
    assert job.view()["state"] == "running" and job.view()["can_submit"] is False
    view = await finished(job)
    got = states(view)
    assert got["gaia"] == "fail" and got["two_passes"] == "pass" and got["straight_line"] == "pass" and got["jpl"] == "pass"
    assert got["edge_bright"] == "fail" and got["simbad"] == "pass" and got["above_bar"] == "warn"
    assert view["state"] == "done" and view["can_submit"] is False
    # kept on disk: another instance has the same answers under the same job id
    again = make(tmp_path, known).get(job.id)
    assert again is not None and states(again.view()) == got


@sync
async def test_job_on_an_unknown_mover_can_be_submitted_with_comments(tmp_path: Path) -> None:
    marks = moving_marks()
    track = {"passes": [0, 1, 2], "ra": [m["ra"] for m in marks], "dec": [m["dec"] for m in marks], "snr": [12.0, 9.5, 10.1]}
    known = FakeKnown({"state": "ok", "rows": [FAINT]}, {"state": "ok", "rows": []}, [])
    c = make(tmp_path, known, result={"tracks": [track]})
    view = await finished(c.start({"field_key": "f1", "ra": 269.4, "dec": 4.7, "marks": marks}))
    got = states(view)
    assert all(got[k] == "pass" for k in ("two_passes", "straight_line", "gaia", "simbad", "jpl", "edge_bright", "above_bar"))
    assert got["neowise"] == "warn" and view["needs_comment"] == ["neowise"]  # nothing in NEOWISE along the path
    assert view["can_submit"] is True


@sync
async def test_jpl_body_fails_and_a_silent_catalogue_blocks_until_it_answers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ck, "JPL_RETRY_S", 0.0)
    marks = [{k: v for k, v in m.items() if k != "mjd"} for m in moving_marks()]  # dated by the pass: JPL is asked over the whole pass
    body = ["9078 (1994 PB2)", marks[0]["ra"] + 100.0 / 3600.0, marks[0]["dec"], 18.8, -20.0, 0.0]
    known = FakeKnown({"state": "unavailable", "error": "ReadTimeout"}, {"state": "ok", "rows": []}, [body], jpl_fail=9)
    c = make(tmp_path, known)
    request = {"field_key": "f1", "ra": 269.4, "dec": 4.7, "marks": marks}
    view = await finished(c.start(request))
    got = states(view)
    assert got["gaia"] == "no_answer" and got["jpl"] == "no_answer" and got["above_bar"] == "no_answer"
    assert view["can_submit"] is False
    # asked again: only the checks without an answer run; now JPL answers and names the body
    known.jpl_fail = 0
    known._gaia = {"state": "ok", "rows": [FAINT]}
    job = c.start(request)
    view = await finished(job)
    got = states(view)
    assert got["gaia"] == "pass" and got["jpl"] == "fail"
    assert "9078 (1994 PB2)" in next(x["detail"] for x in view["checks"] if x["id"] == "jpl")
    assert view["can_submit"] is False


@sync
async def test_orbit_runs_the_archive_check(tmp_path: Path) -> None:
    known = FakeKnown({"state": "ok", "rows": []}, {"state": "ok", "rows": []}, [])
    c = make(tmp_path, known)
    seen: list[dict[str, Any]] = []

    def fake_archive(orbit: dict[str, Any], cache: Path, say: Any) -> dict[str, Any]:
        seen.append(orbit)
        say("NEOWISE exposures read: 10 of 994")
        res = {"status": "ok", "frames": 994, "epochs_used": 21, "epochs_unmasked": 13, "years": [2014.1, 2024.1]}
        return {**res, "flux_mjy": -0.0005, "err_mjy": 0.0031, "would_be_sigma": 11.0, "ruled_out": True, "seen": False}

    c.orbit_check = fake_archive
    orbit = {"ra": 60.683, "dec": 8.331, "mjd": 61089.42, "d_au": 394.0, "mu": [-0.57, 0.022], "flux_mjy": 0.034}
    mark = {"pass": 0, "ra": 60.94, "dec": 8.39, "mjd": 60906.0}
    view = await finished(c.start({"field_key": "", "ra": 60.94, "dec": 8.39, "marks": [mark], "orbit": orbit}))
    got = states(view)
    assert seen == [orbit] and got["neowise"] == "fail" and got["two_passes"] == "fail" and view["can_submit"] is False


def test_routes(tmp_path: Path) -> None:
    known = FakeKnown({"state": "ok", "rows": [FAINT]}, {"state": "ok", "rows": []}, [])
    st = SimpleNamespace(cfg=SimpleNamespace(store_dir=tmp_path), store=None, movers=None)
    st.checks = make(tmp_path, known)
    app = FastAPI()
    app.include_router(make_checks_router(st))
    with TestClient(app) as c:
        body = {"field_key": "f1", "ra": 269.4, "dec": 4.7, "marks": [{k: v for k, v in m.items() if k != "mjd"} for m in moving_marks()]}
        r = c.post("/api/checks", json=body)
        assert r.status_code == 200 and set(r.json()) >= {"job"}
        job = r.json()["job"]
        for _ in range(200):
            v = c.get(f"/api/checks/{job}").json()
            if v["state"] == "done":
                break
        assert v["state"] == "done" and set(v) >= {"state", "checks", "can_submit"}
        assert st.checks_status(job) == v and v["field_key"] == "f1" and v["ra"] == 269.4 and st.checks_status("nope") is None
        assert [x["id"] for x in v["checks"]] == list(ck.CHECK_IDS)
        assert all(set(x) >= {"id", "state", "detail"} and x["detail"] for x in v["checks"])
        assert c.post("/api/checks", json=body).json()["job"] == job  # the same request is the same job
        assert c.get("/api/checks/0123456789abcdef0123").status_code == 404
        assert c.post("/api/checks", json={**body, "marks": [{"pass": 0, "ra": 400, "dec": 0}]}).status_code == 422
