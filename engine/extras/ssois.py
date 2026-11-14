"""Archival images that cover a known orbit, from CADC's Solar System Object Image Search (SSOIS).

For a lead that has an orbit (a name, or elements) SSOIS lists the images of many archives (CFHT, Subaru, Pan-STARRS, DECam, HST, ...)
that contain the object at the time of the image: precovery and a way to confirm an orbit. There is no documented API: the search page's
own script calls `cadcbin/ssos/ssosclf.pl` with the form's fields and `format=tsv` gives a table. A search takes about a second.
Radio footprints (ALMA) are matches of stored footprints, not images of the object, and are left out unless asked for.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

BASE = "https://www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca"
ENDPOINT = f"{BASE}/cadcbin/ssos/ssosclf.pl"
RESULTS_PAGE = f"{BASE}/en/ssois/results.html"
RADIO = ("ALMA", "VLA", "SMA", "JCMT", "GBT")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NAME = re.compile(r"^[A-Za-z0-9 ()/_.+-]{1,60}$")


class SsoisError(RuntimeError):
    pass


@dataclass(frozen=True)
class Elements:
    """Heliocentric ecliptic J2000 elements at `epoch` (YYYY-MM-DD): a in au; e; i, node, peri, anom in degrees."""

    epoch: str
    a: float
    e: float
    i: float
    node: float
    peri: float
    anom: float


def _dates(start: str, end: str) -> dict[str, str]:
    if not (DATE.match(start) and DATE.match(end)) or start > end:
        raise ValueError("dates must be YYYY-MM-DD, start not after end")
    return {"epoch1": start.replace("-", " "), "epoch2": end.replace("-", " ")}


def name_query(name: str, start: str, end: str) -> dict[str, str]:
    if not NAME.match(name):
        raise ValueError("not an object name")
    return {
        "lang": "en",
        "search": "bynameCADC",
        "object": name,
        **_dates(start, end),
        "eellipse": "",
        "eunits": "arcseconds",
        "extres": "yes",
        "xyres": "yes",
    }


def orbit_query(el: Elements, start: str, end: str) -> dict[str, str]:
    if not DATE.match(el.epoch):
        raise ValueError("the epoch must be YYYY-MM-DD")
    if not (0.0 <= el.e < 1.0 and 0.0 < el.a < 1000.0 and 0.0 <= el.i <= 180.0):
        raise ValueError("elements out of range: need 0 <= e < 1, 0 < a < 1000 au, 0 <= i <= 180")
    return {
        "lang": "en",
        "search": "orbel",
        "epoch": el.epoch,
        "a": f"{el.a:.6f}",
        "e": f"{el.e:.6f}",
        "i": f"{el.i:.5f}",
        "longi": f"{el.node:.5f}",
        "peri": f"{el.peri:.5f}",
        "anom": f"{el.anom:.5f}",
        **_dates(start, end),
        "eellipse": "",
        "eunits": "arcseconds",
        "extres": "yes",
        "xyres": "yes",
    }


def human_link(query: dict[str, str]) -> str:
    """The results page of the same search, for a person to open."""
    return f"{RESULTS_PAGE}?{urlencode(query)}"


def parse(text: str, include_radio: bool = False) -> list[dict[str, Any]]:
    """The rows of an SSOIS TSV answer as dicts, earliest first."""
    if text.lstrip().startswith("<"):
        raise SsoisError("SSOIS answered with a page, not a table (the search failed)")
    rows: list[dict[str, Any]] = []
    for r in csv.DictReader(io.StringIO(text), delimiter="\t"):
        try:
            inst = r["Telescope/Instrument"].strip()
            if not include_radio and any(inst.upper().startswith(x) for x in RADIO):
                continue
            rows.append(
                {
                    "image": r["Image"].strip(),
                    "mjd": float(r["MJD"]),
                    "filter": r["Filter"].strip(),
                    "exposure_s": float(r["Exptime"]),
                    "ra": float(r["Object_RA"]),
                    "dec": float(r["Object_Dec"]),
                    "target": r["Image_target"].strip(),
                    "instrument": inst,
                    "datalink": (r.get("Datalink") or "").strip() or None,
                }
            )
        except (KeyError, ValueError):
            continue
    rows.sort(key=lambda x: float(x["mjd"]))
    return rows


async def search(client: httpx.AsyncClient, query: dict[str, str], include_radio: bool = False) -> list[dict[str, Any]]:
    r = await client.get(ENDPOINT, params={**query, "format": "tsv"})
    r.raise_for_status()
    return parse(r.text, include_radio)
