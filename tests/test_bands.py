from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.bands import BANDS_FILE, load_bands


def test_seven_named_bands_in_order() -> None:
    bands = load_bands()
    assert [b.key for b in bands] == ["star", "h2o", "ch4", "ice", "pah", "co2", "co"]
    assert bands[0].name == "Starlight"


def test_windows_are_half_open() -> None:
    star = load_bands()[0]
    assert star.contains(0.75)
    assert not star.contains(1.10)


def test_methane_and_dust_windows_overlap_on_purpose() -> None:
    by_key = {b.key: b for b in load_bands()}
    assert by_key["ch4"].contains(3.3) and by_key["pah"].contains(3.3)


def test_invalid_window_is_refused(tmp_path: Path) -> None:
    raw = json.loads(BANDS_FILE.read_text(encoding="utf-8"))
    raw["bands"][0]["windows"] = [[0.5, 1.1]]
    bad = tmp_path / "bands.json"
    bad.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        load_bands(bad)
