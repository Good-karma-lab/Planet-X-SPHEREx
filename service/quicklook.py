"""A quick-look spectrum of a place from the SPHEREx DR1 all-sky spectral cube, read by byte ranges.

The cube is published as HiPS tiles of order 5 in the public bucket (dr1/hips5_cube/<release>/Norder5/DirD/NpixN_cube.fits, 320 MB
each): an IMAGE extension of 102 planes of 512 × 512 float32 pixels (MJy/sr) on the HEALPix grid, a WCS-WAVE table with the
wavelength of every plane, and further extensions of the same shape. One pixel's spectrum is 102 reads of 4 bytes at offsets
computed from the header; the headers of a tile are read once and kept. Nothing of the 320 MB file is fetched beyond that.

This is the all-sky map's own value at the place (a mosaic of the survey, about 6″ pixels): a quick look shown while the
spectrum measured from the individual exposures is still being built, never instead of it.
"""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from service.reader.s3 import S3

RELEASE = "dr1/hips5_cube/hips_20261026"
ORDER = 5
TILE = 512
BLOCK = 2880


def ang2pix_nest(order: int, ra: float, dec: float) -> int:
    """HEALPix nested pixel of a sky position (Górski et al. 2005)."""
    nside = 1 << order
    z = math.sin(math.radians(dec))
    za = abs(z)
    tt = ((ra % 360.0) / 90.0) % 4.0
    if za <= 2.0 / 3.0:
        t1 = nside * (0.5 + tt)
        t2 = nside * z * 0.75
        jp = math.floor(t1 - t2)
        jm = math.floor(t1 + t2)
        ifp, ifm = jp // nside, jm // nside
        face = (ifp % 4) + 4 if ifp == ifm else (ifp % 4 if ifp < ifm else (ifm % 4) + 8)
        ix, iy = jm % nside, nside - (jp % nside) - 1
    else:
        ntt = min(math.floor(tt), 3)
        tp = tt - ntt
        tmp = nside * math.sqrt(3.0 * (1.0 - za))
        jp = min(math.floor(tp * tmp), nside - 1)
        jm = min(math.floor((1.0 - tp) * tmp), nside - 1)
        if z >= 0:
            face, ix, iy = ntt, nside - jm - 1, nside - jp - 1
        else:
            face, ix, iy = ntt + 8, jp, jm

    def spread(v: int) -> int:
        r = 0
        for b in range(order):
            r |= ((v >> b) & 1) << (2 * b)
        return r

    return face * (nside * nside) + spread(ix) + 2 * spread(iy)


@dataclass
class TileLayout:
    key: str
    wcs_header: str
    data_offset: int
    planes: int
    extra: list[tuple[str, int, str]]  # (EXTNAME, data offset, BUNIT) of further cubes of the same shape
    lam: list[float]


def _header_end(buf: bytes) -> int | None:
    for i in range(0, len(buf) - 79, 80):
        if buf[i : i + 3] == b"END" and buf[i + 3 : i + 80].strip() == b"":
            return (i // BLOCK + 1) * BLOCK
    return None


class QuickLook:
    def __init__(self, s3: S3, store_dir: Path) -> None:
        self.s3 = s3
        self.dir = store_dir / "quicklook"
        self.layouts: dict[int, TileLayout] = {}
        self.locks: dict[int, asyncio.Lock] = {}

    @staticmethod
    def key_of(npix: int) -> str:
        return f"{RELEASE}/Norder{ORDER}/Dir{(npix // 10000) * 10000}/Npix{npix}_cube.fits"

    async def _header_at(self, key: str, offset: int) -> tuple[fits.Header, int]:
        size = 4 * BLOCK
        while True:
            buf = await self.s3.get_range(key, offset, offset + size)
            end = _header_end(buf)
            if end is not None:
                return fits.Header.fromstring(buf[:end].decode("ascii", "replace")), offset + end
            size *= 2

    async def layout(self, npix: int) -> TileLayout:
        if npix in self.layouts:
            return self.layouts[npix]
        lock = self.locks.setdefault(npix, asyncio.Lock())
        async with lock:
            if npix in self.layouts:
                return self.layouts[npix]
            path = self.dir / "layouts" / f"{npix}.json"
            if path.is_file():
                d = json.loads(path.read_text())
                lay = TileLayout(d["key"], d["wcs_header"], d["data_offset"], d["planes"], [tuple(x) for x in d["extra"]], d["lam"])
                self.layouts[npix] = lay
                return lay
            key = self.key_of(npix)
            _, off = await self._header_at(key, 0)  # primary (no data)
            img, data0 = await self._header_at(key, off)
            planes = int(img["NAXIS3"])
            nbytes = TILE * TILE * planes * abs(int(img["BITPIX"])) // 8
            pos = data0 + ((nbytes + BLOCK - 1) // BLOCK) * BLOCK
            extra: list[tuple[str, int, str]] = []
            lam: list[float] = []
            for _ in range(6):
                try:
                    h, d0 = await self._header_at(key, pos)
                except Exception:  # no further extension
                    break
                name = str(h.get("EXTNAME", ""))
                if h.get("XTENSION") == "BINTABLE" and name in ("WCS-WAVE", "SPECTRAL_CHANNELS"):
                    tb = await self.s3.get_range(key, d0, d0 + int(h["NAXIS1"]) * int(h["NAXIS2"]))
                    lam = _wavelengths(h, tb, planes)
                    size = int(h["NAXIS1"]) * int(h["NAXIS2"]) + int(h.get("PCOUNT", 0))
                elif int(h.get("NAXIS", 0)) == 3 and int(h["NAXIS1"]) == TILE and int(h["NAXIS3"]) == planes:
                    extra.append((name, d0, str(h.get("BUNIT", ""))))
                    size = TILE * TILE * planes * abs(int(h["BITPIX"])) // 8
                else:
                    dims = [int(h.get(f"NAXIS{i}", 1)) for i in range(1, int(h.get("NAXIS", 0)) + 1)]
                    size = abs(int(h.get("BITPIX", 8))) // 8 * int(np.prod(dims))
                    size += int(h.get("PCOUNT", 0))
                pos = d0 + ((size + BLOCK - 1) // BLOCK) * BLOCK
            # the celestial part only (the spectral axis is a lookup table that needs the whole file)
            h2 = fits.Header()
            h2["NAXIS"] = 2
            h2["WCSAXES"] = 2
            for k in ("CRPIX1", "CRPIX2", "PC1_1", "PC1_2", "PC2_1", "PC2_2", "CDELT1", "CDELT2", "CUNIT1", "CUNIT2", "CTYPE1", "CTYPE2"):
                h2[k] = img[k]
            for k in ("CRVAL1", "CRVAL2", "PV2_1", "PV2_2", "LONPOLE", "LATPOLE", "RADESYS"):
                if k in img:
                    h2[k] = img[k]
            wcs_h = h2.tostring(sep="\n")
            lay = TileLayout(key, wcs_h, data0, planes, extra, lam)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(lay.__dict__))
            self.layouts[npix] = lay
            return lay

    async def spectrum(self, ra: float, dec: float) -> dict[str, Any]:
        npix = ang2pix_nest(ORDER, ra, dec)
        lay = await self.layout(npix)
        w = WCS(fits.Header.fromstring(lay.wcs_header, sep="\n"), naxis=2)
        x, y = (float(v) for v in w.all_world2pix([[ra, dec]], 0)[0])
        xi, yi = round(x), round(y)
        if not (0 <= xi < TILE and 0 <= yi < TILE):
            raise ValueError(f"the place falls outside tile {npix} ({xi}, {yi})")

        async def plane_values(data0: int) -> list[float | None]:
            async def one(k: int) -> float | None:
                o = data0 + (k * TILE * TILE + yi * TILE + xi) * 4
                b = await self.s3.get_range(lay.key, o, o + 4)
                v = float(np.frombuffer(b, dtype=">f4")[0])
                return v if math.isfinite(v) else None

            return list(await asyncio.gather(*(one(k) for k in range(lay.planes))))

        hits = next((d0 for name, d0, _ in lay.extra if name.upper() == "NHITS"), None)
        if hits is not None:
            flux, nhits = await asyncio.gather(plane_values(lay.data_offset), plane_values(hits))
        else:
            flux, nhits = await plane_values(lay.data_offset), []
        out: dict[str, Any] = {
            "label": "quick look (all-sky cube)",
            "source": f"SPHEREx DR1 all-sky spectral cube, HiPS order {ORDER} tile {npix}, pixel ({xi}, {yi})",
            "unit": "MJy/sr",
            "lam": [round(v, 4) for v in lay.lam] if lay.lam else None,
            "flux": [None if v is None else round(v, 5) for v in flux],
        }
        out["nhits"] = [None if v is None else int(v) for v in nhits]  # exposures behind each plane's value at this pixel
        return out


def _wavelengths(h: fits.Header, data: bytes, planes: int) -> list[float]:
    """The WAVELENGTH column of the WCS-WAVE table: one wavelength per plane (in µm), read from the row layout."""
    sizes = {"L": 1, "B": 1, "I": 2, "J": 4, "K": 8, "E": 4, "D": 8, "A": 1}
    kinds = {"I": ">i2", "J": ">i4", "K": ">i8", "E": ">f4", "D": ">f8"}
    row = int(h["NAXIS1"])
    off = 0
    for i in range(1, int(h["TFIELDS"]) + 1):
        form = str(h[f"TFORM{i}"]).strip()
        rep = int(form[:-1]) if form[:-1] else 1
        code = form[-1]
        if str(h[f"TTYPE{i}"]).strip().upper() in ("WAVELENGTH", "WAVELENGTHS") and code in kinds:
            arr = np.frombuffer(data, dtype=np.uint8).reshape(-1, row)[:, off : off + sizes[code]].copy()
            return [float(v) for v in arr.view(kinds[code]).ravel()[:planes]]
        off += rep * sizes.get(code, 1)
    return []
