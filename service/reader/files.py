"""One Level-2 file: its layout (from the header chain) and its rows, through the store's caches.

Caches (docs/plan/05-architecture.md, 5.6):
- headers: store/raw/headers/ab/cd/<digest>.npz — the header bytes and offsets of every HDU, enough to rebuild the layout offline;
- row bands: store/raw/bands/ab/cd/<digest>.npz — 64 rows of one plane of one file.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np
from astropy.io import fits
from numpy.typing import NDArray

from engine.spx import rows as spxrows
from engine.spx.fitsio import HDU, hdu_at, header_length
from engine.spx.layout import Layout, PlainImage, RiceImage, layout_from_hdus
from service.reader.s3 import S3, ReadError
from service.storage import digest, load_npz, save_npz, sharded

BAND_ROWS = 64
HEADER_PROBE = 32768  # first read at each header: enough for every header seen so far (IMAGE ~26 kB, PSF 43 kB needs two)
Plane = Literal["image", "flags", "variance", "zodi"]


@dataclass
class FileReader:
    s3: S3
    store: Path
    _layouts: dict[str, Layout] = field(default_factory=dict)
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    # ---- layout -------------------------------------------------------------------------------------------------------------
    def _headers_path(self, key: str) -> Path:
        return sharded(self.store / "raw" / "headers", digest("headers-v1", key), ".npz")

    async def layout(self, key: str) -> Layout:
        if key in self._layouts:
            return self._layouts[key]
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if key in self._layouts:
                return self._layouts[key]
            lay = self._layout_from_cache(key) or await self._walk(key)
            self._layouts[key] = lay
            return lay

    def _layout_from_cache(self, key: str) -> Layout | None:
        z = load_npz(self._headers_path(key))
        if z is None:
            return None
        offsets = z["offsets"].tolist()
        hdus = []
        for i, off in enumerate(offsets):
            hdus.append(hdu_at(i, int(off), z[f"h{i}"].tobytes()))
        return layout_from_hdus(hdus, int(z["size"]))

    async def _walk(self, key: str) -> Layout:
        size = await self.s3.size(key)
        hdus: list[HDU] = []
        raw: list[bytes] = []
        off = 0
        while off < size:
            buf = await self.s3.get_range(key, off, min(size, off + HEADER_PROBE))
            while header_length(buf) is None:
                if off + len(buf) >= size:
                    raise ReadError(f"{key}: header at {off} has no END card")
                buf += await self.s3.get_range(key, off + len(buf), min(size, off + len(buf) + HEADER_PROBE))
            n = header_length(buf)
            assert n is not None
            h = hdu_at(len(hdus), off, buf)
            hdus.append(h)
            raw.append(buf[:n])
            off = h.next_offset
        save_npz(
            self._headers_path(key),
            offsets=np.array([h.header_offset for h in hdus], np.int64),
            size=np.int64(size),
            **{f"h{i}": np.frombuffer(b, np.uint8) for i, b in enumerate(raw)},
        )
        return layout_from_hdus(hdus, size)

    async def header_bytes_of(self, key: str, start: int, end: int) -> bytes:
        return await self.s3.get_range(key, start, end)

    # ---- rows ---------------------------------------------------------------------------------------------------------------
    def _band_path(self, key: str, plane: Plane, band: int) -> Path:
        return sharded(self.store / "raw" / "bands", digest("band-v1", key, plane, BAND_ROWS, band), ".npz")

    async def rows(self, key: str, plane: Plane, y0: int, y1: int) -> NDArray[np.generic]:
        """Rows y0..y1-1 of a plane, native byte order (float32 for image/variance/zodi, int32 for flags)."""
        lay = await self.layout(key)
        img = _plane(lay, plane)
        ny = img.ny
        y0, y1 = max(0, y0), min(ny, y1)
        if y1 <= y0:
            raise ValueError(f"rows {y0}..{y1} outside 0..{ny}")
        b0, b1 = y0 // BAND_ROWS, (y1 - 1) // BAND_ROWS + 1
        bands: dict[int, NDArray[np.generic]] = {}
        missing = []
        for b in range(b0, b1):
            z = load_npz(self._band_path(key, plane, b))
            if z is not None:
                bands[b] = z["rows"]
            else:
                missing.append(b)
        for run in _runs(missing):
            got = await self._fetch_bands(key, img, run[0], run[-1] + 1)
            for b, arr in got.items():
                save_npz(self._band_path(key, plane, b), rows=arr)
                bands[b] = arr
        block = np.concatenate([bands[b] for b in range(b0, b1)], axis=0)
        return block[y0 - b0 * BAND_ROWS : y1 - b0 * BAND_ROWS]

    async def _fetch_bands(self, key: str, img: PlainImage | RiceImage, b0: int, b1: int) -> dict[int, NDArray[np.generic]]:
        y0, y1 = b0 * BAND_ROWS, min(img.ny, b1 * BAND_ROWS)
        if isinstance(img, PlainImage):
            a, b = img.rows_range(y0, y1)
            arr: NDArray[np.generic] = spxrows.plain_rows(img, await self.s3.get_range(key, a, b), y1 - y0)
        else:
            t0, t1 = img.tiles_of_rows(y0, y1)
            da, db = img.descriptor_range(t0, t1)
            desc = spxrows.descriptors(img, await self.s3.get_range(key, da, db))
            ha, hb = spxrows.heap_span(desc)
            heap = await self.s3.get_range(key, img.heap_offset + ha, img.heap_offset + hb)
            arr = spxrows.rice_rows(img, desc, heap, ha, t0)[: y1 - y0]
        return {b: arr[(b - b0) * BAND_ROWS : (b - b0 + 1) * BAND_ROWS] for b in range(b0, b1)}

    async def table_bytes(self, key: str, which: Literal["psf", "wave"]) -> tuple[fits.Header, bytes]:
        lay = await self.layout(key)
        t = lay.psf if which == "psf" else lay.wave
        if t is None:
            raise ReadError(f"{key}: no {which} extension")
        return t.header, await self.s3.get_range(key, t.data_offset, t.data_offset + t.data_size)


def _plane(lay: Layout, plane: Plane) -> PlainImage | RiceImage:
    img = {"image": lay.image, "flags": lay.flags, "variance": lay.variance, "zodi": lay.zodi}[plane]
    if img is None:
        raise ReadError(f"no {plane} plane in this file")
    return img


def _runs(xs: list[int]) -> list[list[int]]:
    out: list[list[int]] = []
    for x in xs:
        if out and out[-1][-1] == x - 1:
            out[-1].append(x)
        else:
            out.append([x])
    return out
