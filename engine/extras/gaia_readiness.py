"""Is Gaia DR4 (planned for 2 December 2026, positions at J2017.5) ready to be switched on?

The service already reads the release from three environment variables (service/upstream/gaia.py: SKY_GAIA_TABLE, SKY_GAIA_EPOCH,
SKY_GAIA_NAME). This module asks the ESA archive whether the DR4 schema and the columns the service uses exist, and whether the CDS-style
HATS listing offers a DR4 catalogue, and answers with the settings to apply. It changes nothing.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any

import httpx

TAP = "https://gea.esac.esa.int/tap-server/tap/sync"
HATS_LISTING = "https://data.lsdb.io/hats/"
PLANNED = "2026-12-02"
NEEDED = ["source_id", "ra", "dec", "pmra", "pmdec", "parallax", "parallax_error", "ruwe", "phot_g_mean_mag", "bp_rp"]
SETTINGS = {"SKY_GAIA_TABLE": "gaiadr4.gaia_source", "SKY_GAIA_EPOCH": "2017.5", "SKY_GAIA_NAME": "Gaia DR4"}


def _csv_column(text: str) -> list[str]:
    if text.lstrip().startswith("<"):
        raise ValueError("the TAP service answered with an error document")
    rows = list(csv.reader(io.StringIO(text)))
    return [r[0] for r in rows[1:] if r]


async def _tap(client: httpx.AsyncClient, adql: str) -> list[str]:
    r = await client.post(TAP, data={"LANG": "ADQL", "FORMAT": "csv", "REQUEST": "doQuery", "QUERY": adql})
    r.raise_for_status()
    return _csv_column(r.text)


async def probe(client: httpx.AsyncClient, now_iso_date: str) -> dict[str, Any]:
    """The state of DR4 in the ESA archive and in the HATS listing; `ready` means the three settings can be applied as they are."""
    out: dict[str, Any] = {"planned": PLANNED, "released": now_iso_date >= PLANNED, "settings": SETTINGS}
    try:
        schemas = await _tap(client, "SELECT schema_name FROM TAP_SCHEMA.schemas")
        out["schema"] = "gaiadr4" in schemas
        if out["schema"]:
            cols = await _tap(client, "SELECT column_name FROM TAP_SCHEMA.columns WHERE table_name = 'gaiadr4.gaia_source'")
            missing = [c for c in NEEDED if c not in cols]
            out["columns_missing"] = missing
        else:
            out["columns_missing"] = list(NEEDED)
    except Exception as e:  # said, not hidden
        out["schema"] = None
        out["columns_missing"] = None
        out["tap_error"] = f"{type(e).__name__}: {str(e)[:120]}"
    try:
        r = await client.get(HATS_LISTING)
        r.raise_for_status()
        out["hats"] = sorted(set(re.findall(r'href="(gaia_dr4[^"/]*)/?"', r.text)))
    except Exception as e:
        out["hats"] = None
        out["hats_error"] = f"{type(e).__name__}: {str(e)[:120]}"
    out["ready"] = bool(out["schema"]) and out["columns_missing"] == []
    out["note"] = (
        "the cone search from Parquet (service/upstream/gaia_hats.py) is written for the DR3 files: a DR4 copy needs its own root "
        "and a check of its partition order; until then DR4 is read through the TAP service"
    )
    return out
