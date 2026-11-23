"""One Level-2 file: its layout (from the header chain) and its rows, through the store's caches.

Caches (docs/plan/05-architecture.md, 5.6):
- headers: store/raw/headers/ab/cd/<digest>.npz — the header bytes and offsets of every HDU, enough to rebuild the layout offline;
- row bands: store/raw/bands/ab/cd/<digest>.npz — 64 rows of one plane of one file.
"""

from __future__ import annotations

import asyncio
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
from astropy.io import fits
from numpy.typing import NDArray

from engine.spx import rows as spxrows
from engine.spx import wcs as spxwcs
from engine.spx.fitsio import HDU, hdu_at, header_length
from engine.spx.layout import Layout, PlainImage, RiceImage, layout_from_hdus
from service import cpu
from service.reader.s3 import S3, ReadError
from service.storage import digest, load_npz, save_npz, sharded

BAND_ROWS = 64
# the layouts kept in memory, the latest used: 270 KB each, and without a limit every exposure ever read stayed (73,000 on prod, 20 GB).
# A square reads up to 1,100 exposures, six squares in a row 4,900; one that left is read from disk again (12 ms, in a thread)
LAYOUTS_KEEP = 6000
HEADER_PROBE = 32768  # first read at each header: enough for every header seen so far (IMAGE ~26 kB, PSF 43 kB needs two)
Plane = Literal["image", "flags", "variance", "zodi"]


@dataclass
class FileReader:
    s3: S3
    store: Path
    _layouts: OrderedDict[str, Layout] = field(default_factory=OrderedDict)
    _layouts_lock: threading.Lock = field(default_factory=threading.Lock)
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    _wcs: dict[str, Any] = field(default_factory=dict)

    def wcs_of(self, key: str, lay: Layout) -> Any:
        """The celestial WCS of a file, built once (building it from the header costs milliseconds, and searches ask for the same
        file again and again)."""
        w = self._wcs.get(key)
        if w is None:
            if len(self._wcs) > 6000:
                self._wcs.clear()
            w = self._wcs[key] = spxwcs.celestial(lay.image_header)
        return w

    async def wcs(self, key: str, lay: Layout) -> Any:
        """wcs_of, built in a thread the first time: astropy reads the header's SIP terms for up to a second, which must never hold the
        event loop (every answer, /health too, would wait)."""
        w = self._wcs.get(key)
        return w if w is not None else await asyncio.to_thread(self.wcs_of, key, lay)

    # ---- layout -------------------------------------------------------------------------------------------------------------
    def _headers_path(self, key: str) -> Path:
        return sharded(self.store / "raw" / "headers", digest("headers-v1", key), ".npz")

    def _kept(self, key: str) -> Layout | None:
        with self._layouts_lock:
            lay = self._layouts.get(key)
            if lay is not None:
                self._layouts.move_to_end(key)
            return lay

    def _keep(self, key: str, lay: Layout) -> None:
        with self._layouts_lock:
            self._layouts[key] = lay
            self._layouts.move_to_end(key)
            while len(self._layouts) > LAYOUTS_KEEP:
                self._layouts.popitem(last=False)

    async def layout(self, key: str) -> Layout:
        lay = self._kept(key)
        if lay is not None:
            return lay
        lock = self._locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                lay = self._kept(key)
                if lay is None:
                    # the cached headers are read and parsed in a thread: hundreds at once held the event loop for over a second
                    lay = await asyncio.to_thread(self._layout_from_cache, key) or await self._walk(key)
                    self._keep(key, lay)
                return lay
        finally:  # those who wait hold the lock itself; one that nobody waits for is not kept either
            if not lock.locked() and self._locks.get(key) is lock:
                del self._locks[key]

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
        """Rows y0..y1-1 of a plane, native byte order (float32 for image/variance/zodi, int32 for flags).

        Decoding and the band cache's compression run in a worker process (service.cpu); this process only fetches bytes."""
        lay = await self.layout(key)
        img = _plane(lay, plane)
        ny = img.ny
        y0, y1 = max(0, y0), min(ny, y1)
        if y1 <= y0:
            raise ValueError(f"rows {y0}..{y1} outside 0..{ny}")
        b0, b1 = y0 // BAND_ROWS, (y1 - 1) // BAND_ROWS + 1
        paths = {b: self._band_path(key, plane, b) for b in range(b0, b1)}
        loaded = await asyncio.to_thread(_load_bands, [paths[b] for b in range(b0, b1)])
        bands: dict[int, NDArray[np.generic]] = {b: a for b, a in zip(range(b0, b1), loaded, strict=True) if a is not None}
        missing = [b for b in range(b0, b1) if b not in bands]
        for run in _runs(missing):
            bands.update(await self._fetch_bands(key, img, run[0], run[-1] + 1, [paths[b] for b in range(run[0], run[-1] + 1)]))
        block = np.concatenate([bands[b] for b in range(b0, b1)], axis=0)
        return block[y0 - b0 * BAND_ROWS : y1 - b0 * BAND_ROWS]

    async def _fetch_bands(
        self, key: str, img: PlainImage | RiceImage, b0: int, b1: int, paths: list[Path] | None = None
    ) -> dict[int, NDArray[np.generic]]:
        y0, y1 = b0 * BAND_ROWS, min(img.ny, b1 * BAND_ROWS)
        if isinstance(img, PlainImage):
            a, b = img.rows_range(y0, y1)
            buf = await self.s3.get_range(key, a, b)
            return await cpu.run(_decode_plain, img, buf, y1 - y0, b0, b1, paths)
        t0, t1 = img.tiles_of_rows(y0, y1)
        da, db = img.descriptor_range(t0, t1)
        desc = spxrows.descriptors(img, await self.s3.get_range(key, da, db))
        ha, hb = spxrows.heap_span(desc)
        heap = await self.s3.get_range(key, img.heap_offset + ha, img.heap_offset + hb)
        return await cpu.run(_decode_rice, img, desc, heap, ha, t0, y1 - y0, b0, b1, paths)

    async def table_bytes(self, key: str, which: Literal["psf", "wave"]) -> tuple[fits.Header, bytes]:
        """The data of the PSF or WCS-WAVE extension. The small WCS-WAVE table is cached in the store; PSF sets are shared by
        service.reader.psfs instead."""
        lay = await self.layout(key)
        t = lay.psf if which == "psf" else lay.wave
        if t is None:
            raise ReadError(f"{key}: no {which} extension")
        if which == "wave":
            path = sharded(self.store / "raw" / "tables", digest("wave-v1", key), ".npz")
            z = load_npz(path)
            if z is not None:
                return t.header, z["data"].tobytes()
            data = await self.s3.get_range(key, t.data_offset, t.data_offset + t.data_size)
            save_npz(path, data=np.frombuffer(data, np.uint8))
            return t.header, data
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


# ---- compute of the row cache (module level: these run in worker processes) ---------------------------------------------------------
def _load_bands(paths: list[Path]) -> list[NDArray[np.generic] | None]:
    out: list[NDArray[np.generic] | None] = []
    for p in paths:
        z = load_npz(p)
        out.append(z["rows"] if z is not None else None)
    return out


def _split_and_keep(arr: NDArray[np.generic], b0: int, b1: int, paths: list[Path] | None) -> dict[int, NDArray[np.generic]]:
    got = {b: arr[(b - b0) * BAND_ROWS : (b - b0 + 1) * BAND_ROWS] for b in range(b0, b1)}
    if paths is not None:
        for b, p in zip(range(b0, b1), paths, strict=True):
            save_npz(p, rows=got[b])
    return got


def _decode_plain(img: PlainImage, buf: bytes, n_rows: int, b0: int, b1: int, paths: list[Path] | None) -> dict[int, NDArray[np.generic]]:
    return _split_and_keep(spxrows.plain_rows(img, buf, n_rows), b0, b1, paths)


def _decode_rice(
    img: RiceImage, desc: NDArray[np.int64], heap: bytes, ha: int, t0: int, n_rows: int, b0: int, b1: int, paths: list[Path] | None
) -> dict[int, NDArray[np.generic]]:
    return _split_and_keep(spxrows.rice_rows(img, desc, heap, ha, t0)[:n_rows], b0, b1, paths)
