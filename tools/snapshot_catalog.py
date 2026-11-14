"""Write data/catalog.json: the objects the globe lists and pins, as SIMBAD gives them.

Four groups that matter for a sky that changes: brown dwarfs, fast stars (proper motion above 1″/yr), Mira variables and young
variable stars. A dated local snapshot so the globe opens without waiting on SIMBAD. Run again to refresh:
    uv run python tools/snapshot_catalog.py
"""

from __future__ import annotations

import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "catalog.json"
WEB = ROOT / "web" / "public" / "catalog.json"
MIRRORS = (
    "https://simbad.harvard.edu/simbad/sim-tap/sync",
    "https://simbad.u-strasbg.fr/simbad/sim-tap/sync",
)
COLS = "b.main_id, b.ra, b.dec, b.pmra, b.pmdec, b.otype, b.sp_type, b.pmra*b.pmra + b.pmdec*b.pmdec AS pm2"
GROUPS = {
    # key -> (ADQL, how many at most)
    "bd": (f"SELECT TOP 2000 {COLS} FROM basic AS b WHERE b.otype = 'BD*..' AND b.ra IS NOT NULL ORDER BY pm2 DESC", 2000),
    "fast": (
        f"SELECT TOP 700 {COLS} FROM basic AS b WHERE b.pmra*b.pmra + b.pmdec*b.pmdec > 1000000 "
        "AND b.otype != 'BD*..' AND b.ra IS NOT NULL ORDER BY pm2 DESC",
        700,
    ),
    "mira": (
        f"SELECT TOP 1000 {COLS} , a.K AS kmag FROM basic AS b JOIN allfluxes AS a ON a.oidref = b.oid WHERE b.otype = 'Mi*' "
        "AND a.K IS NOT NULL ORDER BY kmag ASC",
        1000,
    ),
    "young": (
        f"SELECT TOP 400 {COLS} , a.K AS kmag FROM basic AS b JOIN allfluxes AS a ON a.oidref = b.oid WHERE b.otype = 'TT*' "
        "AND a.K IS NOT NULL ORDER BY kmag ASC",
        400,
    ),
}
OTYPE_WORD = {
    "BD*": "Brown dwarf",
    "Mi*": "Mira variable",
    "TT*": "T Tauri star",
    "PM*": "High proper-motion star",
    "WD*": "White dwarf",
    "BY*": "BY Dra variable",
    "Er*": "Eruptive variable",
    "Ro*": "Rotating variable",
    "LM*": "Low-mass star",
    "*": "Star",
    "**": "Double star",
    "SB*": "Spectroscopic binary",
}
ABOUT = (
    "Brown dwarfs, fast stars (proper motion above 1″/yr), Mira variables and young variable stars as SIMBAD gives them "
    "(ICRS J2000 place and proper motion). A dated local snapshot so the globe opens without waiting on SIMBAD; written "
    "by tools/snapshot_catalog.py."
)


def ask(adql: str) -> list[dict[str, object]]:
    params = {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "json", "QUERY": adql}
    last: Exception | None = None
    for url in MIRRORS:
        try:
            r = httpx.post(url, data=params, timeout=120, headers={"User-Agent": "spherex-sky/0.1 (catalog snapshot)"})
            r.raise_for_status()
            if 'QUERY_STATUS" value="ERROR' in r.text[:2000]:
                raise ValueError(r.text[:600])
            body = r.json()
            names = [c["name"] for c in body["metadata"]]
            return [dict(zip(names, row, strict=True)) for row in body["data"]]
        except (httpx.HTTPError, KeyError, ValueError) as e:
            last = e
    raise SystemExit(f"SIMBAD did not answer: {last}")


def tidy(name: str) -> str:
    """SIMBAD main identifiers with the catalogue prefixes people do not say ('NAME ', 'V* ', '* ')."""
    for p in ("NAME ", "V* ", "* ", "** "):
        if name.startswith(p):
            return name[len(p) :]
    return name


def main() -> int:
    targets = json.loads((ROOT / "data" / "targets.json").read_text())["targets"]
    bench = {t["main_id"] for t in targets}
    seen: set[str] = set()
    out: list[dict[str, object]] = []
    for t in targets:
        pm = math.hypot(t["pmra_mas_yr"] or 0, t["pmdec_mas_yr"] or 0)
        kind = "bd" if str(t["otype"]).startswith("BD") or t["label"].startswith("WISE") else "fast" if pm > 1000 else "var"
        out.append(
            {
                "n": t["label"],
                "ra": round(t["ra_deg"], 6),
                "dec": round(t["dec_deg"], 6),
                "k": kind,
                "pmra": t["pmra_mas_yr"],
                "pmdec": t["pmdec_mas_yr"],
                "sp": t.get("sp_type") or "",
                "c": OTYPE_WORD.get(str(t["otype"]), str(t["otype"])),
                "b": 1,
            }
        )
        seen.add(t["main_id"])
    counts = {}
    for key, (adql, _) in GROUPS.items():
        rows = ask(adql)
        n = 0
        for r in rows:
            mid = str(r["main_id"])
            if mid in seen or mid in bench or r["ra"] is None:
                continue
            seen.add(mid)
            n += 1
            out.append(
                {
                    "n": tidy(mid),
                    "ra": round(float(r["ra"]), 6),
                    "dec": round(float(r["dec"]), 6),
                    "k": key,
                    "pmra": r["pmra"],
                    "pmdec": r["pmdec"],
                    "sp": r["sp_type"] or "",
                    "c": OTYPE_WORD.get(str(r["otype"]), str(r["otype"])),
                }
            )
        counts[key] = n
        print(f"{key}: {n}", file=sys.stderr)
    doc = {
        "about": ABOUT,
        "source": MIRRORS[0],
        "snapshot_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "counts": counts,
        "catalog": out,
    }
    text = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    OUT.write_text(text + "\n")
    WEB.write_text(text + "\n")
    print(f"wrote {len(out)} objects to {OUT.relative_to(ROOT)} and {WEB.relative_to(ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
