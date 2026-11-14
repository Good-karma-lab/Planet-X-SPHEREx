"""A quick-look spectrum of any place from the SPHEREx DR1 all-sky cube (HiPS tiles of HEALPix order 5).

A tile `Npix{k}_cube.fits` holds three planes-by-planes cubes of 512 x 512 x 102 (IMAGE float32 in MJy/sr, NHITS int32, FLAGS int32), one
plane after another, then the table of the 102 spectral channels. The pixel under a position is found from the tile's HPX WCS; the
102 values of that pixel are 102 four-byte reads at offsets computed from the header, so a spectrum costs a few hundred bytes of the
320 MB file. The layout of the first tile read is kept: another tile is checked against it (its dimensions, the length of its FLAGS
header) and read by the same offsets.
"""

from __future__ import annotations

import asyncio
import math
import struct
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from numpy.typing import NDArray

from engine.spx.fitsio import BLOCK, HDU, blocks, hdu_at, header_length, parse_header
from service.coverage import ang2pix_nested

ORDER = 5
TILE_PIXEL_ORDER = 14  # a tile pixel is one HEALPix pixel of order 5 + 9
PIXEL_SR = 4.0 * math.pi / (12.0 * 4.0**TILE_PIXEL_ORDER)  # equal-area projection: every pixel has this solid angle (12.9″ across)
HEADER_READ = 4 * BLOCK


class RangeReader(Protocol):
    async def size(self, key: str) -> int: ...

    async def get_range(self, key: str, start: int, end: int) -> bytes: ...


class CubeError(RuntimeError):
    pass


def tile_key(root: str, pix: int) -> str:
    """The object key of the order-5 pixel `pix` below the HiPS root (`Dir` counts in tens of thousands)."""
    return f"{root.rstrip('/')}/Norder{ORDER}/Dir{pix // 10000 * 10000}/Npix{pix}_cube.fits"


def tile_of(ra: float, dec: float) -> int:
    return ang2pix_nested(ORDER, ra, dec)


@dataclass(frozen=True)
class Layout:
    """Where the three cubes and the channel table are in a tile, and how big they are."""

    nx: int
    ny: int
    nchan: int
    image: int  # file offsets of the first data byte of each cube
    flags: int
    channels: int
    flags_header_length: int
    flags_header_offset: int
    bitpix_image: int = -32
    mask_names: dict[int, str] = field(default_factory=dict)  # bit value -> name, from the FLAGS header

    @property
    def plane_bytes(self) -> int:
        return self.nx * self.ny * 4


@dataclass(frozen=True)
class Channels:
    detector: NDArray[np.int16]
    wavelength: NDArray[np.float32]
    wl_min: NDArray[np.float32]
    wl_max: NDArray[np.float32]
    resolving: NDArray[np.float32]
    bandwidth: NDArray[np.float32]


_CHANNEL_DTYPE = np.dtype(
    [("det", ">i2"), ("sub", ">i2"), ("wl", ">f4"), ("lo", ">f4"), ("hi", ">f4"), ("r", ">f4"), ("rs", ">f4"), ("bw", ">f4")]
)


def parse_channels(data: bytes, nchan: int) -> Channels:
    a = np.frombuffer(data, dtype=_CHANNEL_DTYPE, count=nchan)
    return Channels(
        a["det"].astype(np.int16),
        a["wl"].astype(np.float32),
        a["lo"].astype(np.float32),
        a["hi"].astype(np.float32),
        a["r"].astype(np.float32),
        a["bw"].astype(np.float32),
    )


def _mask_names(h: fits.Header) -> dict[int, str]:
    out: dict[int, str] = {}
    for k, v in h.items():
        if str(k).startswith("MSKN"):
            m = h.get("MSKM" + str(k)[4:])
            if m is not None:
                out[int(m)] = str(v)
    return out


async def _read_hdu(reader: RangeReader, key: str, index: int, offset: int, file_size: int) -> HDU:
    n = HEADER_READ
    while True:
        buf = await reader.get_range(key, offset, min(offset + n, file_size))
        if header_length(buf) is not None:
            return hdu_at(index, offset, buf)
        n *= 2
        if n > 64 * BLOCK or offset + n // 2 >= file_size:
            raise CubeError(f"{key}: no END card in the header at {offset}")


async def read_layout(reader: RangeReader, key: str) -> tuple[Layout, fits.Header, Channels]:
    """Walk the header chain of a tile (six short reads); returns the layout, the IMAGE header and the channel table."""
    off = 0
    hdus: dict[str, HDU] = {}
    file_size = await reader.size(key)
    for i in range(6):
        h = await _read_hdu(reader, key, i, off, file_size)
        hdus[h.name] = h
        off = h.next_offset
    try:
        image, flags, chan = hdus["IMAGE"], hdus["FLAGS"], hdus["SPECTRAL_CHANNELS"]
    except KeyError as e:
        raise CubeError(f"{key}: no {e.args[0]} extension") from e
    nx, ny, nchan = (int(image.header[f"NAXIS{i}"]) for i in (1, 2, 3))
    layout = Layout(
        nx=nx,
        ny=ny,
        nchan=nchan,
        image=image.data_offset,
        flags=flags.data_offset,
        channels=chan.data_offset,
        flags_header_length=flags.data_offset - flags.header_offset,
        flags_header_offset=flags.header_offset,
        mask_names=_mask_names(flags.header),
    )
    table = await reader.get_range(key, chan.data_offset, chan.data_offset + nchan * 28)
    return layout, image.header, parse_channels(table, nchan)


async def check_layout(reader: RangeReader, key: str, layout: Layout) -> fits.Header:
    """The IMAGE header of another tile; raises CubeError unless the tile has the layout already known."""
    head, flags_head = await asyncio.gather(
        reader.get_range(key, 0, blocks(1) + HEADER_READ),
        reader.get_range(key, layout.flags_header_offset, layout.flags_header_offset + layout.flags_header_length),
    )
    h0 = hdu_at(0, 0, head)
    h1 = hdu_at(1, h0.next_offset, head[h0.next_offset :])
    dims = tuple(int(h1.header[f"NAXIS{i}"]) for i in (1, 2, 3))
    if dims != (layout.nx, layout.ny, layout.nchan) or h1.data_offset != layout.image:
        raise CubeError(f"{key}: the tile has another layout than the first one read")
    if header_length(flags_head) != layout.flags_header_length or parse_header(flags_head).get("EXTNAME") != "FLAGS":
        raise CubeError(f"{key}: the FLAGS header is not where the first tile had it")
    return h1.header


def celestial_wcs(image_header: fits.Header) -> WCS:
    """The sky part of a tile's WCS: the cards of axes 1 and 2 only (the third axis is a WAVE-TAB lookup that needs the whole file)."""
    h = fits.Header()
    h["NAXIS"] = 2
    h["WCSAXES"] = 2
    for k, v in image_header.items():
        name = str(k)
        if name in ("NAXIS", "WCSAXES", "COMMENT", "HISTORY", "") or name.startswith("NAXIS"):
            continue
        if name.startswith(("CRPIX", "CRVAL", "CDELT", "CTYPE", "CUNIT", "PV2_", "PC")) or name in (
            "LONPOLE",
            "LATPOLE",
            "RADESYS",
            "MJDREF",
        ):
            if name.endswith("3") or name.startswith(("PC3", "PC1_3", "PC2_3", "PC3_", "PV3", "CNAME3")):
                continue
            h[name] = v
    return WCS(h)


def pixel_of(image_header: fits.Header, ra: float, dec: float, nx: int, ny: int) -> tuple[int, int] | None:
    """The (x, y) pixel of a tile (0-based) under a position, or None when the position is outside the tile's square."""
    w = celestial_wcs(image_header)
    x, y = w.all_world2pix([ra], [dec], 0)
    xi, yi = math.floor(float(x[0]) + 0.5), math.floor(float(y[0]) + 0.5)
    if not (0 <= xi < nx and 0 <= yi < ny):
        return None
    return xi, yi


@dataclass(frozen=True)
class CubeSpectrum:
    tile: int
    x: int
    y: int
    lam: list[float]
    bandwidth: list[float]
    detector: list[int]
    sb: list[float | None]  # surface brightness, MJy/sr; None where the channel is empty (NaN)
    flags: list[int | None]  # the FLAGS word of the channels read; None for those not asked for
    flag_names: dict[int, str]

    def ok(self) -> list[bool]:
        return [v is not None for v in self.sb]


async def read_spectrum(
    reader: RangeReader,
    key: str,
    layout: Layout,
    channels: Channels,
    x: int,
    y: int,
    tile: int,
    flags: str = "bad",
    concurrency: int = 32,
) -> CubeSpectrum:
    """The values of the pixel (x, y): one four-byte read per channel of the IMAGE cube, then of the FLAGS cube for the channels asked for.

    `flags` is "bad" (the channels whose value is empty: what explains a gap), "all" or "none".
    """
    if flags not in ("bad", "all", "none"):
        raise ValueError("flags must be bad, all or none")
    sem = asyncio.Semaphore(concurrency)
    pix = (y * layout.nx + x) * 4
    n = layout.nchan

    async def one(base: int, plane: int) -> bytes:
        async with sem:
            a = base + plane * layout.plane_bytes + pix
            return await reader.get_range(key, a, a + 4)

    sb_raw = struct.unpack(f">{n}f", b"".join(await asyncio.gather(*[one(layout.image, k) for k in range(n)])))
    sb = [None if not math.isfinite(v) else float(v) for v in sb_raw]
    want = [k for k in range(n) if flags == "all" or (flags == "bad" and sb[k] is None)]
    fl: list[int | None] = [None] * n
    for k, raw in zip(want, await asyncio.gather(*[one(layout.flags, k) for k in want]), strict=True):
        fl[k] = int(struct.unpack(">i", raw)[0])
    return CubeSpectrum(
        tile=tile,
        x=x,
        y=y,
        lam=[round(float(v), 4) for v in channels.wavelength],
        bandwidth=[round(float(v), 4) for v in channels.bandwidth],
        detector=[int(v) for v in channels.detector],
        sb=sb,
        flags=fl,
        flag_names=layout.mask_names,
    )


@dataclass
class CubeSource:
    """Keeps the layout and the channel table of the first tile read and serves spectra of any place."""

    reader: RangeReader
    root: str
    _known: tuple[Layout, Channels] | None = field(default=None, repr=False)
    _headers: dict[int, fits.Header] = field(default_factory=dict, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def _header(self, key: str, tile: int) -> tuple[Layout, Channels, fits.Header]:
        async with self._lock:
            if self._known is None:
                layout, header, channels = await read_layout(self.reader, key)
                self._known = (layout, channels)
                self._headers[tile] = header
            layout, channels = self._known
            if tile not in self._headers:
                self._headers[tile] = await check_layout(self.reader, key, layout)
                if len(self._headers) > 400:
                    self._headers.pop(next(iter(self._headers)))
            return layout, channels, self._headers[tile]

    async def spectrum(self, ra: float, dec: float, flags: str = "bad") -> CubeSpectrum | None:
        tile = tile_of(ra, dec)
        key = tile_key(self.root, tile)
        layout, channels, header = await self._header(key, tile)
        xy = pixel_of(header, ra, dec, layout.nx, layout.ny)
        if xy is None:
            return None
        return await read_spectrum(self.reader, key, layout, channels, xy[0], xy[1], tile, flags)
