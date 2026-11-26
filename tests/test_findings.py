# ruff: noqa: E501
"""Findings: the finds of the result files, the database, the identification rows and the plain verdicts."""

from __future__ import annotations

import asyncio
import json
import math
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from service.findings import SCHEMA, FindingsDB, collect, query, status_of, verdict_of
from service.products.store import product_key

TARGETS = [{"n": "Barnard's Star", "ra": 269.45, "dec": 4.69}]


def _store(tmp_path: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    for d in ("movers", "browndwarfs", "red", "anomalies", "pairs"):
        (tmp_path / d).mkdir()
    fk = "f" * 24
    rows = [{"key": fk, "kind": "field", "params": {"ra": 269.45, "dec": 4.69, "fov": 20}, "created": 1.0, "complete": True}]
    keys = {fk: {"movers": "m1", "pairs": "p1", "browndwarfs": "b1", "anomalies": "a1"}}
    track = {
        "speed": 10.66,
        "pa": 353.0,
        "ra_2025_9": 269.4463,
        "dec_2025_9": 4.7677,
        "snr": [5.0, None, 7.0],
        "check": "same",
        "sigma": 13.2,
        "mjd": [1.0, 2.0, 3.0],
    }
    pair = {"components": [{"ra": 269.5, "dec": 4.8, "snr": 9.0}, {"ra": 269.5001, "dec": 4.8002, "snr": 8.0}], "change_sigma": 4.4}
    (tmp_path / "movers" / "m1.json").write_text(
        json.dumps({"tracks": [track], "pairs": [pair], "passes": 3, "mjd": [1.0, 2.0, 3.0], "chance": {"mean": 0.0, "trials": 20}})
    )
    (tmp_path / "browndwarfs" / "b1.json").write_text(
        json.dumps({"candidates": [{"ra": 269.6, "dec": 4.6, "pm": 2.0, "snr_long": 11.0, "sigma": 5.0}], "known": [], "slow": []})
    )
    (tmp_path / "red" / f"{product_key('red', {'field': fk})}.json").write_text(
        json.dumps({"red": [{"ra": 269.3, "dec": 4.5, "colour": 3.0, "snr_long": 20.0}]})
    )
    (tmp_path / "anomalies" / "a1.json").write_text(
        json.dumps({"anomalies": [{"ra": 269.2, "dec": 4.4, "score": 4.9, "snr": 15.0, "drivers": []}], "threshold": 1.8, "edges": [0.75]})
    )
    return rows, keys


def test_collect_lists_every_kind_and_a_signature(tmp_path: Path) -> None:
    rows, keys = _store(tmp_path)
    finds, sig = collect(tmp_path, rows, keys, TARGETS)
    assert sorted(f["kind"] for f in finds) == ["anomaly", "browndwarf", "mover", "pair", "red"]
    assert {f["place"] for f in finds} == {"Barnard's Star"}
    assert collect(tmp_path, rows, keys, TARGETS)[1] == sig  # unchanged files: the same signature
    (tmp_path / "anomalies" / "a1.json").write_text("{}")
    assert collect(tmp_path, rows, keys, TARGETS)[1] != sig


def test_a_field_being_made_again_keeps_its_finds(tmp_path: Path) -> None:
    """The sweep strips a searched square's field; asked for again, it is made again and is not complete for that while (or for good,
    if a restart cut the build). Its finds come from its searches' result files, so they stay in Findings."""
    rows, keys = _store(tmp_path)
    want = sorted(f["id"] for f in collect(tmp_path, rows, keys, TARGETS)[0])
    rows = [{**r, "complete": False} for r in rows]
    assert sorted(f["id"] for f in collect(tmp_path, rows, keys, TARGETS)[0]) == want


def test_database_keeps_identification_when_a_find_is_rewritten_and_drops_vanished_finds(tmp_path: Path) -> None:
    rows, keys = _store(tmp_path)
    finds, _ = collect(tmp_path, rows, keys, TARGETS)
    db = FindingsDB(tmp_path / "findings.db", writer=True)
    assert db.write_finds(finds) == (5, 0)
    assert db.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    mover = next(f for f in finds if f["kind"] == "mover")
    db.put_ident(mover["id"], "simbad", "ok", "* Barnard's star", 0.4, "BY*", {})
    db.put_ident(mover["id"], "vsx", "unavailable", None, None, None, {"error": "timeout"})
    db.db.commit()
    assert db.write_finds([f for f in finds if f["kind"] != "red"]) == (0, 1)
    got = db.idents_of([mover["id"]])[mover["id"]]
    assert {i["catalogue"]: i["state"] for i in got} == {"simbad": "ok", "vsx": "unavailable"}
    # a find with catalogues not yet asked is pending; one that did not answer waits before it is asked again
    assert mover["id"] in {r["id"] for r in db.pending(10)}


def test_a_newer_schema_is_read_and_left_alone(tmp_path: Path) -> None:
    p = tmp_path / "findings.db"
    db = sqlite3.connect(p)
    db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    db.execute("INSERT INTO meta VALUES ('schema', ?)", (str(SCHEMA + 1),))
    db.commit()
    db.close()
    assert FindingsDB(p, writer=True).read_only


def test_query_status_and_verdict(tmp_path: Path) -> None:
    rows, keys = _store(tmp_path)
    finds, _ = collect(tmp_path, rows, keys, TARGETS)
    db = FindingsDB(tmp_path / "findings.db", writer=True)
    db.write_finds(finds)
    red = next(f for f in finds if f["kind"] == "red")
    asyncio.run(asyncio.sleep(0))
    db.put_ident(red["id"], "simbad", "none", None, None, None, {})
    db.put_ident(red["id"], "gaia", "unavailable", None, None, None, {"error": "x"})
    db.db.commit()
    out = query(db, None, "all", None, 100, 0)
    assert out["total"] == 5 and out["counts"]["mover"]["known"] == 1  # a track Gaia matches is known
    r = next(f for f in out["finds"] if f["id"] == red["id"])
    assert r["status"] == "unchecked" and "Not yet checked" in r["verdict"] and "Did not answer: Gaia DR3" in r["verdict"]
    assert query(db, "red", "known", None, 10, 0)["count"] == 0
    only = query(db, None, "unchecked", "Barnard's Star", 2, 1)
    assert only["count"] == 4 and len(only["finds"]) == 2 and out["counts"]["red"]["unchecked"] == 1
    assert status_of({"kind": "film", "data": {}}, [{"catalogue": "sbident", "state": "ok"}]) == "known"
    assert "known solar-system body" in verdict_of(
        {"kind": "film"}, [{"catalogue": "sbident", "label": "JPL", "state": "ok", "name": "433 Eros", "type": None, "sep": 3.0}], "known"
    )


def test_related_tables_are_replaced_not_duplicated_and_carry_versions(tmp_path: Path) -> None:
    rows, keys = _store(tmp_path)
    finds, _ = collect(tmp_path, rows, keys, TARGETS)
    mover = next(f for f in finds if f["kind"] == "mover")
    assert len(mover["positions"]) == 0 or mover["versions"]["movers"]
    db = FindingsDB(tmp_path / "findings.db", writer=True)
    db.write_finds(finds)
    db.write_finds(finds)  # the scans computed again: the same rows, once
    pair = next(f for f in finds if f["kind"] == "pair")
    n = db.db.execute("SELECT COUNT(*) FROM lightcurves WHERE find_id=?", (pair["id"],)).fetchone()[0]
    assert n == len(pair["lightcurve"])
    db.put_ident(mover["id"], "simbad", "ok", "x", 1.0, "BY*", {}, {"raw": {"a": 1}})
    out = query(db, "mover", "all", None, 5, 0)["finds"][0]
    assert out["versions"] and next(i for i in out["idents"] if i["catalogue"] == "simbad")["raw"] == {"raw": {"a": 1}}
    assert db.db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0] == "2"


class _Resp:
    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.body


def _jpl(rows: list[list[Any]]) -> dict[str, Any]:
    fields = [
        "Object name",
        'Dist. from center RA (")',
        'Dist. from center Dec (")',
        'RA rate ("/h)',
        'Dec rate ("/h)',
        "Visual magnitude (V)",
    ]
    return {"fields_first": fields, "data_first_pass": rows}


def _findings_with(body: dict[str, Any]) -> Any:
    from service.findings import Findings
    from service.upstream.breaker import Breaker

    class Client:
        async def get(self, url: str, params: dict[str, str], **_kw: object) -> _Resp:
            assert params["vmag-lim"] == "21.0"
            return _Resp(body)

    f = Findings.__new__(Findings)
    f.breaker = Breaker("JPL")
    f.catalogue = type("C", (), {"client": Client()})()
    return f


def test_sbident_needs_a_near_body_that_moves_like_the_find() -> None:
    # ours: 58 ″/h toward 90° (east). A body 3″ away moving 56 ″/h east matches; one 3″ away moving north does not; one 40″ away is too far.
    ok = _findings_with(_jpl([["8294 Takayuki", "3.0", "0.0", "56.0", "0.0", "19.5"], ["far", "40", "0", "58", "0", "18"]]))
    got = asyncio.run(ok.sbident(120.0, 10.0, 61000.0, 58.0, 90.0))
    assert got["state"] == "ok" and got["name"] == "8294 Takayuki" and got["body_rate"] == 56.0
    wrong = _findings_with(_jpl([["12 Victoria", "3.0", "0.0", "0.0", "40.0", "17"]]))
    got = asyncio.run(wrong.sbident(120.0, 10.0, 61000.0, 58.0, 90.0))
    assert got["state"] == "none" and "not like this find" in got["note"]
    crowded = _findings_with(_jpl([["x", "50", "0", "58", "0", "20"]] * 400))
    assert asyncio.run(crowded.sbident(120.0, 10.0, 61000.0, 58.0, 90.0))["state"] == "none"


def test_generic_simbad_types_and_pending_answers_explain_nothing() -> None:
    from service.findings import _ident_from

    assert _ident_from("simbad", {"state": "pending"})[0] == "unavailable"
    red = {"kind": "red", "data": {}}
    everyone = [{"catalogue": c, "state": "none"} for c in ("gaia", "twomass", "allwise", "vsx", "ned")]
    star = [{"catalogue": "simbad", "state": "ok", "type": "Star"}, *everyone]
    assert status_of(red, star) == "unexplained"  # all answered, a bare "Star" explains nothing
    assert status_of(red, [{"catalogue": "simbad", "state": "ok", "type": "Star"}]) == "unchecked"  # the others not asked yet
    assert status_of(red, [{"catalogue": "simbad", "state": "ok", "type": "Mira"}]) == "known"


def test_units_merge_gates_and_motion_agreement(tmp_path: Path) -> None:
    from service.findings import merge

    rows, keys = _store(tmp_path)
    bd = {"candidates": [{"ra": 133.8, "dec": -7.2, "pm": 7931.8, "snr_long": 20.0, "sigma": 12.0}], "known": [], "slow": []}
    (tmp_path / "browndwarfs" / "b1.json").write_text(json.dumps(bd))
    finds, _ = collect(tmp_path, rows, keys, TARGETS)
    dwarf = next(f for f in finds if f["kind"] == "browndwarf")
    assert abs(dwarf["rate"] - 7.9318) < 1e-6  # mas/yr from the scan, ″/yr here
    # the same object found at two field sizes is one find that lists both detections
    a = {"id": "x", "kind": "red", "place": "p", "fov": 20, "ra": 10.0, "dec": 5.0, "scans": ["a"], "data": {}, "snr": 5.0, "score": 1.0}
    b = {**a, "id": "y", "fov": 5, "ra": 10.0 + 1.0 / 3600, "scans": ["b"], "data": {}, "snr": 9.0}
    far = {**a, "id": "z", "ra": 10.01, "scans": ["c"], "data": {}}
    merged = merge([a, b, far])
    assert len(merged) == 2 and sorted(len(m["data"]["detections"]) for m in merged) == [1, 2]
    two = next(m for m in merged if len(m["data"]["detections"]) == 2)
    assert two["fov"] == 20 and two["snr"] == 9.0 and two["scans"] == ["a", "b"]
    # a mover whose proper motion a catalogue source shares is known; before the catalogues answer it is only "unchecked"
    mover = {"kind": "mover", "rate": 6.92, "data": {"track": {}}}
    gaia = {"catalogue": "gaia", "state": "ok", "raw": {"pmra": 6500.0, "pmdec": -2300.0}}
    assert status_of(mover, [gaia]) == "known"
    assert status_of(mover, []) == "unchecked"


def test_film_tracks_all_listed_with_their_own_control(tmp_path: Path) -> None:
    key = "ab" + "c" * 22
    d = tmp_path / "products" / "filmsearch"
    from service.products.versions import VERSIONS

    mdir = d / VERSIONS["filmsearch"] / key[:2] / key
    mdir.mkdir(parents=True)
    pts = [{"f": 0, "x": 10.0, "y": 10.0, "z": 6.0}]
    orbit = {
        "n": 5,
        "n_found": 3,
        "n_recovered": 2,
        "rate": 3.0,
        "pa": 90.0,
        "method": "orbit",
        "verified": True,
        "z_together": 9.0,
        "pts": pts,
    }
    line = {"n": 4, "n_found": 4, "rate": 9.0, "pa": 80.0, "method": "line", "verified": False, "z_together": 6.0, "pts": pts}
    meta = {
        "tracks": [orbit, line],
        "grid": {"ra0": 10.0, "dec0": 5.0, "step_arcsec": 3.0, "n": 20},
        "frames": [{"mjd": 61000.0}],
        "control": {"chance": {"5": 0.04}, "line_chance": {}},
    }
    (mdir / "meta.json").write_text(json.dumps(meta))
    rows = [{"key": key, "kind": "filmsearch", "params": {"ra": 10.0, "dec": 5.0, "fov": 60}, "created": 1.0, "complete": True}]
    finds, _ = collect(tmp_path, rows, {}, TARGETS)
    by = {f["data"]["track"]["method"]: f for f in finds}
    assert len(finds) == 2 and by["orbit"]["chance"] == 0.04 and not by["orbit"]["data"]["less_certain"]
    assert by["line"]["data"]["less_certain"] and by["line"]["chance"] == 0.0
    # the lab opens a film on the film's own field (warm), not on a new field centred on the track
    assert by["orbit"]["data"]["field_params"] == {"ra": 10.0, "dec": 5.0, "fov": 60.0}


def test_slow_motions_are_not_finds(tmp_path: Path) -> None:
    rows, keys = _store(tmp_path)
    slow_track = {
        "speed": 5.0,
        "ra_2025_9": 269.3,
        "dec_2025_9": 4.9,
        "snr": [5.0],
        "check": "none",
        "grade": "slow",
        "sigma": 2.0,
        "mjd": [1.0],
    }
    mv = json.loads((tmp_path / "movers" / "m1.json").read_text())
    mv["tracks"].append(slow_track)
    (tmp_path / "movers" / "m1.json").write_text(json.dumps(mv))
    bd = {"candidates": [], "known": [], "slow": [{"ra": 269.6, "dec": 4.6, "pm": 4300.0, "grade": "slow", "sigma": 2.0}]}
    (tmp_path / "browndwarfs" / "b1.json").write_text(json.dumps(bd))
    finds, _ = collect(tmp_path, rows, keys, TARGETS)
    assert not [f for f in finds if f["kind"] == "browndwarf"]
    assert len([f for f in finds if f["kind"] == "mover"]) == 1  # the same-motion track stays, the slow one is out


def test_a_deciding_catalogue_that_has_not_answered_keeps_a_find_unchecked() -> None:
    from service.findings import MAX_ATTEMPTS

    red = {"kind": "red", "data": {}}
    ids = [{"catalogue": c, "state": "none", "attempts": 0} for c in ("simbad", "twomass", "allwise", "ned", "vsx")]
    retrying = [*ids, {"catalogue": "gaia", "state": "unavailable", "attempts": 2}]
    gave_up = [*ids, {"catalogue": "gaia", "state": "unavailable", "attempts": MAX_ATTEMPTS}]
    assert status_of(red, retrying) == "unchecked"  # Gaia decides a red source and is still being retried
    assert status_of(red, gave_up) == "unchecked"  # a deciding catalogue that has not answered never makes it unexplained
    # VSX, NED and 2MASS only add detail: SIMBAD and Gaia having answered, a find is final whatever they did
    decided = [
        {"catalogue": "simbad", "state": "none"},
        {"catalogue": "gaia", "state": "none"},
        {"catalogue": "vsx", "state": "unavailable", "attempts": 0, "label": "VSX"},
    ]
    assert status_of(red, decided) == "unexplained"
    assert "Did not answer: VSX" in verdict_of(red, decided, "unexplained")


def test_peaks_known_by_jpl_or_a_star_residual() -> None:
    from service.findings import decisive_of

    peak = {"kind": "peak", "ra": 10.0, "dec": 5.0, "data": {"peak": {"ref_mjd": 61000.0}}}
    old = {"kind": "peak", "ra": 10.0, "dec": 5.0, "data": {"peak": {}}}
    assert decisive_of("peak", 10.0, 5.0, peak["data"]) == ["simbad", "gaia", "sbident", "halo"]
    assert decisive_of("peak", 10.0, 5.0, old["data"]) == ["simbad", "gaia", "halo"]  # no date: JPL is not asked
    assert status_of(peak, [{"catalogue": "sbident", "state": "ok"}]) == "known"
    star = {"catalogue": "allwise", "state": "ok", "sep": 1.4, "label": "AllWISE", "name": "AllWISE J040313.55+082720.0"}
    got = status_of(old, [star])
    assert got == "residual" and "residual of that star" in verdict_of(old, [star], got)
    far = {**star, "sep": 5.0}
    # an undated peak cannot be compared with the known bodies (JPL), so nothing explaining it is not yet "unexplained"
    assert (
        status_of(
            old,
            [far, {"catalogue": "simbad", "state": "none"}, {"catalogue": "gaia", "state": "none"}, {"catalogue": "halo", "state": "none"}],
        )
        == "unchecked"
    )
    dated_far = [
        far,
        {"catalogue": "simbad", "state": "none"},
        {"catalogue": "gaia", "state": "none"},
        {"catalogue": "sbident", "state": "none"},
        {"catalogue": "halo", "state": "none"},
    ]
    assert status_of(peak, dated_far) == "unexplained"


def _jpl_by_date(rows_by_mjd: dict[str, list[list[Any]]]) -> Any:
    from service.findings import Findings
    from service.upstream.breaker import Breaker

    class Client:
        async def get(self, url: str, params: dict[str, str], **_kw: object) -> _Resp:
            day = params["obs-time"][:10]
            return _Resp(_jpl(rows_by_mjd.get(day, [])))

    f = Findings.__new__(Findings)
    f.breaker = Breaker("JPL")
    f.catalogue = type("C", (), {"client": Client()})()
    f._kept_visits = lambda *a: None  # type: ignore[method-assign]
    return f


def test_film_track_is_known_when_the_same_body_stands_where_it_does_at_two_of_three_visits() -> None:
    from service.findings import _iso

    mjds = [61000.0, 61005.0, 61010.0]
    days = [_iso(m)[:10] for m in mjds]
    body = [["9952 Test", "2.0", "0.0", "58.0", "0.0", "18.0"]]
    f = _jpl_by_date({days[0]: body, days[2]: body})  # seen at two visits (the middle date has none)
    vis = [{"ra": 120.0, "dec": 10.0, "mjd": m} for m in mjds]
    row = _film_row(vis)
    got = asyncio.run(f.sbident_visits(row))
    assert got["state"] == "ok" and got["name"] == "9952 Test" and got["visits_matched"] == 2
    f2 = _jpl_by_date({days[0]: body})  # one of three: not enough
    assert asyncio.run(f2.sbident_visits(row))["state"] == "none"


def _film_row(vis: list[dict[str, Any]], rate: float = 58.0, pa: float = 90.0) -> Any:
    import sqlite3 as sq

    db = sq.connect(":memory:")
    db.row_factory = sq.Row
    db.execute("CREATE TABLE t (id, kind, ra, dec, data)")
    db.execute("INSERT INTO t VALUES ('film:x', 'film', 120.0, 10.0, ?)", (json.dumps({"track": {"rate": rate, "pa": pa}, "visits": vis}),))
    return db.execute("SELECT * FROM t").fetchone()


def _as_kept(got: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"catalogue": "sbident", "state": got["state"], "name": got.get("name"), "detail": json.loads(json.dumps(got))}]


def test_a_film_track_is_asked_at_every_visit_and_a_chain_through_a_known_body_is_no_lead() -> None:
    """4754 Panthoos, 5511 Cloanthus and 2017 Wesson led the first page as unexplained tracks (23 Nov 2026): one real body linked with
    noise. JPL was asked at three visits of six."""
    from service.findings import _iso, chain_of, verdict_of

    mjds = [61000.0 + 2 * k for k in range(6)]
    days = [_iso(m)[:10] for m in mjds]
    vis = [{"ra": 120.0, "dec": 10.0, "mjd": m} for m in mjds]
    f = {"kind": "film", "data": {"track": {"method": "orbit", "verified": True}}, "ra": 120.0, "dec": 10.0}

    def say(by: dict[str, list[list[str]]]) -> tuple[str, dict[str, Any]]:
        got = asyncio.run(_jpl_by_date(by).sbident_visits(_film_row(vis)))
        return status_of(f, _as_kept(got)), got

    def body(sep: str, rate: str = "10.0", v: str = "18.0", name: str = "4754 Panthoos") -> list[list[str]]:
        return [[name, sep, "0.0", rate, "0.0", v]]

    st, got = say({days[1]: body("3.6")})  # beside it at a visit the old check never asked
    assert st == "chain" and got["all_visits"] is True and got["visits_checked"] == 6 and got["state"] == "none"
    said = chain_of(f, _as_kept(got))
    assert said == {"name": "4754 Panthoos", "n": 1, "of": 6, "sep": 3.6}
    assert "4754 Panthoos stands 3.6″" in verdict_of(f, _as_kept(got), "chain")
    assert say({days[1]: body("9.0")})[0] == "unexplained"  # one visit further than 6″: chance
    assert say({days[1]: body("9.0"), days[4]: body("13.1")})[0] == "chain"  # the same body at two
    assert say({days[1]: body("9.0"), days[4]: body("9.0", name="another")})[0] == "unexplained"  # two bodies at one visit each
    assert say({days[1]: body("3.0", v="n.a.")})[0] == "unexplained"  # no known brightness (a comet's fragment): beside it by chance
    assert say({days[1]: body("3.0", v="22.5")})[0] == "unexplained"  # too faint to be in the pictures
    assert say({days[0]: body("3.0", rate="58.0")})[0] == "chain"  # it moves like the track, at one visit of six only
    assert say({d: body("3.0", rate="58.0") for d in days[:4]})[0] == "known"  # at four of six: the track is the body
    assert say({})[0] == "unexplained"
    line = {**f, "data": {"track": {"method": "line", "verified": False}}}
    assert status_of(line, _as_kept(say({days[1]: body("3.6")})[1])) == "unverified"


def test_what_three_visits_said_is_kept_and_a_comet_is_asked_for(tmp_path: Path) -> None:
    """The 680 tracks already asked about: only their other visits are asked; a visit that shows a body moving like the track (3I/ATLAS,
    which JPL leaves out unless bodies without a magnitude are asked for) asks the first ones again."""
    from service.findings import Findings, _iso
    from service.upstream.breaker import Breaker

    mjds = [61000.0 + k for k in range(5)]
    vis = [{"ra": 120.0, "dec": 10.0, "mjd": m} for m in mjds]
    db = FindingsDB(tmp_path / "findings.db", writer=True)
    old = {
        "two_pass": True,
        "n_bodies": 1,
        "per_visit": [{"mjd": mjds[k], "state": "none", "name": None, "sep": None, "note": None} for k in (0, 2, 4)],
    }
    old["per_visit"][0]["note"] = "4754 Panthoos (5010 T-3) stands 3.6″ away but moves 10″/h toward 39°, not like this find"
    db.put_ident("film:x", "sbident", "none", None, None, None, old)
    db.db.commit()
    assert status_of({"kind": "film", "data": {}}, db.idents_of(["film:x"])["film:x"]) == "chain"  # by the old note, at once
    asked: list[dict[str, str]] = []

    def run(rows: list[list[str]]) -> dict[str, Any]:
        class Client:
            async def get(self, url: str, params: dict[str, str], **_kw: object) -> _Resp:
                asked.append(params)
                return _Resp(_jpl(rows))

        f = Findings.__new__(Findings)
        f.breaker = Breaker("JPL")
        f.catalogue = type("C", (), {"client": Client()})()
        f.db = db
        return asyncio.run(f.sbident_visits(_film_row(vis)))

    got = run([])
    assert [p["obs-time"][:10] for p in asked] == [_iso(mjds[k])[:10] for k in (1, 3)] and asked[0]["mag-required"] == "false"
    assert len(got["per_visit"]) == 5 and status_of({"kind": "film", "data": {}}, _as_kept(got)) == "chain"
    asked.clear()
    got = run([["C/2025 N1 (ATLAS)", "4.0", "0.0", "58.0", "0.0", "n.a."]])
    assert got["state"] == "ok" and got["name"] == "C/2025 N1 (ATLAS)" and got["visits_matched"] == 5 and len(asked) == 5


def test_the_unexplained_tracks_are_asked_of_jpl_before_what_never_runs_out(tmp_path: Path) -> None:
    """26 of 28 unexplained tracks stood behind 1,100 finds no catalogue was asked about (prod, 23 Nov 2026)."""
    import time as _time

    from service.findings import Findings, Snapshot, pending_ids

    db = FindingsDB(tmp_path / "findings.db", writer=True)
    base = {"place": "far", "fov": 60, "ra": 120.0, "dec": 10.0, "rate": 58.0, "chance": 0.0, "scans": []}
    tracks = [{**base, "id": f"film:{i}", "kind": "film", "snr": 7.0 + i, "score": 7.0 + i, "data": {}} for i in range(4)]
    new = [{**base, "id": f"peak:{i}", "kind": "peak", "snr": 9.0, "score": 9.0, "data": {}} for i in range(20)]
    db.write_finds([*tracks, *new])
    for i in range(4):
        for c in ("simbad", "gaia", "ned", "vsx", "allwise", "twomass", "halo"):
            db.put_ident(f"film:{i}", c, "none", None, None, None, {"blend_r": 1})
        db.put_ident(f"film:{i}", "sbident", "none", None, None, None, {"two_pass": True, "per_visit": []})
    db.db.commit()
    now = _time.time()
    path = str(db.path)
    before = pending_ids(path, 100, frozenset(), now)
    assert all(i.startswith("peak:") for i in before[:20])  # the rest of a track's visits waits for every find never asked
    leads = ("film:1", "film:3")
    got = pending_ids(path, 100, frozenset(), now, None, ("peak:5",), leads)
    assert got[:3] == ["peak:5", "film:1", "film:3"] and set(got) == set(before)  # the first page, the leads in their order, the rest
    db.put_ident("film:1", "sbident", "none", None, None, None, {"two_pass": True, "all_visits": True, "per_visit": []})
    db.db.commit()
    assert "film:1" not in pending_ids(path, 100, frozenset(), now, None, (), leads)  # asked once, no more

    f = Findings.__new__(Findings)
    snap = Snapshot([], lambda b, w: {})
    one = {"kind": "film", "status": "unexplained", "promise": 70, "score": 8.0, "visible": False}
    snap.items = [
        {**one, "id": "film:h"},
        {**one, "id": "film:v", "visible": True, "promise": 60},
        {**one, "id": "film:u", "visible": None, "promise": 50},
        {**one, "id": "film:w", "status": "weak", "visible": True},
        {**one, "id": "peak:p", "kind": "peak", "visible": True},
    ]
    f.set_leads(snap)
    assert f.leads == ("film:v", "film:u", "film:h")  # the visible first; no weak track, no other kind


def test_the_counts_of_a_list_are_those_of_counting_every_find(tmp_path: Path) -> None:
    """The counts are taken of the combinations of what the filters read (Snapshot.groups), not of every find."""
    from service.findings import HIDDEN, query, snapshot

    db = FindingsDB(tmp_path / "findings.db", writer=True)
    base = {"fov": 60, "dec": 10.0, "rate": 1.0, "chance": 0.0, "scans": []}
    kinds = ("film", "peak", "red", "mover")
    db.write_finds(
        [
            {**base, "id": f"{kinds[i % 4]}:{i}", "kind": kinds[i % 4], "place": f"p{i % 3}", "ra": 1.0 + i, "snr": 5.0 + i % 7, "score": 5.0 + i % 7,
             "data": {"track": {"z_together": 5.0 + i % 7}} if i % 4 == 0 else {}}
            for i in range(60)
        ]
    )  # fmt: skip
    snap = snapshot(db, seen=lambda f: (None if hash(f["ra"]) % 3 == 0 else hash(f["ra"]) % 3 == 1, None))
    items = snap.items
    assert sum(n for _, n in snap.groups()) == 60 and snap.groups() is snap.groups()
    for place in (None, "p1"):
        for status in (None, "all", "weak", "unchecked"):
            for waiting in (False, True):
                got = query(db, None, status, place, 5, 0, snap=snap, waiting=waiting, visibility="seen")

                def ok(f: dict[str, Any], place: Any = place, status: Any = status, waiting: bool = waiting) -> bool:
                    hid = f["status"] in HIDDEN
                    by = f["status"] == status if status in HIDDEN else not hid if status in (None, "all") else f["status"] == status
                    return (
                        by and (place is None or f["place"] == place) and (not waiting or f["stage"] == "new") and f["visible"] is not False
                    )

                want = [f for f in items if ok(f)]
                assert got["count"] == len(want)
                assert {k: n for k, n in got["counts_view"].items() if n} == {
                    k: sum(f["kind"] == k for f in want) for k in kinds if any(f["kind"] == k for f in want)
                }
                assert got["total"] == sum(f["status"] not in HIDDEN for f in items)
                assert got["counts"]["film"]["weak"] == sum(f["kind"] == "film" and f["status"] == "weak" for f in items)


def test_a_change_of_a_listed_find_stays_out_of_the_shared_snapshot(tmp_path: Path) -> None:
    """routes_findings.enrich writes viewers and stage into the finds it is given: the counts and the next list must not see it."""
    from service.findings import query, snapshot

    db = FindingsDB(tmp_path / "findings.db", writer=True)
    base = {
        "place": "p",
        "fov": 60,
        "dec": 10.0,
        "rate": 1.0,
        "chance": 0.0,
        "scans": [],
        "kind": "film",
        "snr": 9.0,
        "score": 9.0,
        "data": {},
    }
    db.write_finds([{**base, "id": f"film:{i}", "ra": 1.0 + i} for i in range(4)])
    snap = snapshot(db)
    for slim in (True, False):
        a = query(db, None, None, None, 10, 0, snap=snap, slim=slim)
        assert a["waiting"] == 4
        for f in a["finds"]:
            f["viewers"], f["stage"] = 3, "in_review"
        b = query(db, None, None, None, 10, 0, snap=snap, slim=slim)
        assert b["waiting"] == 4 and b["stage_view"] == a["stage_view"] and {f["stage"] for f in b["finds"]} == {"new"}
        assert query(db, None, None, None, 10, 0, snap=snap, stage="new")["count"] == 4 == b["stage_view"]["new"]


def test_a_silent_jpl_does_not_change_what_it_said_before(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import time as _time

    from service.findings import Findings, pending_ids
    from service.upstream.breaker import Breaker, BreakerOpenError

    vis = [{"ra": 120.0, "dec": 10.0, "mjd": 61000.0 + k} for k in range(4)]
    db = FindingsDB(tmp_path / "findings.db", writer=True)
    base = {
        "kind": "film",
        "place": "far",
        "fov": 60,
        "ra": 120.0,
        "dec": 10.0,
        "rate": 58.0,
        "snr": 9.0,
        "score": 9.0,
        "chance": 0.0,
        "scans": [],
    }
    db.write_finds([{**base, "id": "film:x", "data": {"track": {"rate": 58.0, "pa": 90.0}, "visits": vis}}])
    per = [{"mjd": v["mjd"], "state": "none", "name": None, "sep": None, "note": None} for v in vis]
    db.put_ident("film:x", "sbident", "none", None, None, None, {"two_pass": True, "per_visit": per[:1] + per[2:]})
    db.db.commit()
    now = _time.time()
    assert pending_ids(str(db.path), 10, frozenset(), now) == ["film:x"]  # its other visit waits to be asked
    row = db.db.execute("SELECT * FROM finds").fetchone()
    for err in (httpx.ReadTimeout("slow"), BreakerOpenError("JPL", 60.0)):
        f = Findings.__new__(Findings)
        f.breaker = Breaker("JPL")
        f.db = db

        async def fail(r: Any, e: Exception = err) -> dict[str, Any]:
            raise e

        monkeypatch.setattr(f, "sbident_visits", fail)
        asyncio.run(f.identify_one(row))
        assert db.db.execute("SELECT state FROM idents WHERE find_id='film:x'").fetchone()[0] == "none"
        assert pending_ids(str(db.path), 10, frozenset(), now) == []  # and it is asked again later, not at once
    assert pending_ids(str(db.path), 10, frozenset(), now + 86400) == ["film:x"]
    db.put_ident("film:x", "sbident", "none", None, None, None, {"two_pass": True, "all_visits": True, "per_visit": per})
    db.db.commit()
    assert pending_ids(str(db.path), 10, frozenset(), now + 86400) == []


def test_wanted_identifications_and_the_best_measurement_of_a_merged_object() -> None:
    from service.findings import CATALOGUES, ecl_lat, merge, want_of

    assert want_of("film", 10.0, 5.0, {}) == ["sbident"]
    assert want_of("browndwarf", 133.0, 15.0, {"group": "candidate"})[-1] == "sbident"  # about 1° from the ecliptic... within 15°
    assert want_of("browndwarf", 133.0, 50.0, {"group": "candidate"}) == list(CATALOGUES)  # far from the ecliptic
    assert want_of("browndwarf", 133.0, 15.0, {"group": "known"}) == list(CATALOGUES)
    assert abs(ecl_lat(0.0, 0.0)) < 1e-9
    a = {
        "id": "a",
        "kind": "browndwarf",
        "place": "p",
        "fov": 20,
        "ra": 10.0,
        "dec": 5.0,
        "rate": 6.2,
        "score": 4.0,
        "snr": 5.0,
        "scans": ["a"],
        "data": {"source": {"speed_err": 1.5}},
    }
    b = {**a, "id": "b", "fov": 5, "rate": 7.9, "score": 12.9, "scans": ["b"], "data": {"source": {"speed_err": 0.6}}}
    m = merge([a, b])
    assert len(m) == 1 and m[0]["rate"] == 7.9 and m[0]["data"]["rate_err"] == 0.6 and m[0]["data"]["best_detection"]["fov"] == 5


def test_identification_order_puts_the_few_that_matter_first(tmp_path: Path) -> None:
    db = FindingsDB(tmp_path / "findings.db", writer=True)

    def put(i: str, kind: str, place: str) -> dict[str, Any]:
        return {
            "id": i,
            "kind": kind,
            "place": place,
            "fov": 20,
            "ra": 10.0,
            "dec": 5.0,
            "rate": 1.0,
            "snr": 1.0,
            "score": 1.0,
            "chance": None,
            "data": {},
            "scans": [],
        }

    db.write_finds(
        [
            put("a1", "anomaly", "Barnard's Star"),
            put("r1", "red", "far"),
            put("m1", "mover", "far"),
            put("p1", "peak", "far"),
            put("m2", "mover", "Barnard's Star"),
        ]
    )
    order = [r["id"] for r in db.pending(10, frozenset({"Barnard's Star"}))]
    assert order[:2] == ["m2", "m1"] or set(order[:3]) == {"m1", "m2", "p1"}
    assert order.index("a1") > order.index("m1") and order.index("r1") > order.index("p1")
    assert order[-1] == "r1" or order[-2:] == ["a1", "r1"] or order[-2:] == ["r1", "a1"]
    # the identification loop asks a worker process, on its own read-only connection, for the same order
    import time as _time

    from service.findings import pending_ids

    assert pending_ids(str(db.path), 10, frozenset({"Barnard's Star"}), _time.time()) == order


def test_identification_summary_splits_deciding_catalogues_from_extras(tmp_path: Path) -> None:
    rows, keys = _store(tmp_path)
    finds, _ = collect(tmp_path, rows, keys, TARGETS)
    db = FindingsDB(tmp_path / "findings.db", writer=True)
    db.write_finds(finds)
    red = next(f for f in finds if f["kind"] == "red")
    db.put_ident(red["id"], "vsx", "unavailable", None, None, None, {})
    db.put_ident(red["id"], "gaia", "unavailable", None, None, None, {})
    db.db.commit()
    got = query(db, None, "all", None, 10, 0)["identification"]
    assert got["unavailable"] == 2 and got["unavailable_decisive"] == 1 and got["unavailable_extras"] == 1


def test_a_galaxy_match_is_not_a_brown_dwarf_or_a_mover() -> None:
    from service.findings import is_galaxy

    assert is_galaxy("ACS-GC 20063724", None) and is_galaxy("[ABC] x", "G") and not is_galaxy("WISE J0855", "BD*")
    bd = {
        "kind": "browndwarf",
        "ra": 150.1,
        "dec": 2.2,
        "rate": 6.9,
        "data": {"group": "known", "source": {"simbad": {"state": "ok", "name": "ACS-GC 20063724", "otype": "G"}}},
    }
    got = status_of(bd, [])
    assert got == "galaxy" and "a galaxy, not a mover or a brown dwarf" in verdict_of(bd, [], got)
    real = {
        "kind": "browndwarf",
        "ra": 133.8,
        "dec": -7.2,
        "rate": 6.2,
        "data": {
            "group": "known",
            "source": {"pmra": -6196.9, "pmdec": 0.0, "simbad": {"state": "ok", "name": "WISEA J085510.74-071442.5", "otype": "BD*"}},
        },
    }
    assert status_of(real, []) != "known"  # the scan's name at the place, without a motion to compare: not enough
    wise = {
        "catalogue": "simbad",
        "state": "ok",
        "name": "WISEA J085510.74-071442.5",
        "type": "BD*",
        "raw": {"pmra": -8123.7, "pmdec": 673.2},
    }
    assert status_of(real, [wise]) == "known"
    sb = [{"catalogue": "simbad", "state": "ok", "name": "NAME Some Galaxy", "type": "AGN", "label": "SIMBAD", "sep": 0.5}]
    assert status_of({"kind": "mover", "ra": 10.0, "dec": 5.0, "rate": 5.0, "data": {"track": {"check": "none"}}}, sb) == "galaxy"


def test_peaks_below_the_bar_are_not_leads_and_the_cleanup_removes_old_rows(tmp_path: Path) -> None:
    db = FindingsDB(tmp_path / "findings.db", writer=True)

    def peak(i: str, ok: bool, ra: float) -> dict[str, Any]:
        return {
            "id": i,
            "kind": "peak",
            "place": "p",
            "fov": 18,
            "ra": ra,
            "dec": 5.0,
            "rate": None,
            "snr": 6.0,
            "score": 6.0,
            "chance": None,
            "data": {"peak": {"ok": ok, "d": 300.0}},
            "scans": [],
        }

    db.write_finds([peak("peak:a", True, 10.0), peak("peak:b", False, 20.0)])
    out = query(db, "peak", "all", None, 10, 0)
    assert out["counts"]["peak"]["all"] == 1 and out["counts"]["peak"]["below"] == 1 and out["counts"]["peak"]["leads"] == 1
    assert [f["id"] for f in out["finds"]] == ["peak:a"]
    assert query(db, "peak", "below", None, 10, 0)["count"] == 1
    from service.findings import Findings

    f = Findings.__new__(Findings)
    f.db = db
    assert f.cleanup() == 1 and f.cleanup() == 0
    assert db.db.execute("SELECT COUNT(*) FROM finds").fetchone()[0] == 1


def test_halo_peaks_and_motion_identity() -> None:
    from service.findings import _motion_identity

    peak = {"kind": "peak", "ra": 10.0, "dec": 5.0, "data": {"peak": {"ok": True}}}
    halo = {"catalogue": "halo", "state": "ok", "name": "Gaia DR3 1 (G 8.2)", "sep": 40.0, "label": "Bright star nearby"}
    got = status_of(peak, [halo])
    assert got == "halo" and "inside its halo" in verdict_of(peak, [halo], got)
    # WISE 0855: SIMBAD names it at the motion-corrected place, Gaia only has a neighbour with another motion
    bd = {
        "kind": "browndwarf",
        "ra": 133.8,
        "dec": -7.2,
        "rate": 7.9,
        "data": {
            "group": "known",
            "source": {"pmra": -7760.0, "pmdec": 1500.0, "simbad": {"state": "ok", "name": "WISEA J085510.74-071442.5", "otype": "Y*"}},
        },
    }
    ids = [
        {
            "catalogue": "simbad",
            "state": "ok",
            "name": "WISEA J085510.74-071442.5",
            "type": "BD*",
            "label": "SIMBAD",
            "raw": {"pmra": -7790.1, "pmdec": 1492.6},
        },
        {
            "catalogue": "gaia",
            "state": "ok",
            "name": "Gaia DR3 5756956644387831296",
            "label": "Gaia DR3",
            "raw": {"pmra": 12.0, "pmdec": -3.0},
        },
    ]
    _motion_identity(bd, ids)
    assert bd["identity"]["name"].startswith("WISEA J0855") and "neighbour" in ids[1]["note"]
    text = verdict_of(bd, ids, status_of(bd, ids))
    assert "WISEA J085510" in text and "5756956644387831296" not in text


def test_flow_fields_promise_first_and_the_looks_people_gave(tmp_path: Path) -> None:
    from service.findings import PROMISING_KINDS, STAGES, FlowDB, flow_fields, promise_of

    # every lead that moves stands above every red source and anomaly, and an unexplained find above the same find when known
    weakest_mover = min(promise_of(k, "unchecked", 0.0, 3) for k in ("mover", "browndwarf", "peak", "film"))
    assert weakest_mover > max(promise_of(k, "unexplained", 99.0, 0) for k in ("red", "anomaly"))
    assert promise_of("mover", "unexplained", 6.0, 0) > promise_of("mover", "known", 6.0, 0) > promise_of("mover", "residual", 6.0, 0)
    assert promise_of("mover", "unexplained", 6.0, 0) - promise_of("mover", "unexplained", 6.0, 1) == 5  # nobody has looked yet
    assert promise_of("anomaly", "halo", None, 0) >= 0 and promise_of("browndwarf", "unexplained", 50.0, 0) <= 100

    rows, keys = _store(tmp_path)
    finds, _ = collect(tmp_path, rows, keys, TARGETS)
    db = FindingsDB(tmp_path / "findings.db", writer=True)
    db.write_finds(finds)
    flow = FlowDB(tmp_path / "product.sqlite")
    out = query(db, None, "all", None, 100, 0, flow=flow, sort="promise")
    assert [f["promise"] for f in out["finds"]] == sorted((f["promise"] for f in out["finds"]), reverse=True)
    assert set(out["stages"]) == set(STAGES) and out["stages"]["known"] == 1 and out["stages"]["new"] == 4
    assert out["waiting"] == 4 and all(f["viewers"] == 0 and f["credit"]["machine"] for f in out["finds"])
    assert out["finds"][0]["kind"] in PROMISING_KINDS and out["finds"][-1]["kind"] in ("red", "anomaly", "mover")
    lead = next(f for f in out["finds"] if f["stage"] == "new")
    assert flow.add_view(lead["id"], "moves", "u1", "Ada") == 1 and flow.add_view(lead["id"], None) == 2
    again = query(db, None, "all", None, 100, 0, flow=flow, sort="promise")
    seen = next(f for f in again["finds"] if f["id"] == lead["id"])
    assert (
        seen["stage"] == "in_review"
        and seen["viewers"] == 2
        and seen["credit"]["checked_by"] == ["Ada"]
        and seen["credit"]["looks"] == {"moves": 1}
    )
    assert again["waiting"] == 3 and again["stages"]["in_review"] == 1
    assert query(db, None, "all", None, 100, 0, flow=flow, waiting=True)["count"] == 3
    assert query(db, None, "all", None, 100, 0, flow=flow, stage="in_review")["count"] == 1
    assert {f["kind"] for f in query(db, None, "all", None, 100, 0, flow=flow, kinds=("red", "anomaly"))["finds"]} == {"red", "anomaly"}
    # finds that can have no clip go to the end; within equal promise a ready clip comes first
    order = query(db, None, "all", None, 100, 0, flow=flow, sort="promise", clip_rank=lambda f: 2 if f["kind"] == "mover" else 0)
    assert order["finds"][-1]["kind"] == "mover" and order["count"] == again["count"]
    # only the named finds; only the finds that carry a machine note (none of this test's finds does)
    assert query(db, None, "all", None, 100, 0, only=frozenset({lead["id"]}))["count"] == 1
    assert query(db, None, "all", None, 100, 0, noted=True)["count"] == 0 and again["noted"] == 0 and seen["note"] is None
    # visible in its own picture or not: an empty ring cuts the promise to a twentieth and leaves the default view
    red_id = next(f["id"] for f in again["finds"] if f["kind"] == "red")
    vis = query(db, None, "all", None, 100, 0, flow=flow, sort="promise", seen=lambda f: (f["id"] != red_id, 7.0), visibility="seen")
    hid = query(db, None, "all", None, 100, 0, flow=flow, sort="promise", seen=lambda f: (f["id"] != red_id, 7.0), visibility="unseen")
    assert red_id not in {f["id"] for f in vis["finds"]} and [f["id"] for f in hid["finds"]] == [red_id]
    assert hid["finds"][0]["promise"] <= 2 and hid["finds"][0]["visible"] is False and vis["visibility"]["in_view"]["not_visible"] == 1
    assert vis["visibility"]["by_kind"]["red"] == {"visible": 0, "not_visible": 1, "not_measured": 0}
    assert query(db, None, "all", None, 100, 0)["finds"][0]["visible"] is None  # not measured: nothing is assumed
    # a fresh store: no picture measured yet, the default view ("seen") still lists every find; "unseen" lists none
    fresh = query(db, None, "all", None, 100, 0, flow=flow, sort="promise", seen=lambda f: (None, None), visibility="seen")
    none_hidden = query(db, None, "all", None, 100, 0, flow=flow, sort="promise", seen=lambda f: (None, None), visibility="unseen")
    assert fresh["count"] == again["count"] and none_hidden["count"] == 0
    # a find with a machine note (it looks like an artefact) goes below every clean find, whatever its promise
    import service.findings as sf

    top = again["finds"][0]["id"]
    sf._NOTES = {top: "a blend that stands still"}
    try:
        noted_last = query(db, None, "all", None, 100, 0, flow=flow, sort="promise")
        assert noted_last["finds"][-1]["id"] == top and noted_last["finds"][-1]["note"] and noted_last["noted"] == 1
    finally:
        sf._NOTES = None
    # the old call and the old fields stay as they were
    old = query(db, None, "all", None, 100, 0)
    assert old["total"] == 5 and {"status", "verdict", "idents"} <= set(old["finds"][0])
    assert flow_fields({"kind": "peak", "status": "halo", "score": 7.0}, None)["stage"] == "artefact"


def test_finds_people_set_aside_leave_the_leads_but_keep_their_page(tmp_path: Path) -> None:
    """Two people agreed a machine find is an artefact (service/accounts/field_people.set_aside): it leaves the default list and the
    leads like a halo, is listed under status=set_aside with the agreement in its sentence; a catalogue's answer stays stronger."""
    rows, keys = _store(tmp_path)
    finds, _ = collect(tmp_path, rows, keys, TARGETS)
    db = FindingsDB(tmp_path / "findings.db", writer=True)
    db.write_finds(finds)
    red = next(f for f in finds if f["kind"] == "red")
    known = next(f for f in finds if f["kind"] == "mover")  # Gaia matches the track: known
    aside = {red["id"]: {"look": "ghost", "agree": 2, "total": 2}, known["id"]: {"look": "streak", "agree": 2, "total": 3}}
    before = query(db, None, "all", None, 100, 0)
    out = query(db, None, "all", None, 100, 0, aside=aside)
    assert red["id"] in {f["id"] for f in before["finds"]} and red["id"] not in {f["id"] for f in out["finds"]}
    assert out["counts"]["red"]["set_aside"] == 1 and out["counts"]["red"]["leads"] == before["counts"]["red"]["leads"] - 1
    hid = query(db, None, "set_aside", None, 100, 0, aside=aside)
    r = next(f for f in hid["finds"] if f["id"] == red["id"])
    assert r["status"] == "set_aside" and r["stage"] == "artefact" and "People set it aside (2 of 2)" in r["verdict"]
    assert next(f for f in out["finds"] if f["id"] == known["id"])["status"] == "known"  # a catalogue's word stays


def test_a_find_people_raised_comes_first(tmp_path: Path) -> None:
    """People raised a find up the queue (service/accounts/people.raised): it is first in the promise order; a known one is not."""
    rows, keys = _store(tmp_path)
    finds, _ = collect(tmp_path, rows, keys, TARGETS)
    db = FindingsDB(tmp_path / "findings.db", writer=True)
    db.write_finds(finds)
    base = query(db, None, "all", None, 100, 0, sort="promise")["finds"]
    leads = [f for f in base if f["status"] in ("unexplained", "unchecked")]
    last = leads[-1]
    out = query(db, None, "all", None, 100, 0, sort="promise", raised={last["id"]: "moves_old"})["finds"]
    assert out[0]["id"] == last["id"] and out[0]["people_raised"] == "moves_old"
    known = next(f for f in base if f["status"] == "known")
    out2 = query(db, None, "all", None, 100, 0, sort="promise", raised={known["id"]: "candidate"})["finds"]
    assert "people_raised" not in next(f for f in out2 if f["id"] == known["id"])


def test_every_find_says_the_unit_of_its_rate() -> None:
    from service.findings import LITE_KEYS, rate_unit

    assert rate_unit("film") == "″/h" and rate_unit("mover") == "″/yr" and rate_unit("browndwarf") == "″/yr"
    assert "rate_unit" in LITE_KEYS


def test_the_identification_loop_asks_the_workers_as_background(monkeypatch: pytest.MonkeyPatch) -> None:
    """Its pending scan goes to a worker process as a background (maintenance) call: as a person's call, every 15 s, it held the
    sweep's share of the workers at service.cpu.PERSON_LOW_CAP."""
    import asyncio
    from types import SimpleNamespace

    from service import cpu
    from service import findings as fmod

    seen: list[int] = []

    async def run(fn: Any, *args: Any) -> Any:
        seen.append(cpu.priority())
        raise asyncio.CancelledError  # one turn is enough

    monkeypatch.setattr(fmod.cpu, "run", run)
    me = SimpleNamespace(db=SimpleNamespace(path="x"), targets=[], front=("film:1",), leads=())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(fmod.Findings.identify_loop(me))  # type: ignore[arg-type]
    assert seen == [3] and seen[0] >= cpu.LOW


def test_the_collector_runs_in_a_worker_process_as_background(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """collect goes to a worker process: its arguments and its answer pickle, and the collector loop asks as maintenance."""
    import pickle
    from types import SimpleNamespace

    from service import cpu
    from service import findings as fmod

    rows, keys = _store(tmp_path)
    got = pickle.loads(pickle.dumps(collect(*pickle.loads(pickle.dumps((tmp_path, rows, keys, TARGETS))))))
    assert got == collect(tmp_path, rows, keys, TARGETS)
    seen: list[int] = []

    async def run(fn: Any, *args: Any) -> Any:
        seen.append(cpu.priority())
        raise asyncio.CancelledError

    monkeypatch.setattr(fmod.cpu, "run", run)
    store = SimpleNamespace(listing=lambda kinds: [])
    keyer = SimpleNamespace(key_of=lambda k: k)
    me = SimpleNamespace(store=store, movers=keyer, pairs=keyer, dwarfs=keyer, anomalies=keyer, store_dir=tmp_path, targets=[])
    me.sweep = lambda: fmod.Findings.sweep(me)  # type: ignore[arg-type]
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(fmod.Findings.sweep_loop(me))  # type: ignore[arg-type]
    assert seen == [3]


def test_a_deciding_catalogue_is_asked_again_until_it_answers(tmp_path: Path) -> None:
    """JPL decides a time-lapse track: past MAX_ATTEMPTS it is still asked (after the longest wait), while a catalogue that only adds
    detail gives up; and the track stays unchecked meanwhile, never "unexplained"."""
    import time as _time

    from service.findings import MAX_ATTEMPTS, RETRY_S, pending_ids

    db = FindingsDB(tmp_path / "findings.db", writer=True)
    film = {"id": "film:1", "kind": "film", "place": "far", "fov": 60, "ra": 60.6, "dec": 8.4, "rate": 36.7, "snr": 19.0,
            "score": 19.0, "chance": 0.0, "data": {}, "scans": []}  # fmt: skip
    red = {**film, "id": "red:1", "kind": "red"}
    db.write_finds([film, red])
    for _ in range(MAX_ATTEMPTS):
        db.put_ident("film:1", "sbident", "unavailable", None, None, None, {})
    for c in ("simbad", "gaia"):
        db.put_ident("red:1", c, "none", None, None, None, {})
    for c in ("twomass", "allwise", "vsx", "ned"):
        for _ in range(MAX_ATTEMPTS):
            db.put_ident("red:1", c, "unavailable", None, None, None, {})
    db.db.commit()  # as the identification loop does after each batch (the scan reads on its own connection)
    later = _time.time() + RETRY_S * 32 + 1
    assert pending_ids(str(db.path), 10, frozenset(), later) == ["film:1"]  # JPL again; the red source's extras gave up
    assert pending_ids(str(db.path), 10, frozenset(), _time.time()) == []  # not before its wait
    # past their tries, the catalogues that only add detail are still asked every EXTRA_RETRY_S (VSX did not answer for days)
    from service.findings import EXTRA_RETRY_S

    assert "red:1" in pending_ids(str(db.path), 10, frozenset(), _time.time() + EXTRA_RETRY_S + 1)
    idents = [{"catalogue": "sbident", "state": "unavailable", "attempts": MAX_ATTEMPTS + 3}]
    assert status_of({"kind": "film", "data": {}}, idents) == "unchecked"


def test_a_brown_dwarf_candidate_that_did_not_move_is_a_blend_not_a_lead() -> None:
    """169.89 +4.35 (explorer round 2): an 8.8″/yr 'mover' whose shift is a blend with a Gaia star 7.7″ away, with an AllWISE source at
    its place from 2010 (moving as claimed it would be 140″ away): not a lead, low promise, and never "no catalogue names it"."""
    from service.findings import STAGE_OF_STATUS, promise_of

    bd = {"kind": "browndwarf", "ra": 170.153439, "dec": 4.438454, "data": {"group": "candidate", "source": {
        "pm": 8838.8, "shift_arcsec": 9.1, "baseline_years": 1.03}}}  # fmt: skip
    base = [{"catalogue": c, "state": "none", "attempts": 0} for c in ("simbad", "twomass", "vsx", "ned", "sbident")]
    gaia_nb = {"catalogue": "gaia", "label": "Gaia DR3", "state": "none", "detail": {"blend_r": 10.0,
               "neighbour": {"name": "Gaia DR3 3813536887008754304", "sep": 7.7}}}  # fmt: skip
    allwise_none = {"catalogue": "allwise", "state": "none"}
    assert status_of(bd, [*base, gaia_nb, allwise_none]) == "blend"
    text = verdict_of(bd, [*base, gaia_nb, allwise_none], "blend")
    assert "7.7″" in text and "Gaia DR3 3813536887008754304" in text and "no catalogue names" not in text
    # the AllWISE source at today's place alone says the same: in 2010 it was already here
    allwise_here = {"catalogue": "allwise", "label": "AllWISE", "state": "ok", "name": "J112036.82+042613.5", "sep": 4.9}
    gaia_none = {"catalogue": "gaia", "state": "none", "detail": {"blend_r": 10.0}}
    assert status_of(bd, [*base, gaia_none, allwise_here]) == "blend"
    assert "stood still" in verdict_of(bd, [*base, gaia_none, allwise_here], "blend")
    assert STAGE_OF_STATUS["blend"] == "artefact" and promise_of("browndwarf", "blend", 7.0, 0) < 10
    # a real fast mover far beyond its neighbour stays a lead; and a candidate a catalogue sits on is never "no catalogue names it"
    fast = {**bd, "data": {"group": "candidate", "source": {"pm": 8838.8, "shift_arcsec": 30.0}}}
    assert status_of(fast, [*base, gaia_nb, {"catalogue": "allwise", "state": "none"}]) == "unexplained"
    slow = {**bd, "data": {"group": "candidate", "source": {"pm": 300.0, "shift_arcsec": 0.3}}}
    named = [*base, gaia_none, {**allwise_here, "sep": 2.0}]
    assert status_of(slow, named) == "unexplained" and "no catalogue names" not in verdict_of(slow, named, "unexplained")


def test_a_find_has_a_slug_in_the_pages_words_and_the_routes_take_it() -> None:
    """p2 (explorer round 2): `film:` showed in addresses and titles. The id stays the key (credits, tasks and answers use it); the
    slug says it in the page's words, and an address with the slug finds the same find."""
    from service.findings import id_of, slug_of

    assert slug_of("film:8dcb8e38bc95") == "track-8dcb8e38bc95" and slug_of("browndwarf:b5e2f0136331") == "browndwarf-b5e2f0136331"
    for fid in ("film:8dcb8e38bc95", "browndwarf:b5e2f0136331", "mover:3", "peak:aae17000e21f", "user:1a2b3c"):
        assert id_of(slug_of(fid)) == fid and id_of(fid) == fid
    assert slug_of("plain") == "plain" and id_of("plain") == "plain"


def test_a_list_with_a_blend_in_it_sorts(tmp_path: Path) -> None:
    """status=blend and the single-find page list the blends: the sort knew every status but blend (a KeyError, HTTP 500)."""
    db = FindingsDB(tmp_path / "findings.db", writer=True)
    bd = {"id": "browndwarf:1", "kind": "browndwarf", "place": "far", "fov": 20, "ra": 170.153439, "dec": 4.438454, "rate": 8.8,
          "snr": 7.0, "score": 7.0, "chance": None, "scans": [],
          "data": {"group": "candidate", "source": {"pm": 8838.8, "shift_arcsec": 9.1}}}  # fmt: skip
    db.write_finds([bd])
    for c in ("simbad", "gaia", "twomass", "vsx", "ned", "sbident"):
        db.put_ident("browndwarf:1", c, "none", None, None, None, {"blend_r": 10.0})
    db.put_ident("browndwarf:1", "allwise", "ok", "J112036.82+042613.5", 4.9, None, {"blend_r": 10.0})
    db.db.commit()
    body = query(db, None, "blend", None, 50, 0)
    assert [f["status"] for f in body["finds"]] == ["blend"] and body["counts"]["browndwarf"]["blend"] == 1


def test_counts_view_counts_each_kind_under_the_other_filters(tmp_path: Path) -> None:
    """The kind chips count under the stage and visibility filters in use: counts_view, one number per kind (the page made a call per
    chip before)."""
    db = FindingsDB(tmp_path / "findings.db", writer=True)

    def put(i: str, kind: str) -> dict[str, Any]:
        return {"id": i, "kind": kind, "place": "far", "fov": 20, "ra": 10.0, "dec": 5.0, "rate": 1.0, "snr": 9.0, "score": 9.0,
                "chance": None, "data": {}, "scans": []}  # fmt: skip

    db.write_finds([put("red:1", "red"), put("red:2", "red"), put("anomaly:1", "anomaly"), put("peak:1", "peak")])
    body = query(db, None, "all", None, 50, 0, kinds=("red",))
    assert body["counts_view"]["red"] == 2 and body["counts_view"]["anomaly"] == 1 and body["counts_view"]["mover"] == 0
    assert [f["kind"] for f in body["finds"]] == ["red", "red"]  # the list itself keeps the kind filter
    assert query(db, None, "all", None, 50, 0, stage="known")["counts_view"]["red"] == 0  # under a stage nobody is in


def test_stage_view_counts_the_stage_chips_under_the_kind_filter(tmp_path: Path) -> None:
    db = FindingsDB(tmp_path / "findings.db", writer=True)

    def put(i: str, kind: str) -> dict[str, Any]:
        return {"id": i, "kind": kind, "place": "far", "fov": 20, "ra": 10.0, "dec": 5.0, "rate": 1.0, "snr": 9.0, "score": 9.0,
                "chance": None, "data": {}, "scans": []}  # fmt: skip

    db.write_finds([put("red:1", "red"), put("red:2", "red"), put("anomaly:1", "anomaly")])
    sv = query(db, None, "all", None, 50, 0, kinds=("red",))["stage_view"]
    assert sv["waiting"] == 2 and sv["new"] == 2 and sv["noted"] == 0  # the anomaly is another kind: not counted
    assert query(db, None, "all", None, 50, 0, kinds=("red",), stage="known")["stage_view"]["waiting"] == 2  # not under its own stage


def test_sbident_asks_jpl_for_its_precise_second_pass() -> None:
    """Explorer round 3: the top time-lapse lead (unexplained, "JPL lists no known body there") is 12304 (1991 SR1). JPL's quick first
    pass put it 17.7″ away (beyond the 15″ match), its precise second pass 4.4″: the identification asks for the second pass and
    measures the separation from the body's own astrometric place."""
    from service.findings import Findings
    from service.upstream.breaker import Breaker

    asked: dict[str, str] = {}
    fields = ["Object name", "Astrometric RA (hh:mm:ss)", "Astrometric Dec (dd mm'ss\")", "Visual magnitude (V)",
              'RA rate ("/h)', 'Dec rate ("/h)']  # fmt: skip
    # the film at 60.6088571 +8.3985333 (04 02 26.13 +08 23 54.7); 12304 4.4″ away, moving like the track (36.7″/h toward 70°)
    body = {"fields_second": fields, "data_second_pass": [["12304 (1991 SR1)", "04 02 26.38", "+08 23 56.1", "18.1", "34.5", "12.6"]]}

    class Client:
        async def get(self, url: str, params: dict[str, str], **_kw: object) -> _Resp:
            asked.update(params)
            return _Resp(body)

    f = Findings.__new__(Findings)
    f.breaker = Breaker("JPL")
    f.catalogue = type("C", (), {"client": Client()})()
    got = asyncio.run(f.sbident(60.6088571, 8.3985333, 61085.955, 36.72, 70.3))
    assert asked["two-pass"] == "true" and asked["suppress-first-pass"] == "true"
    assert got["state"] == "ok" and got["name"] == "12304 (1991 SR1)" and 3.0 < got["sep"] < 6.0 and got["two_pass"] is True


def test_the_most_promising_find_is_identified_first(tmp_path: Path) -> None:
    """The leads at the top of Findings are corrected first: the 36.7″/h track (promise 75) before a weak Planet X peak."""
    import time as _time

    from service.findings import pending_ids

    db = FindingsDB(tmp_path / "findings.db", writer=True)

    def put(i: str, kind: str, score: float) -> dict[str, Any]:
        return {"id": i, "kind": kind, "place": "far", "fov": 60, "ra": 60.6, "dec": 8.4, "rate": 1.0, "snr": score, "score": score,
                "chance": None, "data": {}, "scans": []}  # fmt: skip

    db.write_finds([put("peak:weak", "peak", 0.5), put("film:top", "film", 19.1), put("red:x", "red", 30.0)])
    db.db.commit()
    order = pending_ids(str(db.path), 10, frozenset(), _time.time())
    assert order.index("film:top") < order.index("peak:weak") < order.index("red:x")


def test_a_check_that_decides_goes_before_extras_asked_again(tmp_path: Path) -> None:
    """With VizieR down, thousands of finds wait on VSX alone; a track whose JPL check was made the old way (no precise second pass) is
    asked first, though a brown dwarf waiting only on VSX is more promising. And an undated peak says what it lacks: its date, not VSX."""
    import time as _time

    from service.findings import EXTRA_RETRY_S, MAX_ATTEMPTS, pending_ids, verdict_of

    db = FindingsDB(tmp_path / "findings.db", writer=True)
    film = {"id": "film:1", "kind": "film", "place": "far", "fov": 60, "ra": 60.6, "dec": 60.4, "rate": 36.7, "snr": 5.0,
            "score": 5.0, "chance": 0.0, "data": {}, "scans": []}  # fmt: skip
    bd = {**film, "id": "browndwarf:1", "kind": "browndwarf", "snr": 9.0, "score": 9.0}
    db.write_finds([film, bd])
    db.put_ident("film:1", "sbident", "none", None, None, None, {"n_bodies": 3})  # asked before two_pass
    from service.findings import want_of

    for c in want_of("browndwarf", 60.6, 60.4, {}):
        if c == "vsx":
            for _ in range(MAX_ATTEMPTS):
                db.put_ident("browndwarf:1", c, "unavailable", None, None, None, {})
        else:
            db.put_ident("browndwarf:1", c, "none", None, None, None, {"blend_r": 10})
    db.db.commit()
    assert pending_ids(str(db.path), 10, frozenset(), _time.time() + EXTRA_RETRY_S + 1) == ["film:1", "browndwarf:1"]
    peak = {"kind": "peak", "ra": 1.0, "dec": 2.0, "data": {"peak": {}}}
    idents = [{"catalogue": c, "state": "none"} for c in ("simbad", "gaia", "halo")] + [
        {"catalogue": "vsx", "label": "VSX (variable stars)", "state": "unavailable"}
    ]
    assert status_of(peak, idents) == "unchecked"
    text = verdict_of(peak, idents, "unchecked")
    assert "date" in text and "JPL" in text and "decide have not all answered" not in text


def test_the_first_page_of_promising_is_identified_first(tmp_path: Path) -> None:
    """Tracks of equal promise (σ is capped): the ones the first page shows, as last served, are asked first and in its order."""
    import time as _time

    from service.findings import pending_ids

    db = FindingsDB(tmp_path / "findings.db", writer=True)
    base = {"kind": "film", "place": "far", "fov": 60, "ra": 60.6, "dec": 60.4, "rate": 36.7, "snr": 19.0, "score": 19.0,
            "chance": 0.0, "data": {}, "scans": []}  # fmt: skip
    db.write_finds([{**base, "id": f"film:{n}", "ra": 60.0 + n} for n in range(6)])
    db.db.commit()
    now = _time.time()
    plain = pending_ids(str(db.path), 10, frozenset(), now)
    assert sorted(plain) == [f"film:{n}" for n in range(6)]
    got = pending_ids(str(db.path), 10, frozenset(), now, None, ("film:5", "film:3", "other:1"))
    assert got[:2] == ["film:5", "film:3"] and sorted(got[2:]) == ["film:0", "film:1", "film:2", "film:4"]


def test_after_a_restart_the_loop_knows_the_first_page_itself(tmp_path: Path) -> None:
    """Until a visitor loads Findings, the identification loop orders by the first page of Promising it works out itself."""
    from types import SimpleNamespace

    from service import findings as fmod

    db = FindingsDB(tmp_path / "findings.db", writer=True)
    base = {"place": "far", "fov": 60, "ra": 60.6, "dec": 60.4, "rate": 36.7, "chance": 0.0, "data": {}, "scans": []}
    db.write_finds([
        {**base, "id": "film:weak", "kind": "film", "snr": 4.0, "score": 4.0},
        {**base, "id": "film:strong", "kind": "film", "ra": 61.0, "snr": 9.0, "score": 9.0},
        {**base, "id": "red:1", "kind": "red", "ra": 62.0, "snr": 30.0, "score": 30.0},  # behind a filter on the page
    ])  # fmt: skip
    db.db.commit()
    front = fmod.Findings._front_now(SimpleNamespace(db=db))  # type: ignore[arg-type]
    assert front[:2] == ("film:strong", "film:weak") and "red:1" not in front


def test_the_served_first_page_is_kept_across_a_restart(tmp_path: Path) -> None:
    """The page's own first page (its order counts who has looked) is saved when served and read back at the next start."""
    from types import SimpleNamespace

    from service import findings as fmod

    me = SimpleNamespace(store_dir=tmp_path, front=())
    me._front_path = lambda: fmod.Findings._front_path(me)  # type: ignore[arg-type]
    fmod.Findings.set_front(me, ("film:b", "peak:a"))  # type: ignore[arg-type]
    assert me.front == ("film:b", "peak:a")
    assert fmod.Findings._front_saved(me) == ("film:b", "peak:a")  # type: ignore[arg-type]
    (tmp_path / "findings-front.json").write_text("not json")
    assert fmod.Findings._front_saved(me) == ()  # type: ignore[arg-type]


def test_a_line_track_never_checked_in_the_exposures_is_not_a_lead(tmp_path: Path) -> None:
    """R Draconis, 18 Nov 2026: a straight-line track whose strongest points held no light in the exposures. A line track without the
    on - off check is `unverified` (hidden, not a lead); one JPL names stays known; an orbit track (always verified) is a lead."""
    from service.findings import verdict_of

    idents = [{"catalogue": "sbident", "state": "none"}]
    line = {"kind": "film", "data": {"track": {"method": "line", "verified": False}}}
    assert status_of(line, idents) == "unverified"
    assert "never checked in the exposures" in verdict_of(line, idents, "unverified")
    assert status_of(line, [{"catalogue": "sbident", "state": "ok", "name": "433 Eros"}]) == "known"
    orbit = {"kind": "film", "data": {"track": {"method": "orbit", "verified": True}}}
    assert status_of(orbit, idents) == "unexplained"
    db = FindingsDB(tmp_path / "findings.db", writer=True)
    base = {
        "place": "R Draconis",
        "fov": 20,
        "ra": 248.4,
        "dec": 66.6,
        "rate": 3.6,
        "snr": 101.0,
        "score": 101.0,
        "chance": 0.0,
        "scans": [],
    }
    db.write_finds([{**base, "id": "film:rdra", "kind": "film", "data": {"track": {"method": "line", "verified": False}}}])
    db.put_ident("film:rdra", "sbident", "none", None, None, None, {"two_pass": True})
    db.db.commit()
    out = query(db, None, None, None, 10, 0)
    assert out["counts"]["film"]["unverified"] == 1 and out["counts"]["film"]["leads"] == 0
    assert [f["id"] for f in query(db, None, "unverified", None, 10, 0)["finds"]] == ["film:rdra"]


def test_one_find_is_read_whatever_its_place_in_the_list(tmp_path: Path) -> None:
    """The find's page reads that one find (any status): it searched a list capped at 5000 and answered 404 past it (19 Nov 2026)."""
    db = FindingsDB(tmp_path / "findings.db", writer=True)
    base = {"place": "far", "fov": 20, "rate": None, "snr": 6.0, "score": 6.0, "chance": 0.0, "scans": [], "data": {}}
    db.write_finds([{**base, "id": f"red:{k:05d}", "kind": "red", "ra": 10.0 + k * 1e-3, "dec": 5.0} for k in range(5200)])
    db.db.commit()
    got = query(db, None, "any", None, 1, 0, only=frozenset({"red:05150"}))
    assert [f["id"] for f in got["finds"]] == ["red:05150"]


def test_movers_and_brown_dwarfs_before_tracks_before_planet_x_peaks() -> None:
    """The user, 19 Nov 2026: an unexplained mover or brown-dwarf candidate (at its 5 sigma bar, already looked at) stands above the
    strongest track nobody has looked at, and every track above the strongest unexplained Planet X peak."""
    from service.findings import promise_of

    weakest_mover = min(promise_of(k, "unexplained", 5.0, 3) for k in ("mover", "browndwarf"))
    strongest_track = promise_of("film", "unexplained", 99.0, 0)
    weakest_track = promise_of("film", "unexplained", 5.0, 3)
    strongest_peak = promise_of("peak", "unexplained", 99.0, 0)
    assert weakest_mover > strongest_track and weakest_track >= strongest_peak
    assert promise_of("peak", "unchecked", 9.0, 0) < weakest_track  # the undated peaks of page 1 go below the tracks
    assert promise_of("browndwarf", "unexplained", 99.0, 0) == 100


def test_a_peak_jpl_names_is_known_before_the_filters_and_the_counts(tmp_path: Path) -> None:
    """Three peaks JPL named came in the answer to status=unexplained as "known", status=known was empty and the counts were wrong
    (prod, 23 Nov 2026): the name was put on the finds of the page after they were chosen."""
    db = FindingsDB(tmp_path / "findings.db", writer=True)
    base = {"kind": "peak", "place": "far", "fov": 18, "rate": None, "snr": 6.0, "score": 6.0, "chance": 0.0, "scans": ["k"]}
    db.write_finds(
        [
            {**base, "id": f"peak:{n}", "ra": 26.0 + i, "dec": 0.4, "data": {"peak": {"ok": True}}}
            for i, n in enumerate(("eris", "edge", "new"))
        ]
    )
    db.db.commit()
    said = {
        "peak:eris": {"state": "ok", "words": "an echo of Eris (JPL), not a new body"},
        "peak:edge": {"state": "residual", "reason": "the signal grows to the edge"},
    }

    def names(f: dict[str, Any]) -> dict[str, Any] | None:
        return said.get(f["id"])

    def ids(status: str | None) -> list[str]:
        out = query(db, "peak", status, None, 10, 0, names=names)
        assert out["count"] == len(out["finds"])
        return sorted(f["id"] for f in out["finds"])

    assert ids("unchecked") == ["peak:new"] and ids("known") == ["peak:eris"] and ids("residual") == ["peak:edge"]
    assert ids("all") == ["peak:eris", "peak:new"]  # a residual is no lead and not in the list
    out = query(db, "peak", "known", None, 10, 0, names=names)
    c = out["counts"]["peak"]
    assert (c["all"], c["known"], c["unchecked"], c["residual"], c["leads"]) == (2, 1, 1, 1, 1)
    f = out["finds"][0]
    assert (
        f["verdict"] == "An echo of Eris (JPL), not a new body." and f["data"]["peak"]["known"]["state"] == "ok" and f["stage"] == "known"
    )
    res = query(db, "peak", "residual", None, 10, 0, names=names)["finds"][0]
    assert res["data"]["peak"]["ok"] is False and res["verdict"].startswith("A residual, not a lead: the signal")
    assert query(db, "peak", None, None, 10, 0)["counts"]["peak"]["unchecked"] == 3  # without JPL's word: as the catalogues left them


def test_only_the_finds_of_the_page_are_read_whole_and_a_slim_list_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = FindingsDB(tmp_path / "findings.db", writer=True)
    base = {"kind": "film", "place": "far", "fov": 60, "dec": 8.4, "rate": None, "snr": 6.0, "chance": 0.0, "scans": ["k"], "data": {}}
    db.write_finds(
        [{**base, "id": f"film:{i}", "ra": 60.0 + i, "score": float(i), "positions": [{"label": "v", "mjd": 1.0}]} for i in range(30)]
    )
    db.db.commit()
    asked: list[list[str]] = []
    real = FindingsDB.related
    monkeypatch.setattr(FindingsDB, "related", lambda self, ids: asked.append(list(ids)) or real(self, ids))
    page = query(db, None, "any", None, 4, 2)["finds"]
    assert len(page) == 4 and asked == [[f["id"] for f in page]] and len(page[0]["positions"]) == 1
    asked.clear()
    slim = query(db, None, "any", None, 1000, 0, slim=True)["finds"]
    assert len(slim) == 30 and not asked and [f["id"] for f in slim[2:6]] == [f["id"] for f in page]
    assert "data" not in slim[0] and slim[2]["status"] == page[0]["status"]


def test_reuse_ids_finds_the_same_rows_as_looking_at_every_row() -> None:
    import random

    from service.findings import reuse_ids, same_object

    def brute(finds: list[dict[str, Any]], have: list[dict[str, Any]]) -> None:
        taken = {f["id"] for f in finds if any(h["id"] == f["id"] for h in have)}
        for f in finds:
            if f["id"] in taken:
                continue
            for h in have:
                if h["id"] not in taken and same_object(h, f):
                    f["id"] = h["id"]
                    taken.add(h["id"])
                    break

    rng = random.Random(83)
    kinds = ["red", "film", "mover", "peak"]
    have = [
        {"id": f"o{i}", "kind": rng.choice(kinds), "ra": 10 + rng.random() * 0.02, "dec": -0.01 + rng.random() * 0.02, "rate": rng.choice([None, 1.0, 5.0])}
        for i in range(600)
    ]  # fmt: skip
    finds = [
        {**h, "id": h["id"] if rng.random() < 0.3 else f"n{i}", "ra": h["ra"] + rng.gauss(0, 1) / 3600, "dec": h["dec"] + rng.gauss(0, 1) / 3600}
        for i, h in enumerate(rng.sample(have, 400))
    ]  # fmt: skip
    a, b = [dict(f) for f in finds], [dict(f) for f in finds]
    reuse_ids(a, have)
    brute(b, have)
    assert [f["id"] for f in a] == [f["id"] for f in b] and any(f["id"].startswith("o") for f in a)


def test_a_weak_track_is_kept_behind_its_own_filter_and_a_known_body_is_never_weak(tmp_path: Path) -> None:
    db = FindingsDB(tmp_path / "findings.db", writer=True)
    base = {"kind": "film", "place": "far", "fov": 20, "rate": 20.0, "chance": 0.0, "scans": ["k"]}

    def put(i: str, ra: float, z: float) -> dict[str, Any]:
        return {
            **base,
            "id": f"film:{i}",
            "ra": ra,
            "dec": 1.0,
            "snr": z,
            "score": z,
            "data": {"track": {"method": "orbit", "verified": True, "z_together": z}},
        }

    db.write_finds([put("weak", 10.0, 6.9), put("strong", 11.0, 7.0), put("body", 12.0, 5.0)])
    for i, st in (("weak", "none"), ("strong", "none"), ("body", "ok")):
        db.put_ident(f"film:{i}", "sbident", st, "1 Ceres" if st == "ok" else None, 2.0, None, {"two_pass": True, "all_visits": True})
    db.db.commit()
    out = query(db, None, None, None, 10, 0)
    assert {f["id"]: f["status"] for f in out["finds"]} == {"film:strong": "unexplained", "film:body": "known"}
    c = out["counts"]["film"]
    assert (c["all"], c["weak"], c["leads"], c["known"]) == (2, 1, 1, 1)
    weak = query(db, None, "weak", None, 10, 0)["finds"]
    assert [f["id"] for f in weak] == ["film:weak"] and "6.9 times the noise" in weak[0]["verdict"] and weak[0]["stage"] == "new"
    up = query(db, None, None, None, 10, 0, raised={"film:weak": "moves_old"})["finds"]  # people agree it moves
    assert {f["id"] for f in up} == {"film:weak", "film:strong", "film:body"}
    body = next(f for f in out["finds"] if f["status"] == "known")
    assert body["verdict"].startswith("A known solar-system body is predicted here: JPL Small-Body Identification: 1 Ceres")


def test_a_known_body_is_named_by_jpl_not_by_a_star_that_stands_there() -> None:
    idents = [
        {"catalogue": "allwise", "label": "AllWISE", "state": "ok", "name": "J110754.33+051334.7", "sep": 6.3},
        {"catalogue": "sbident", "label": "JPL", "state": "ok", "name": "C/2025 N1 (ATLAS)", "sep": 6.8},
    ]
    text = verdict_of({"kind": "film", "data": {}}, idents, "known")
    assert "C/2025 N1 (ATLAS), 6.8″ away" in text and "J110754" not in text


def test_a_red_source_is_classed_by_the_catalogues_and_asked_of_people_only_when_they_say_nothing() -> None:
    from service.findings import ask_people, catalogue_class

    def ident(c: str, state: str, **k: Any) -> dict[str, Any]:
        return {"catalogue": c, "state": state, "name": None, "sep": 2.0, "type": None, "detail": {}, **k}

    red = {"kind": "red", "data": {"source": {}}}
    none = [ident("simbad", "none"), ident("twomass", "none")]

    def label(f: dict[str, Any], w1: float, w2: float, two: str = "none") -> str:
        ids = [ident("simbad", "none"), ident("twomass", two), ident("allwise", "ok", detail={"W1": w1, "W2": w2})]
        assert not ask_people(f, ids)  # its colours answer better than an eye
        got = catalogue_class(f, ids)
        assert got is not None
        return got["label"]

    assert label(red, 15.0, 14.1, "ok") == "colours of an active galaxy"
    assert label(red, 15.0, 13.0).startswith("very red")
    assert label(red, 15.0, 14.9).startswith("neither")
    moving = {"kind": "red", "data": {"source": {"moves": True, "pmra": 3139.5, "pmdec": -1537.2}}}
    assert label(moving, 15.0, 14.9) == "stood still since 2010"
    typed = [ident("simbad", "ok", type="QSO", name="X"), ident("allwise", "none")]
    assert (catalogue_class(red, typed) or {})["label"] == "QSO" and not ask_people(red, typed)
    assert catalogue_class(red, [*none, ident("allwise", "none")]) is None and ask_people(red, [*none, ident("allwise", "none")])
    assert not ask_people(red, [*none, ident("allwise", "unavailable")])  # not answered yet: not asked
    assert not ask_people(red, [*none])


def test_a_peak_is_not_a_main_belt_body_that_passes_there() -> None:
    f = _findings_with(_jpl([["12 Victoria", "3.0", "0.0", "40.0", "0.0", "17"], ["136199 Eris", "4.0", "0.0", "1.0", "0.0", "18.7"]]))
    got = asyncio.run(f.sbident(120.0, 10.0, 61000.0, None, None, False, 15.0))
    assert got["state"] == "ok" and got["name"] == "136199 Eris"
    f = _findings_with(_jpl([["12 Victoria", "3.0", "0.0", "40.0", "0.0", "17"]]))
    assert asyncio.run(f.sbident(120.0, 10.0, 61000.0, None, None, False, 15.0))["state"] == "none"
    assert asyncio.run(f.sbident(120.0, 10.0, 61000.0, None, None, False))["state"] == "ok"  # (no limit: as for a brown dwarf)


def _peak(fid: str, sn: float | None, d0: float = 260.0, det: str = "123", search: str = "distant") -> dict[str, object]:
    return {
        "id": f"peak:{fid}", "kind": "peak", "place": "p", "fov": 18.0, "ra": 10.0 + len(fid), "dec": 1.0, "rate": None, "snr": sn, "score": 7.0,
        "chance": None, "scans": ["s"],
        "data": {"search": search, "peak": {"ok": True, "phot_sn": sn, "knob": {"d": [d0, 400.0]}, "bydet": {k: [0.1, 9] for k in det}}},
    }  # fmt: skip


def test_the_faint_peaks_stand_behind_their_own_filter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a far peak of detectors 1-3 under 6 sigma is faint; hidden in the list when asked, always there by its id."""
    from service.findings import faint_peak

    rule = {
        "a": (_peak("a", 5.9), True), "b": (_peak("b", 6.0), False), "c": (_peak("c", 5.1, d0=30.1), False),
        "d": (_peak("d", 5.1, det="4"), False), "e": (_peak("e", None), False), "f": (_peak("f", 5.1, search="deep"), False),
        "g": (_peak("g", 5.1, det="1234"), False),
    }  # fmt: skip
    for p, want in rule.values():
        assert faint_peak("peak", p["data"]) is want, p["id"]  # type: ignore[arg-type]
    db = FindingsDB(tmp_path / "findings.db", writer=True)
    rows = [{**p, "sigma": None, "rate_err": None} for p, _ in rule.values()]
    for r in rows:
        r.setdefault("versions", {})
    db.write_finds(rows)
    ids = lambda **k: {f["id"] for f in query(db, "peak", "all", None, 50, 0, **k)["finds"]}  # noqa: E731
    every = {p["id"] for p, _ in rule.values()}
    assert ids() == every and ids(faint="hide") == every - {"peak:a"}
    out = query(db, "peak", "all", None, 50, 0, faint="hide")
    assert (
        out["faint"] == 1 and out["count"] == 6 and out["counts_view"]["peak"] == 6 and query(db, "peak", "all", None, 5, 0)["faint"] == 1
    )
    assert ids(faint="hide", real=frozenset({"peak:a"})) == every  # a person said it is real
    assert ids(faint="hide", raised={"peak:a": "candidate"}) == every
    assert query(db, None, "any", None, 1, 0, only=frozenset({"peak:a"}))["finds"][0]["id"] == "peak:a"
    monkeypatch.setenv("SKY_HIDE_FAINT_PEAKS", "0")
    assert ids(faint="hide") == every and query(db, "peak", "all", None, 50, 0)["faint"] == 0


def test_one_faint_peak_in_ten_is_asked_about_by_its_id() -> None:
    from service.accounts.people import askable
    from service.findings import faint_sample

    n = sum(faint_sample(f"peak:{i:012x}") for i in range(5000))
    assert 400 < n < 600 and faint_sample("peak:00000000000a") == faint_sample("peak:00000000000a")
    assert not askable({"kind": "peak", "visible": True, "faint": True})
    assert askable({"kind": "peak", "visible": True}) and askable({"kind": "peak", "visible": True, "faint": True, "raised": "candidate"})


@pytest.mark.parametrize(
    ("ours", "theirs", "known"),
    [
        ((3759.0, -2794.0), (3981.977, -2466.832), True),  # eps Ind B (26 Nov prod)
        ((-6196.9, 0.0), (-8123.7, 673.2), True),  # WISE 0855: 1.3 times faster in the catalogue, 5 degrees apart
        ((6476.9, -1525.5), (None, None), False),  # NVSS J032033+154917, a radio source: no motion of its own
        ((10571.2, 3024.9), (-17.146, 0.062), False),  # a Gaia star that stands still
        ((3000.0, 0.0), (-3000.0, 0.0), False),  # as fast, the other way
        ((3000.0, 0.0), (7000.0, 0.0), False),  # the same way, more than twice as fast
        ((3000.0, 0.0), (2400.0, 1600.0), True),  # 34 degrees apart, 0.96 times as fast
    ],
)
def test_a_brown_dwarf_is_known_only_by_a_catalogue_source_that_moves_as_it_does(
    ours: tuple[float, float], theirs: tuple[float | None, float | None], known: bool
) -> None:
    from service.findings import _motion_identity

    f = {
        "kind": "browndwarf", "ra": 50.0, "dec": 10.0, "rate": math.hypot(*ours) / 1000,
        "data": {"group": "known", "source": {"pmra": ours[0], "pmdec": ours[1], "simbad": {"state": "ok", "name": "X", "otype": "Rad"}}},
    }  # fmt: skip
    ids = [
        {
            "catalogue": "simbad",
            "state": "ok",
            "name": "X",
            "type": "Rad",
            "label": "SIMBAD",
            "sep": 3.0,
            "raw": {"pmra": theirs[0], "pmdec": theirs[1]},
        }
    ]
    _motion_identity(f, ids)
    assert (status_of(f, ids) == "known") is known
    assert ("identity" in f) is known


def test_a_peak_in_a_bright_stars_glow_stands_behind_the_faint_filter(tmp_path: Path) -> None:
    """26 Nov: 64 of 485 far peaks, the first four Promising among them, had a 2MASS star of K <= 10 25-60″ away: its glow outside the
    search's mask. Asked once per far peak (wings_to_ask), kept apart from the catalogues, hidden as a faint peak is."""
    db = FindingsDB(tmp_path / "findings.db", writer=True)
    rows = [{**_peak(i, 8.0), "sigma": None, "rate_err": None, "versions": {}} for i in ("w", "x", "y")]
    rows.append({**_peak("z", 8.0, search="deep"), "sigma": None, "rate_err": None, "versions": {}})
    db.write_finds(rows)
    assert {r["id"] for r in db.wings_to_ask(time.time(), 10)} == {"peak:w", "peak:x", "peak:y"}  # far peaks only
    db.put_wing("peak:w", "ok", "2MASS J03270136+1245470", 41.0, 8.13)
    db.put_wing("peak:x", "none")
    db.put_wing("peak:y", "unavailable")
    db.db.commit()
    assert [r["id"] for r in db.wings_to_ask(time.time(), 10)] == []  # the unanswered one waits an hour
    assert [r["id"] for r in db.wings_to_ask(time.time() + 3700, 10)] == ["peak:y"]
    ids = lambda **k: {f["id"] for f in query(db, "peak", "all", None, 50, 0, **k)["finds"]}  # noqa: E731
    assert ids(faint="hide") == {"peak:x", "peak:y", "peak:z"} and query(db, "peak", "all", None, 50, 0, faint="hide")["faint"] == 1
    w = query(db, None, "any", None, 1, 0, only=frozenset({"peak:w"}))["finds"][0]  # its own link still opens it
    assert w["star_wing"] == {"name": "2MASS J03270136+1245470", "sep": 41.0, "k": 8.13} and w["faint"]
    assert "peak:w" in ids(faint="hide", real=frozenset({"peak:w"})) and "peak:w" in ids(faint="hide", raised={"peak:w": "candidate"})


def test_the_bright_star_is_asked_of_irsa_and_else_of_the_allwise_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    from service import context
    from service.findings import Findings

    async def tap(which: str, url: str, adql: str) -> str:
        assert "fp_psc" in adql and "k_m <= 10.0" in adql
        # 41″ east at K 8.1 (the band), 10″ at K 6 (inside 25″: not the wing), 59″ at K 9.9
        return "designation,ra,dec,k_m\n03270136+1245470,51.755753,12.763,8.13\n03270000+1245000,51.74692,12.764,6.0\n0327x,51.7604,12.764,9.9\n"

    f = Findings.__new__(Findings)
    f.catalogue = SimpleNamespace(_tap=tap)
    got = asyncio.run(f.bright_ir_star(51.744073, 12.764138))
    assert got["state"] == "ok" and got["k"] == 8.13 and 40 < got["sep"] < 42 and got["via"] == "irsa"

    async def down(which: str, url: str, adql: str) -> str:
        raise httpx.ConnectError("IRSA down")

    async def run(fn: Any, *a: Any) -> Any:
        return fn(*a)

    f.catalogue = SimpleNamespace(_tap=down)
    monkeypatch.setattr("service.findings.cpu.run", run)
    monkeypatch.setattr(
        context, "allwise_cone", lambda ra, dec, r: [{"ra": 51.7550, "dec": 12.7641, "k_m_2mass": 7.5, "designation": "J1"}]
    )
    got = asyncio.run(f.bright_ir_star(51.744073, 12.764138))
    assert got["state"] == "ok" and got["k"] == 7.5 and got["via"] == "allwise"
    monkeypatch.setattr(context, "allwise_cone", lambda ra, dec, r: [{"ra": 51.7441, "dec": 12.7641, "k_m_2mass": 7.5}])  # 0″: no wing
    assert asyncio.run(f.bright_ir_star(51.744073, 12.764138)) == {"state": "none", "via": "allwise"}


def test_the_wing_loop_asks_several_at_once_and_says_how_far_it_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from service import findings as fmod
    from service.findings import Findings

    monkeypatch.setattr(fmod, "WING_FIRST_S", 0.0)
    monkeypatch.setattr(fmod, "WING_PAUSE_S", 0.0)
    db = FindingsDB(tmp_path / "findings.db", writer=True)
    db.write_finds([{**_peak(f"p{i}", 8.0), "sigma": None, "rate_err": None, "versions": {}} for i in range(9)])
    at_once = {"now": 0, "most": 0}

    async def star(ra: float, dec: float) -> dict[str, Any]:
        at_once["now"] += 1
        at_once["most"] = max(at_once["most"], at_once["now"])
        await asyncio.sleep(0.02)
        at_once["now"] -= 1
        if ra < 13:
            raise httpx.ConnectError("down")
        return {"state": "ok", "name": "s", "sep": 40.0, "k": 8.0, "via": "allwise"}

    f = Findings.__new__(Findings)
    f.db = db
    f.bright_ir_star = star  # type: ignore[method-assign]

    async def run() -> None:
        t = asyncio.create_task(f.wing_loop())
        for _ in range(200):
            await asyncio.sleep(0.02)
            if "left" in caplog.text:
                break
        t.cancel()

    with caplog.at_level("WARNING", logger="sky.findings"):
        asyncio.run(run())
    assert 1 < at_once["most"] <= fmod.WING_AT_ONCE
    assert "wings: asked 9 (ok " in caplog.text and "failed " in caplog.text and "via AllWISE" in caplog.text
    states = dict(db.db.execute("SELECT state, count(*) FROM wings GROUP BY state").fetchall())
    assert sum(states.values()) == 9 and states.get("unavailable", 0) >= 1
