"""The geocentric position of the SPHEREx spacecraft at given times, from JPL Horizons (target -163182), in the ICRF frame, km.

The MPC wants this for every observation made from a spacecraft. Horizons has the mission's reconstructed trajectory from 2025-03-12 and
a prediction a few months ahead (the ephemeris says so in its header); a time outside that range is refused by Horizons.
"""

from __future__ import annotations

import re

import httpx

HORIZONS = "https://ssd.jpl.nasa.gov/api/horizons.api"
TARGET = "-163182"
CHUNK = 40  # times per request (TLIST)
_ROW = re.compile(r"^\s*(?P<jd>[0-9.]+)\s*,[^,]*,\s*(?P<x>[-+0-9.Ee]+)\s*,\s*(?P<y>[-+0-9.Ee]+)\s*,\s*(?P<z>[-+0-9.Ee]+)\s*,", re.MULTILINE)


class OrbitError(RuntimeError):
    pass


def parse_vectors(result: str) -> list[tuple[float, float, float, float]]:
    """(JD UT, x, y, z km) rows of a Horizons CSV vector table, between its $$SOE and $$EOE lines."""
    a, b = result.find("$$SOE"), result.find("$$EOE")
    if a < 0 or b < a:
        raise OrbitError("Horizons returned no vectors")
    return [(float(m["jd"]), float(m["x"]), float(m["y"]), float(m["z"])) for m in _ROW.finditer(result[a:b])]


async def positions(client: httpx.AsyncClient, mjds: list[float]) -> list[tuple[float, float, float]]:
    """The spacecraft's (x, y, z) in km, geocentric ICRF, at each UTC MJD (in the order given)."""
    out: list[tuple[float, float, float]] = []
    for i in range(0, len(mjds), CHUNK):
        part = mjds[i : i + CHUNK]
        tlist = " ".join(f"{m + 2400000.5:.6f}" for m in part)
        params = {
            "format": "json",
            "COMMAND": f"'{TARGET}'",
            "EPHEM_TYPE": "VECTORS",
            "CENTER": "'500@399'",
            "REF_PLANE": "FRAME",
            "REF_SYSTEM": "ICRF",
            "OUT_UNITS": "KM-S",
            "VEC_TABLE": "1",
            "CSV_FORMAT": "YES",
            "TIME_TYPE": "UT",
            "TLIST": f"'{tlist}'",
        }
        r = await client.get(HORIZONS, params=params)
        r.raise_for_status()
        body = r.json()
        if "error" in body:
            raise OrbitError(str(body["error"])[:200])
        rows = parse_vectors(str(body.get("result", "")))
        if len(rows) != len(part):
            raise OrbitError(f"Horizons gave {len(rows)} positions for {len(part)} times")
        out += [(x, y, z) for _, x, y, z in rows]
    return out
