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
    assert r["status"] == "unexplained" and "Did not answer: Gaia DR3" in r["verdict"] and "Nothing within 6″ in SIMBAD" in r["verdict"]
    assert query(db, "red", "known", None, 10, 0)["count"] == 0
    only = query(db, None, "unexplained", "Barnard's Star", 2, 1)
    assert only["count"] == 4 and len(only["finds"]) == 2
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
