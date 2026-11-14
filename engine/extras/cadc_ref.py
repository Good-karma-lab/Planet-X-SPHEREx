"""Deep static optical references of a place from the Canadian Astronomy Data Centre (CADC): find them, cut them out.

Only deep, calibrated stacks count: processing level 3 or higher and at least 300 s of exposure (CFHT MegaPipe and HST stacks where they
exist). The search is a CAOM2 TAP query by position; the cutout is the archive's SODA service, asked by the artifact of the stack's file
(DataLink resolves it from the plane's publisher ID). Anonymous, no key. A cutout of 60″ takes about ten seconds the first time.
"""

from __future__ import annotations

import csv
import io
import math
import re
import struct
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass
from typing import Any

import httpx
import numpy as np
from astropy.io import fits

TAP = "https://ws.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/argus/sync"
DATALINK = "https://ws.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/caom2ops/datalink"
SODA = "https://ws.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/caom2ops/sync"
COLLECTIONS = ("CFHTMEGAPIPE", "HSTHLA")
MIN_LEVEL = 3
MIN_EXPOSURE_S = 300.0
MAX_CUTOUT_ARCSEC = 300.0
USER_AGENT = "spherex-sky/0.1"


class CadcError(RuntimeError):
    pass


@dataclass(frozen=True)
class Reference:
    collection: str
    publisher_id: str
    band: str
    level: int
    exposure_s: float
    pixel_arcsec: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "collection": self.collection,
            "publisher_id": self.publisher_id,
            "band": self.band,
            "level": self.level,
            "exposure_s": self.exposure_s,
            "pixel_arcsec": self.pixel_arcsec,
        }


def search_adql(ra: float, dec: float) -> str:
    colls = ",".join(f"'{c}'" for c in COLLECTIONS)
    return (
        "SELECT o.collection AS collection, p.publisherID AS publisher_id, p.energy_bandpassName AS band, "
        "p.calibrationLevel AS level, p.time_exposure AS exposure_s, p.position_sampleSize AS pixel_arcsec "
        "FROM caom2.Observation o JOIN caom2.Plane p ON p.obsID = o.obsID "
        f"WHERE 1 = INTERSECTS(POINT('ICRS', {ra:.6f}, {dec:.6f}), p.position_bounds) AND o.collection IN ({colls}) "
        f"AND p.calibrationLevel >= {MIN_LEVEL} AND p.time_exposure >= {MIN_EXPOSURE_S:.0f}"
    )


def parse_references(text: str) -> list[Reference]:
    """The references in a TAP CSV answer, deepest exposure first; rows without a publisher ID or an exposure are dropped."""
    if text.lstrip().startswith("<"):
        raise CadcError("the TAP service answered with an error document")
    out: list[Reference] = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            pix = float(row["pixel_arcsec"]) if row.get("pixel_arcsec") else None
            out.append(
                Reference(
                    row["collection"],
                    row["publisher_id"],
                    row.get("band") or "",
                    int(float(row["level"])),
                    float(row["exposure_s"]),
                    None if pix is None or not math.isfinite(pix) else round(pix, 4),
                )
            )
        except (KeyError, ValueError):
            continue
    out.sort(key=lambda r: -r.exposure_s)
    return out


async def find(client: httpx.AsyncClient, ra: float, dec: float) -> list[Reference]:
    r = await client.post(TAP, data={"LANG": "ADQL", "FORMAT": "csv", "QUERY": search_adql(ra, dec)}, follow_redirects=True)
    r.raise_for_status()
    return parse_references(r.text)


def artifact_of(datalink_xml: str) -> str:
    """The artifact URI (`cadc:CFHTSG/file.fits`) of the file a DataLink answer calls `#this`."""
    root = ET.fromstring(datalink_xml)
    for tr in root.iter():
        if not tr.tag.endswith("TR"):
            continue
        tds = [(td.text or "") for td in tr if td.tag.endswith("TD")]
        if len(tds) > 4 and tds[4] == "#this":
            m = re.search(r"/files/(cadc:[^?\s]+)$", tds[1])
            if m:
                return m.group(1)
    raise CadcError("DataLink lists no file for this plane")


async def artifact(client: httpx.AsyncClient, publisher_id: str) -> str:
    r = await client.get(DATALINK, params={"ID": publisher_id}, follow_redirects=True)
    r.raise_for_status()
    return artifact_of(r.text)


async def cutout(client: httpx.AsyncClient, art: str, ra: float, dec: float, size_arcsec: float) -> bytes:
    """The FITS cutout of `size_arcsec` (the diameter of the circle) around a position, from the SODA service."""
    if not 1.0 <= size_arcsec <= MAX_CUTOUT_ARCSEC:
        raise ValueError(f"size must be between 1 and {MAX_CUTOUT_ARCSEC:.0f} arcsec")
    if not re.fullmatch(r"cadc:[A-Za-z0-9_./+-]+\.fits", art):
        raise ValueError("not an archive file name")
    params = {"ID": art, "CIRCLE": f"{ra:.6f} {dec:.6f} {size_arcsec / 7200.0:.6f}"}
    r = await client.get(SODA, params=params, follow_redirects=True)
    r.raise_for_status()
    if not r.content.startswith(b"SIMPLE"):
        raise CadcError("the cutout service did not return a FITS file")
    return r.content


def to_png(fits_bytes: bytes) -> tuple[bytes, dict[str, Any]]:
    """An 8-bit grey PNG of the first image of a FITS file (asinh stretch, sky level to the 99.7th percentile, north up) and facts."""
    with fits.open(io.BytesIO(fits_bytes)) as hdul:
        hdu = next((h for h in hdul if h.data is not None and h.data.ndim == 2), None)
        if hdu is None:
            raise CadcError("no image in the cutout")
        data = np.asarray(hdu.data, dtype=np.float64)
        header = hdu.header
    good = np.isfinite(data)
    if not good.any():
        raise CadcError("the cutout has no valid pixels")
    sky = float(np.median(data[good]))
    hi = float(np.percentile(data[good], 99.7))
    span = max(hi - sky, 1e-9)
    soft = 0.05 * span
    s = np.arcsinh((np.where(good, data, sky) - sky) / soft) / math.asinh(span / soft)
    img = np.clip(s, 0.0, 1.0)
    img8 = np.ascontiguousarray((img * 255.0 + 0.5).astype(np.uint8)[::-1])
    facts = {
        "width": int(img8.shape[1]),
        "height": int(img8.shape[0]),
        "sky": round(sky, 4),
        "exptime": header.get("EXPTIME"),
        "filter": header.get("FILTER") or header.get("FILTNAM"),
    }
    return _png_gray(img8), facts


def _png_gray(a: np.ndarray) -> bytes:
    h, w = a.shape

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + a[i].tobytes() for i in range(h))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )
