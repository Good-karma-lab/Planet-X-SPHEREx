from __future__ import annotations

import csv
import io
from typing import Any

from service.routes_export import COLUMNS, csv_rows, to_csv


def body() -> dict[str, Any]:
    return {
        "tracks": [
            {
                "ra_2025_9": 133.76,
                "dec_2025_9": -7.24,
                "speed": 7.82,
                "pa": 281.0,
                "snr": [None, None],
                "snr_long": 15.4,
                "check": "none",
                "source": "red",
                "name": "WISEA J085510.74-071442.5",
                "colour": 1.98,
            },
            {
                "ra_2025_9": 10.0,
                "dec_2025_9": 20.0,
                "speed": 1.5,
                "pa": 90.0,
                "snr": [8.0, 11.5],
                "check": "same",
                "gaia": {"source_id": "42"},
                "passes": [0, 1],
            },
        ],
        "pairs": [
            {
                "separation_arcsec": 11.5,
                "verdict": "steady",
                "change_sigma": 2.0,
                "components": [{"ra": 1.0, "dec": 2.0, "snr": 99}, {"ra": 1.1, "dec": 2.1}],
            }
        ],
        "brown_dwarfs": {
            "candidates": [
                {
                    "ra": 5.0,
                    "dec": 6.0,
                    "pm": 3000.0,
                    "pa": 10.0,
                    "snr_long": 9.0,
                    "colour": 3.0,
                    "status": "unidentified",
                    "gaia": {"verdict": "none"},
                }
            ],
            "known": [
                {
                    "ra": 7.0,
                    "dec": 8.0,
                    "pm": 4700.0,
                    "pa": 120.0,
                    "snr_long": 285.0,
                    "colour": 3.8,
                    "status": "known brown dwarf",
                    "simbad": {"name": "* eps Ind B"},
                    "gaia": {"verdict": "same"},
                }
            ],
        },
        "red_sources": [{"ra": 3.0, "dec": 4.0, "colour": 2.8, "snr_long": 17.0, "moves": False, "lower_limit": False}],
        "anomalies": [{"ra": 9.0, "dec": 9.5, "snr": 42.8, "score": 10.7, "moves": False}],
    }


def test_every_kind_of_find_is_a_row_of_the_csv() -> None:
    rows = list(csv.DictReader(io.StringIO(to_csv(body()))))
    assert rows[0].keys() == set(COLUMNS)
    assert [r["kind"] for r in rows] == [
        "mover",
        "mover",
        "pair",
        "brown_dwarf_candidate",
        "known_brown_dwarf",
        "red_source",
        "anomaly",
    ]
    wise = rows[0]
    assert wise["name"].startswith("WISEA") and wise["snr"] == "15.4" and wise["note"] == "found in the red bands"
    assert rows[1]["snr"] == "11.5" and rows[1]["gaia"] == "42"
    assert rows[4]["name"] == "* eps Ind B" and rows[4]["speed_arcsec_per_year"] == "4.7"


def test_an_empty_field_still_gives_a_header() -> None:
    empty = {"tracks": [], "pairs": [], "brown_dwarfs": {"candidates": [], "known": []}, "red_sources": [], "anomalies": []}
    assert csv_rows(empty) == [] and to_csv(empty).startswith("kind,id,ra,dec")
