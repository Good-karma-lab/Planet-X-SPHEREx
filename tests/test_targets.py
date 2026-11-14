from __future__ import annotations

import json
from pathlib import Path

TARGETS = Path(__file__).resolve().parent.parent / "data" / "targets.json"


def test_exactly_the_five_test_targets_of_the_challenge() -> None:
    doc = json.loads(TARGETS.read_text(encoding="utf-8"))
    labels = [t["label"] for t in doc["targets"]]
    assert labels == ["Barnard's Star", "ε Indi B", "WISE 0855−0714", "RW Aurigae", "R Draconis"]
    assert doc["source"].startswith("https://") and doc["snapshot_at"]


def test_every_target_has_a_place_and_a_motion() -> None:
    for t in json.loads(TARGETS.read_text(encoding="utf-8"))["targets"]:
        assert 0 <= t["ra_deg"] < 360 and -90 <= t["dec_deg"] <= 90
        assert t["epoch"] == 2000.0
        assert t["pmra_mas_yr"] is not None and t["pmdec_mas_yr"] is not None
