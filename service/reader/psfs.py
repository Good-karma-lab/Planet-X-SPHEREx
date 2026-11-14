"""One PSF set per (detector, file layout), shared by every file of it.

The PSF data of two files of one detector and release are byte-identical (validation V2, docs/validation/2026-11-14-m1-level2.md: 18
of 18 pairs, QR3 and DR1 share theirs). So a PSF set is read once per (detector, layout, PSF data size), kept in memory and on disk
(store/raw/psf/<checksum>.npz), and the validation suite re-checks the identity on live files.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from engine.spx import psf as spxpsf
from service.reader.files import FileReader
from service.storage import load_npz, save_npz


@dataclass
class PsfProvider:
    files: FileReader
    store: Path
    _by_family: dict[tuple[int, str, int], spxpsf.PsfSet] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    reads: int = 0  # PSF data units actually fetched (for tests and the health page)

    def _path(self, family: tuple[int, str, int]) -> Path:
        det, kind, size = family
        return self.store / "raw" / "psf" / f"D{det}-{kind}-{size}.npz"

    async def for_file(self, key: str) -> spxpsf.PsfSet:
        lay = await self.files.layout(key)
        if lay.psf is None or lay.psf_kind is None:
            raise ValueError(f"{key}: no PSF extension")
        family = (lay.detector, lay.psf_kind, lay.psf.data_size)
        if family in self._by_family:
            return self._by_family[family]
        async with self._lock:
            if family in self._by_family:
                return self._by_family[family]
            ps = self._load(family) or await self._read(key, family)
            self._by_family[family] = ps
            return ps

    def _load(self, family: tuple[int, str, int]) -> spxpsf.PsfSet | None:
        z = load_npz(self._path(family))
        if z is None:
            return None
        return spxpsf.PsfSet(str(z["kind"]), int(z["osf"]), z["zones"], z["xc"], z["yc"], str(z["checksum"]))

    async def _read(self, key: str, family: tuple[int, str, int]) -> spxpsf.PsfSet:
        header, data = await self.files.table_bytes(key, "psf")
        self.reads += 1
        ps = spxpsf.from_cube(header, data) if family[1] == "cube" else spxpsf.from_epsf(header, data)
        save_npz(
            self._path(family),
            kind=np.str_(ps.kind),
            osf=np.int64(ps.oversampling),
            zones=ps.zones.astype(np.float32),
            xc=ps.x_centres,
            yc=ps.y_centres,
            checksum=np.str_(ps.checksum),
        )
        return ps
