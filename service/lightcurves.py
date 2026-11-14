"""Light curves of a source from other surveys: ZTF (optical, 2018 on) and NEOWISE (3.4 and 4.6 µm, 2014–2024), from IRSA.

The place is moved to each survey's middle epoch by the source's proper motion; a source answers only within 2″ (ZTF) or 3″
(NEOWISE). ZTF points with any catalogue flag and NEOWISE frames of quality 0 are left out. Each part says ok / none / unavailable.
"""

from __future__ import annotations

import csv
import io
from typing import Any

import httpx

from service.spectrum import place_at

ZTF = "https://irsa.ipac.caltech.edu/cgi-bin/ZTF/nph_light_curves"
TAP = "https://irsa.ipac.caltech.edu/TAP/sync"
MAX_POINTS = 3000
# IRSA TAP can take a minute for NEOWISE (a table of 2·10¹⁰ rows): its own long timeout
CLIENT = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10), headers={"User-Agent": "spherex-sky/0.1"})


def _mjd(year: float) -> float:
    return 51544.5 + (year - 2000.0) * 365.25


async def ztf(params: dict[str, Any], client: httpx.AsyncClient = CLIENT) -> dict[str, Any]:
    ra, dec = place_at(params, _mjd(2021.5))
    try:
        r = await client.get(ZTF, params={"POS": f"CIRCLE {ra:.6f} {dec:.6f} {2.0 / 3600:.6f}", "FORMAT": "csv"})
        r.raise_for_status()
    except Exception as e:  # said, never cached
        return {"state": "unavailable", "error": type(e).__name__, "points": []}
    rows = list(csv.DictReader(io.StringIO(r.text)))
    pts = []
    for x in rows:
        try:
            if int(x["catflags"]) != 0:
                continue
            pts.append(
                {
                    "mjd": round(float(x["mjd"]), 4),
                    "mag": round(float(x["mag"]), 3),
                    "err": round(float(x["magerr"]), 3),
                    "band": x["filtercode"],
                }
            )
        except (KeyError, ValueError):
            continue
    pts.sort(key=lambda p: float(p["mjd"] or 0.0))
    return {"state": "ok" if pts else "none", "points": pts[:MAX_POINTS]}


async def neowise(params: dict[str, Any], client: httpx.AsyncClient = CLIENT) -> dict[str, Any]:
    ra, dec = place_at(params, _mjd(2019.0))
    q = (
        "SELECT mjd, w1mpro, w1sigmpro, w2mpro, w2sigmpro FROM neowiser_p1bs_psd WHERE qual_frame > 0 AND "
        f"CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {3.0 / 3600:.6f})) = 1"
    )
    try:
        r = await client.post(TAP, data={"QUERY": q, "FORMAT": "csv", "LANG": "ADQL", "REQUEST": "doQuery"})
        r.raise_for_status()
    except Exception as e:  # said, never cached
        return {"state": "unavailable", "error": f"{type(e).__name__}: {str(e)[:120]}", "points": []}
    if r.text.lstrip().startswith("<"):
        return {"state": "unavailable", "error": "TAP error", "points": []}

    def num(v: str) -> float | None:
        try:
            return round(float(v), 3)
        except ValueError:
            return None

    pts = [
        {
            "mjd": round(float(x["mjd"]), 4),
            "w1": num(x["w1mpro"]),
            "w1e": num(x["w1sigmpro"]),
            "w2": num(x["w2mpro"]),
            "w2e": num(x["w2sigmpro"]),
        }
        for x in csv.DictReader(io.StringIO(r.text))
        if x.get("mjd")
    ]
    pts.sort(key=lambda p: float(p["mjd"] or 0.0))
    return {"state": "ok" if pts else "none", "points": pts[:MAX_POINTS]}
