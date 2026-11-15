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
        async def get(self, url: str, params: dict[str, str]) -> _Resp:
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
