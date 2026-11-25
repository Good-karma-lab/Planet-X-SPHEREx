from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from service import shadow
from service.products.sxp import encode
from service.products.versions import VERSIONS

N = 60
PTS = [{"f": k, "x": 20.0 + 3 * k, "y": 30.0, "z": 8.0, "how": "found"} for k in (0, 2, 3, 5)]
TRACK = {"n": 4, "n_found": 4, "method": "orbit", "verified": True, "z_together": 12.0, "pts": PTS}


@pytest.fixture
def store(tmp_path: Path) -> Path:
    with sqlite3.connect(tmp_path / "findings.db") as c:
        c.execute("CREATE TABLE finds (id TEXT PRIMARY KEY, kind TEXT, data TEXT, scans TEXT, ra REAL, dec REAL)")
        c.execute("CREATE TABLE idents (find_id TEXT, catalogue TEXT, state TEXT)")
    d = tmp_path / "products" / "filmsearch" / VERSIONS["filmsearch"] / "ab" / "abkey"
    d.mkdir(parents=True)
    frames = [{"visit": k, "det": 1, "mjd": 100.0 + k} for k in range(6)]
    (d / "meta.json").write_text(json.dumps({"film": "fifilm", "frames": frames, "tracks": [TRACK]}))
    shadow.writer(tmp_path).close()
    return tmp_path


def add_find(store: Path, fid: str, kind: str, data: dict[str, Any], scans: list[str]) -> None:
    with sqlite3.connect(store / "findings.db") as c:
        c.execute("INSERT INTO finds VALUES (?,?,?,?,10.0,5.0)", (fid, kind, json.dumps(data), json.dumps(scans)))


def rows_of(store: Path) -> list[dict[str, Any]]:
    with shadow.read_only(shadow.db_path(store)) as c:
        return [dict(r) for r in c.execute("SELECT * FROM auto_verdicts ORDER BY id")]


def put_film(store: Path) -> None:
    z = np.zeros((6, N, N), np.float32)
    for p in PTS:
        z[p["f"], int(p["y"]), int(p["x"])] = 10.0
    m = np.clip(np.round(1 + 254 * np.clip((z + 5) / 20, 0, 1)), 1, 255).astype(np.uint8)
    d = store / "products" / "film" / VERSIONS["film"] / "fi" / "fifilm"
    d.mkdir(parents=True)
    (d / "film.sxp").write_bytes(encode({"motion": m, "sky": m}, {}))


def test_a_track_is_scored_from_the_search_and_the_film(store: Path) -> None:
    add_find(store, "film:a", "film", {"track": TRACK}, ["abkey"])
    (r,) = shadow.score_batch(str(store), 10, True)
    assert r["score_z"] == 12.0 and r["score_share_z"] == pytest.approx(12.0 * 4 / 6)
    assert r["would_hide"] == 0 and r["score_gain_med"] is None and r["inputs_missing"] == "maps,nulls" and r["backlog"] == 1
    put_film(store)
    (r,) = shadow.score_batch(str(store), 10, False)
    assert r["score_gain_med"] == pytest.approx(10.0, abs=0.1) and r["inputs_missing"] == "maps:8bit,nulls"


def test_under_the_rule_a_track_would_be_hidden_and_a_lost_search_is_said(store: Path) -> None:
    low = {**TRACK, "z_together": 9.0}
    add_find(store, "film:low", "film", {"track": low}, ["abkey"])
    add_find(store, "film:lost", "film", {"track": {**TRACK, "pts": PTS[:3]}}, ["abkey", "other"])
    got = {r["find_id"]: r for r in shadow.score_batch(str(store), 10, False)}
    assert got["film:low"]["would_hide"] == 1
    assert got["film:lost"]["would_hide"] is None and "search" in got["film:lost"]["inputs_missing"]


@pytest.mark.parametrize(
    ("search", "d0", "det", "sn", "hide", "why"),
    [
        ("distant", 256.0, "123", 5.9, 1, ""),
        ("distant", 256.0, "123", 6.0, 0, ""),
        ("distant", 32.0, "123", 5.1, None, "rule:near"),
        ("distant", 256.0, "4", 5.1, None, "rule:detector"),
        ("distant", 256.0, "12", 5.1, None, "rule:detector"),
        ("distant", 32.0, "4", 5.1, None, "rule:detector,rule:near"),
        ("deep", 256.0, "123", 5.1, None, "rule:sum of passes"),
        ("distant", 256.0, "123", None, None, "phot"),
        ("distant", None, "123", 7.0, None, "rule:near"),
    ],
)
def test_a_peak_is_judged_only_where_the_rule_was_checked(
    store: Path, search: str, d0: float | None, det: str, sn: float | None, hide: int | None, why: str
) -> None:
    peak = {"phot_sn": sn, "bydet": {k: [0.1, 9] for k in det}, "knob": {"d": [d0, 2000.0] if d0 else []}}
    add_find(store, "peak:a", "peak", {"peak": peak, "search": search}, ["pk"])
    (r,) = shadow.score_batch(str(store), 10, False)
    assert (r["would_hide"], r["inputs_missing"], r["score_phot"]) == (hide, why, sn)


def test_the_table_only_grows_and_a_find_is_judged_once_per_measurement(store: Path) -> None:
    s = shadow.Shadow(None, store)
    add_find(store, "film:a", "film", {"track": TRACK}, ["abkey"])
    s.add(shadow.score_batch(str(store), 10, False))
    assert shadow.score_batch(str(store), 10, False) == []
    with sqlite3.connect(shadow.db_path(store)) as c:
        for q in ("UPDATE auto_verdicts SET would_hide=1", "DELETE FROM auto_verdicts"):
            with pytest.raises(sqlite3.IntegrityError):
                c.execute(q)
    with sqlite3.connect(store / "findings.db") as c:
        c.execute("UPDATE finds SET data=?", (json.dumps({"track": {**TRACK, "z_together": 13.0}}),))
    s.add(shadow.score_batch(str(store), 10, False))
    assert len(rows_of(store)) == 2


def test_off_by_default_and_off_on_a_node(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SKY_SHADOW", raising=False)
    assert shadow.start(None, tmp_path) is None and not shadow.db_path(tmp_path).exists()
    monkeypatch.setenv("SKY_SHADOW", "1")
    monkeypatch.setenv("SKY_NODE_MAIN", "http://main")
    monkeypatch.setenv("SKY_NODE_SECRET", "x")
    assert not shadow.enabled()


def test_the_loop_counts_what_was_there_then_reports(store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        add_find(store, "film:a", "film", {"track": TRACK}, ["abkey"])
        add_find(store, "film:b", "film", {"track": {**TRACK, "z_together": 5.0}}, ["abkey"])
        with sqlite3.connect(store / "findings.db") as c:
            c.execute("INSERT INTO idents VALUES ('film:b', 'sbident', 'ok')")
        with sqlite3.connect(store / "product.sqlite") as c:
            c.execute("CREATE TABLE acc_task_answers (user_id TEXT, kind TEXT, finding_id TEXT, answer TEXT)")
            c.executemany("INSERT INTO acc_task_answers VALUES (?,'moves','film:b','moves')", [("u1",), ("u2",)])
        before = (store / "findings.db").read_bytes()
        monkeypatch.setattr(shadow.Shadow, "room", lambda self: True)
        monkeypatch.setattr(shadow, "START_S", 0.0)
        monkeypatch.setattr(shadow, "EVERY_S", 0.0)
        monkeypatch.setattr(shadow, "DUTY", 0.0)
        slept = asyncio.sleep
        monkeypatch.setattr(shadow.asyncio, "sleep", lambda s: slept(0.001))
        s = shadow.Shadow(None, store)
        task = asyncio.create_task(s.loop())
        for _ in range(2000):
            await slept(0.01)
            with shadow.read_only(shadow.db_path(store)) as c:
                if c.execute("SELECT 1 FROM shadow_events WHERE what='day'").fetchone():
                    break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (store / "findings.db").read_bytes() == before  # read only
        rep = shadow.report(store, store / "product.sqlite")
        assert rep["film"]["judged"] == 2 and rep["film"]["backlog"]["would_hide"] == 1 and rep["film"]["last_day"]["judged"] == 0
        assert rep["film"]["jpl_known"]["would_hide"] == ["film:b"] and rep["film"]["people_real"]["would_hide"] == ["film:b"]
        assert len(rep["stop"]) == 2 and any("look" in x for x in rep["stop"])
        assert "u1" not in json.dumps(rep)

    asyncio.run(run())


def test_the_loop_waits_while_people_are_at_work_and_stops_after_too_many_errors(store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Sch:
        left = 3

        def person_waiting(self) -> bool:
            self.left -= 1
            return self.left >= 0

        def person_active(self) -> bool:
            return False

    async def run() -> None:
        slept = asyncio.sleep
        waits: list[float] = []

        async def nap(s: float) -> None:
            waits.append(s)
            await slept(0)

        monkeypatch.setattr(shadow.asyncio, "sleep", nap)
        s = shadow.Shadow(SimpleNamespace(scheduler=Sch()), store)
        assert await s.work(os.path.basename, "a/b") == "b"
        assert waits.count(shadow.PAUSE_S) == 3
        monkeypatch.setattr(shadow.Shadow, "room", lambda self: True)
        (store / "findings.db").unlink()
        await asyncio.wait_for(s.loop(), 10)
        assert s.errors == shadow.MAX_ERRORS

    asyncio.run(run())


def test_the_share_counts_the_frames_between_the_ends() -> None:
    assert shadow.share(3, [{"f": 1}, {"f": 4}], [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]) == 0.75
    assert shadow.share(3, [], [1.0]) is None


def test_a_peak_that_jpl_names_counts_as_known(store: Path) -> None:
    from service.fpx_known import FpxIdentifier

    p = FpxIdentifier(None, store / "fpx_known", client=object()).path("distant", "pk")  # type: ignore[arg-type]
    p.parent.mkdir()
    p.write_text(json.dumps({"0": {"state": "ok", "at": [10.0, 5.0, 300.0]}}))
    add_find(store, "peak:a", "peak", {"peak": {"phot_sn": 5.5}, "search": "distant"}, ["pk"])
    f = {"id": "peak:a", "kind": "peak", "ra": 10.0, "dec": 5.0, "scans": ["pk"], "data": {"search": "distant"}}
    assert shadow.known_peaks(store, [f]) == {"peak:a"}
    assert shadow.known_peaks(store, [{**f, "ra": 11.0}]) == set()
