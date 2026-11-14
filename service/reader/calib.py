"""The mission's per-pixel wavelength calibration (`spectral_wcs`: CWAVE and CBAND, one value per detector pixel).

IRSA documents a Level-2 file's WCS-WAVE table as an approximation for visualisation and these maps as the reference. Validation V1
(docs/validation/2026-11-14-v1-wavelength.md): our spline through the WCS-WAVE nodes agrees with CWAVE to a median of 0.0001–0.0005 µm,
but its worst pixels reach 0.01 µm (D5, a quarter of a channel). So the fields read the wavelength from CWAVE (two 16 MB planes per
detector, read once and kept on disk and in memory) and fall back to the spline only when a map cannot be read.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from engine.spx.fitsio import hdu_at
from service.reader.s3 import S3
from service.storage import load_npz, save_npz

CALIBRATION = "dr1/spectral_wcs/cal-swcs-v5-2026-191/{d}/spectral_wcs_D{d}_spx_cal-swcs-v5-2026-191.fits"
NAME = "cal-swcs-v5-2026-191"


@dataclass
class WavelengthMaps:
    s3: S3
    store: Path
    _maps: dict[int, tuple[NDArray[np.float32], NDArray[np.float32]]] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def _path(self, det: int) -> Path:
        return self.store / "raw" / "calib" / f"{NAME}-D{det}.npz"

    async def get(self, det: int) -> tuple[NDArray[np.float32], NDArray[np.float32]]:
        """(CWAVE, CBAND) of a detector, µm, indexed [y, x] in detector pixels."""
        if det in self._maps:
            return self._maps[det]
        async with self._lock:
            if det in self._maps:
                return self._maps[det]
            z = load_npz(self._path(det))
            if z is not None:
                m = (z["cwave"], z["cband"])
            else:
                m = await self._read(det)
                save_npz(self._path(det), cwave=m[0], cband=m[1])
            self._maps[det] = m
            return m

    async def _read(self, det: int) -> tuple[NDArray[np.float32], NDArray[np.float32]]:
        key = CALIBRATION.format(d=det)
        size = await self.s3.size(key)
        out: dict[str, NDArray[np.float32]] = {}
        off, i = 0, 0
        while off < size and len(out) < 2:
            head = await self.s3.get_range(key, off, min(size, off + 40000))
            h = hdu_at(i, off, head)
            if h.name in ("CWAVE", "CBAND"):
                n1, n2 = int(h.header["NAXIS1"]), int(h.header["NAXIS2"])
                data = await self.s3.get_range(key, h.data_offset, h.data_offset + h.data_size)
                out[h.name] = np.frombuffer(data, ">f4").reshape(n2, n1).astype(np.float32)
            off, i = h.next_offset, i + 1
        return out["CWAVE"], out["CBAND"]
