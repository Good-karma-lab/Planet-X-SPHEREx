"""Findings: the finds of the result files, the database, the identification rows and the plain verdicts."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

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


def test_a_catalogue_that_never_answers_does_not_hold_a_find_unchecked_forever() -> None:
    from service.findings import MAX_ATTEMPTS

    red = {"kind": "red", "data": {}}
    ids = [{"catalogue": c, "state": "none", "attempts": 0} for c in ("simbad", "twomass", "allwise", "ned", "vsx")]
    retrying = [*ids, {"catalogue": "gaia", "state": "unavailable", "attempts": 2}]
    gave_up = [*ids, {"catalogue": "gaia", "state": "unavailable", "attempts": MAX_ATTEMPTS}]
    assert status_of(red, retrying) == "unchecked"  # Gaia decides a red source and is still being retried
    assert status_of(red, gave_up) == "unexplained"
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
    # the old call and the old fields stay as they were
    old = query(db, None, "all", None, 100, 0)
    assert old["total"] == 5 and {"status", "verdict", "idents"} <= set(old["finds"][0])
    assert flow_fields({"kind": "peak", "status": "halo", "score": 7.0}, None)["stage"] == "artefact"
