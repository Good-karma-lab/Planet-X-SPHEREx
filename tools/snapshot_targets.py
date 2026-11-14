"""Write data/targets.json: the five test targets as SIMBAD gives them (J2000 place, proper motion, parallax).

The globe shows the test targets without waiting for SIMBAD; this snapshot is that local copy. Run again to refresh:
    uv run python tools/snapshot_targets.py
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

OUT = Path(__file__).resolve().parent.parent / "data" / "targets.json"
MIRRORS = (
    "https://simbad.cds.unistra.fr/simbad/sim-tap/sync",
    "https://simbad.harvard.edu/simbad/sim-tap/sync",
)
# label shown in the interface -> SIMBAD identifier
TARGETS = {
    "Barnard's Star": "NAME Barnard's star",
    "ε Indi B": "* eps Ind B",
    "WISE 0855−0714": "WISE J085510.83-071442.5",
    "RW Aurigae": "V* RW Aur",
    "R Draconis": "V* R Dra",
}
ABOUT = (
    "The five test targets of the challenge, as SIMBAD gives them (ICRS J2000 place, proper motion, parallax). "
    "A dated local snapshot so the globe never waits on SIMBAD; written by tools/snapshot_targets.py."
)
QUERY = """
SELECT i.id AS ident, b.main_id, b.ra, b.dec, b.pmra, b.pmdec, b.plx_value, b.plx_err, b.otype, b.sp_type,
       a.V AS v, a.J AS j
FROM basic AS b
JOIN ident AS i ON i.oidref = b.oid
LEFT JOIN allfluxes AS a ON a.oidref = b.oid
WHERE i.id IN ({ids})
"""


def query() -> tuple[str, list[dict[str, object]]]:
    ids = ", ".join("'" + v.replace("'", "''") + "'" for v in TARGETS.values())
    params = {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "json", "QUERY": QUERY.format(ids=ids)}
    last: Exception | None = None
    for url in MIRRORS:
        try:
            r = httpx.post(url, data=params, timeout=30, headers={"User-Agent": "spherex-sky/0.1 (target snapshot)"})
            r.raise_for_status()
            body = r.json()
            names = [c["name"] for c in body["metadata"]]
            return url, [dict(zip(names, row, strict=True)) for row in body["data"]]
        except (httpx.HTTPError, KeyError, ValueError) as e:
            last = e
    raise SystemExit(f"SIMBAD did not answer: {last}")


def main() -> int:
    url, rows = query()
    by_ident = {str(r["ident"]): r for r in rows}
    targets = []
    for label, ident in TARGETS.items():
        r = by_ident.get(ident)
        if r is None:
            print(f"SIMBAD has no row for {ident!r}", file=sys.stderr)
            return 1
        targets.append(
            {
                "label": label,
                "simbad_id": ident,
                "main_id": r["main_id"],
                "otype": r["otype"],
                "sp_type": r["sp_type"],
                "ra_deg": r["ra"],
                "dec_deg": r["dec"],
                "epoch": 2000.0,
                "pmra_mas_yr": r["pmra"],
                "pmdec_mas_yr": r["pmdec"],
                "plx_mas": r["plx_value"],
                "plx_err_mas": r["plx_err"],
                "v_mag": r["v"],
                "j_mag": r["j"],
            }
        )
    doc = {
        "about": ABOUT,
        "source": url,
        "snapshot_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "targets": targets,
    }
    OUT.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(targets)} targets) from {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
