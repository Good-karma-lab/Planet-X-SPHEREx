"""Findings: the finds of the result files, the database, the identification rows and the plain verdicts."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

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
    return f


def test_film_track_is_known_when_the_same_body_stands_where_it_does_at_two_of_three_visits() -> None:
    import sqlite3 as sq

    from service.findings import _iso

    mjds = [61000.0, 61005.0, 61010.0]
    days = [_iso(m)[:10] for m in mjds]
    body = [["9952 Test", "2.0", "0.0", "58.0", "0.0", "18.0"]]
    f = _jpl_by_date({days[0]: body, days[2]: body})  # seen at two visits (the middle date has none)
    vis = [{"ra": 120.0, "dec": 10.0, "mjd": m} for m in mjds]
    db = sq.connect(":memory:")
    db.row_factory = sq.Row
    db.execute("CREATE TABLE t (kind, ra, dec, data)")
    db.execute("INSERT INTO t VALUES ('film', 120.0, 10.0, ?)", (json.dumps({"track": {"rate": 58.0, "pa": 90.0}, "visits": vis}),))
    row = db.execute("SELECT * FROM t").fetchone()
    got = asyncio.run(f.sbident_visits(row))
    assert got["state"] == "ok" and got["name"] == "9952 Test" and got["visits_matched"] == 2
    f2 = _jpl_by_date({days[0]: body})  # one of three: not enough
    assert asyncio.run(f2.sbident_visits(row))["state"] == "none"


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
        "rate": 7.9,
        "data": {"group": "known", "source": {"simbad": {"state": "ok", "name": "WISEA J085510.74-071442.5", "otype": "BD*"}}},
    }
    assert status_of(real, []) == "known"
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
        "data": {"group": "known", "source": {"simbad": {"state": "ok", "name": "WISEA J085510.74-071442.5", "otype": "Y*"}}},
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
    me = SimpleNamespace(db=SimpleNamespace(path="x"), targets=[])
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
