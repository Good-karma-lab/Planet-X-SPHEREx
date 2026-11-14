"""A fixed regression field from the SPHEREx deep sequences: R Draconis, at the northern ecliptic pole.

The deep collections (`spherex_dr1_deep`, `spherex_qr3_deep`) repeat the same few fields many times, so the same place is seen over and
over: a set of exposures that does not change between runs, usable to test stacking, the passes and variable-star light curves. The
snapshot in `data/regression/r_dra_deep.json` lists the Level-2 files (S3 keys) IRSA's SIA service returned for the star's place; the check
compares it with any exposure index: a file the archive lists but the index lacks is an index gap.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx
from astropy.io.votable import parse

SIA = "https://irsa.ipac.caltech.edu/SIA"
COLLECTIONS = ("spherex_dr1_deep", "spherex_qr3_deep")
SNAPSHOT = Path(__file__).resolve().parents[2] / "data" / "regression" / "r_dra_deep.json"
RA, DEC, RADIUS_DEG = 248.16761083295, 66.75497941722, 0.05  # R Dra, J2000 (the star hardly moves: 20 mas/yr)


def parse_sia(content: bytes) -> list[dict[str, Any]]:
    """The exposures of an SIA VOTable answer: S3 key, detector, start time (MJD), obs id."""
    votable = parse(io.BytesIO(content), verify="ignore")
    out: list[dict[str, Any]] = []
    for table in votable.iter_tables():
        names = {f.name for f in table.fields}
        if not {"cloud_access", "t_min", "energy_bandpassname"} <= names:
            continue
        for row in table.array:
            cloud = json.loads(str(row["cloud_access"]) or "{}")
            key = cloud.get("aws", {}).get("key")
            if not key:
                continue
            out.append(
                {
                    "key": key,
                    "det": int(str(row["energy_bandpassname"]).rsplit("D", 1)[-1]),
                    "mjd": round(float(row["t_min"]), 5),
                    "obs_id": str(row["obs_id"]),
                }
            )
    return out


async def fetch(client: httpx.AsyncClient) -> dict[str, list[dict[str, Any]]]:
    """The deep-sequence files over R Dra, by collection, straight from IRSA."""
    out: dict[str, list[dict[str, Any]]] = {}
    for coll in COLLECTIONS:
        r = await client.get(SIA, params={"COLLECTION": coll, "POS": f"circle {RA} {DEC} {RADIUS_DEG}"})
        r.raise_for_status()
        out[coll] = sorted(parse_sia(r.content), key=lambda e: (e["mjd"], e["det"]))
    return out


def load(path: Path = SNAPSHOT) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def compact(snapshot: dict[str, list[dict[str, Any]]], taken: str) -> dict[str, Any]:
    """The snapshot as stored: a list of [key, detector, mjd] per collection (small and diff-friendly)."""
    return {
        "field": {"name": "R Draconis", "ra": RA, "dec": DEC, "radius_deg": RADIUS_DEG},
        "taken": taken,
        "source": SIA,
        "collections": {c: [[e["key"], e["det"], e["mjd"]] for e in rows] for c, rows in snapshot.items()},
    }


def compare(snapshot: dict[str, Any], index_keys: Iterable[str]) -> dict[str, Any]:
    """What the archive lists over R Dra against the keys an exposure index holds near it."""
    have = set(index_keys)
    report: dict[str, Any] = {"collections": {}, "missing": [], "ok": True}
    for coll, rows in snapshot["collections"].items():
        keys = [r[0] for r in rows]
        missing = [k for k in keys if k not in have]
        report["collections"][coll] = {
            "files": len(keys),
            "in_index": len(keys) - len(missing),
            "detectors": {str(d): sum(1 for r in rows if r[1] == d) for d in range(1, 7)},
            "first_mjd": min((r[2] for r in rows), default=None),
            "last_mjd": max((r[2] for r in rows), default=None),
        }
        report["missing"] += missing
    report["ok"] = not report["missing"]
    report["missing"] = report["missing"][:50]
    return report
