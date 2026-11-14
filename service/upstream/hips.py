"""All-sky survey tiles for the globe (HiPS from the IRSA mirror), kept on disk.

A tile never changes, so the first request fetches it and every later one is served from the store. The globe asks for the
Allsky image of order 3 (the whole sky at about 0.1° per pixel) and, zoomed in, for single tiles of a deeper order.
"""

from __future__ import annotations

import re
import secrets
import threading
from pathlib import Path

import httpx

BASE = "https://irsa.ipac.caltech.edu/data/hips/"
SURVEYS = {
    "dss2": ("CDS/DSS2/color", 9),
    "2mass": ("CDS/2MASS/Color", 9),
    "wise": ("CDS/AllWISE/RGB-W4-W2-W1", 8),
    "dust": ("CDS/IRIS/color", 3),
    # archive images of a field (single bands, for the Reference picture and the archive frames of the Lab)
    "dss2red": ("CDS/DSS2/red", 9),
    "2massk": ("CDS/2MASS/K", 9),
    "wise1": ("CDS/AllWISE/W1", 8),
    "sdss": ("CDS/SDSS/DR9/color", 10),
    # three frames of the SPHEREx DR1 all-sky cube (frame = 17·(band − 1) + channel − 1): 0.91, 1.86 and 4.50 µm, for the globe's
    # SPHEREx background (a visualisation product: 8-bit, asinh-stretched)
    "spx8": ("SPHEREx/spherex_allsky_cube", 5),
    "spx40": ("SPHEREx/spherex_allsky_cube", 5),
    "spx88": ("SPHEREx/spherex_allsky_cube", 5),
}
FRAME = {"spx8": 8, "spx40": 40, "spx88": 88}
TILE = re.compile(r"^(allsky|\d{1,9})$")


class HipsTiles:
    def __init__(self, root: Path, client: httpx.Client | None = None) -> None:
        self.root = root
        self.client = client or httpx.Client(timeout=httpx.Timeout(30, connect=10), headers={"User-Agent": "spherex-sky/0.1"})
        self.lock = threading.Lock()
        self.aclient: httpx.AsyncClient | None = None
        self.count = -1  # tiles on disk (counted once in the background, then kept up to date)
        self.fetched = 0
        self.failed = 0

    def count_tiles(self) -> int:
        self.count = sum(1 for p in self.root.rglob("*") if p.suffix in (".jpg", ".png")) if self.root.is_dir() else 0
        return self.count

    async def aget(self, survey: str, order: int, tile: str) -> bytes | None:
        """A tile from disk, or from the mirror with a short timeout and one retry. Never waits behind a job or a lock: its own
        async client, parallel fetches."""
        rel = self.relative(survey, order, tile)
        path = self.root / survey / rel
        if path.is_file():
            return path.read_bytes()
        if self.aclient is None:
            self.aclient = httpx.AsyncClient(
                timeout=httpx.Timeout(8, connect=4),
                headers={"User-Agent": "spherex-sky/0.1"},
                limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
            )
        url = BASE + SURVEYS[survey][0] + "/" + rel
        last: Exception | None = None
        for _ in range(2):
            try:
                r = await self.aclient.get(url)
            except httpx.HTTPError as e:
                last = e
                continue
            if r.status_code == 404:
                return None
            if r.status_code >= 500:
                last = httpx.HTTPStatusError(f"{r.status_code}", request=r.request, response=r)
                continue
            r.raise_for_status()
            self._keep(path, r.content)
            return r.content
        self.failed += 1
        assert last is not None
        raise last

    def _keep(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{secrets.token_hex(4)}.part")
        tmp.write_bytes(data)
        tmp.replace(path)
        self.fetched += 1
        if self.count >= 0:
            self.count += 1

    async def close(self) -> None:
        if self.aclient is not None:
            await self.aclient.aclose()

    @staticmethod
    def relative(survey: str, order: int, tile: str) -> str:
        """Path of a tile inside a survey: Norder3/Allsky.jpg or Norder{o}/Dir{10000·(i//10000)}/Npix{i}.jpg."""
        if survey not in SURVEYS or not TILE.match(tile):
            raise ValueError("unknown survey or tile")
        deepest = SURVEYS[survey][1]
        frame = FRAME.get(survey)
        ext = "png" if frame is not None else "jpg"
        suffix = f"_{frame}" if frame else ""
        if tile == "allsky":
            if order != 3:
                raise ValueError("the Allsky image exists for order 3")
            return f"Norder3/Allsky{suffix}.{ext}"
        i = int(tile)
        if not 3 <= order <= deepest or i >= 12 * 4**order:
            raise ValueError("tile outside the survey")
        return f"Norder{order}/Dir{(i // 10000) * 10000}/Npix{i}{suffix}.{ext}"

    def get(self, survey: str, order: int, tile: str) -> bytes | None:
        rel = self.relative(survey, order, tile)
        path = self.root / survey / rel
        if path.is_file():
            return path.read_bytes()
        r = self.client.get(BASE + SURVEYS[survey][0] + "/" + rel)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        self._keep(path, r.content)
        return r.content
