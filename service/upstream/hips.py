"""All-sky survey tiles for the globe (HiPS from the IRSA mirror), kept on disk.

A tile never changes, so the first request fetches it and every later one is served from the store. The globe asks for the
Allsky image of order 3 (the whole sky at about 0.1° per pixel) and, zoomed in, for single tiles of a deeper order.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

import httpx

BASE = "https://irsa.ipac.caltech.edu/data/hips/"
SURVEYS = {
    "dss2": ("CDS/DSS2/color", 9),
    "2mass": ("CDS/2MASS/Color", 9),
    "wise": ("CDS/AllWISE/RGB-W4-W2-W1", 8),
    "dust": ("CDS/IRIS/color", 3),
}
TILE = re.compile(r"^(allsky|\d{1,9})$")


class HipsTiles:
    def __init__(self, root: Path, client: httpx.Client | None = None) -> None:
        self.root = root
        self.client = client or httpx.Client(timeout=httpx.Timeout(30, connect=10), headers={"User-Agent": "spherex-sky/0.1"})
        self.lock = threading.Lock()

    @staticmethod
    def relative(survey: str, order: int, tile: str) -> str:
        """Path of a tile inside a survey: Norder3/Allsky.jpg or Norder{o}/Dir{10000·(i//10000)}/Npix{i}.jpg."""
        if survey not in SURVEYS or not TILE.match(tile):
            raise ValueError("unknown survey or tile")
        deepest = SURVEYS[survey][1]
        if tile == "allsky":
            if order != 3:
                raise ValueError("the Allsky image exists for order 3")
            return "Norder3/Allsky.jpg"
        i = int(tile)
        if not 3 <= order <= deepest or i >= 12 * 4**order:
            raise ValueError("tile outside the survey")
        return f"Norder{order}/Dir{(i // 10000) * 10000}/Npix{i}.jpg"

    def get(self, survey: str, order: int, tile: str) -> bytes | None:
        rel = self.relative(survey, order, tile)
        path = self.root / survey / rel
        if path.is_file():
            return path.read_bytes()
        r = self.client.get(BASE + SURVEYS[survey][0] + "/" + rel)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(r.content)
        tmp.replace(path)
        return r.content
