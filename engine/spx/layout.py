"""Where everything is in one Level-2 file, from its header chain.

QR2 (v20–v25): IMAGE, FLAGS (plain int32), VARIANCE, ZODI, PSF (image cube), WCS-WAVE.
QR3 and DR1 (v27): IMAGE, FLAGS (RICE-compressed binary table, one tile per row), VARIANCE, ZODI, EPSF (binary table), WCS-WAVE.
Header lengths differ between files, so every offset comes from walking the chain, never from a constant.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from astropy.io import fits

from engine.spx import flags as spxflags
from engine.spx.fitsio import HDU


@dataclass(frozen=True)
class PlainImage:
    data_offset: int
    nx: int
    ny: int
    bitpix: int  # -32 float32, 32 int32

    @property
    def row_bytes(self) -> int:
        return self.nx * abs(self.bitpix) // 8

    def rows_range(self, y0: int, y1: int) -> tuple[int, int]:
        """Byte range [a, b) of rows y0..y1-1."""
        return self.data_offset + y0 * self.row_bytes, self.data_offset + y1 * self.row_bytes


@dataclass(frozen=True)
class RiceImage:
    """A tile-compressed image (FITS 4.0 section 10) stored as a binary table of variable-length byte arrays."""

    table_offset: int
    heap_offset: int
    nx: int
    ny: int
    tile_rows: int  # rows per tile (ZTILE2); ZTILE1 must equal nx
    blocksize: int
    bytepix: int
    descriptor_bytes: int  # 8 for 'P' (two int32), 16 for 'Q' (two int64)

    def tiles_of_rows(self, y0: int, y1: int) -> tuple[int, int]:
        return y0 // self.tile_rows, (y1 - 1) // self.tile_rows + 1

    def descriptor_range(self, t0: int, t1: int) -> tuple[int, int]:
        return self.table_offset + t0 * self.descriptor_bytes, self.table_offset + t1 * self.descriptor_bytes


@dataclass(frozen=True)
class TableHDU:
    data_offset: int
    data_size: int
    header_text: str

    @property
    def header(self) -> fits.Header:
        """Parsed anew on every call (0.1 ms): the kept layouts hold only the text."""
        return fits.Header.fromstring(self.header_text)


@dataclass(frozen=True)
class Layout:
    """Kept by the thousand (service.reader.files): it holds the headers as text and the few numbers read from them every time, never
    astropy Header objects (1,100 objects a file, which the garbage collector walked on every full collection)."""

    release_layout: Literal["qr2", "v27"]
    image: PlainImage
    flags: PlainImage | RiceImage
    variance: PlainImage | None
    zodi: PlainImage | None
    psf: TableHDU | None  # QR2: the PSF image cube; v27: the EPSF table
    psf_kind: Literal["cube", "epsf"] | None
    wave: TableHDU | None
    image_text: str
    flags_text: str
    file_size: int
    spacecraft: tuple[float, float, float] | None  # X_SC, Y_SC, Z_SC (km from the Earth's centre); None when one is missing or not a number
    # read when the layout is made; None when that failed: the property then reads the header and fails as before, for its caller only
    detector_read: int | None
    mjd_read: float | None
    flag_bits_read: tuple[dict[str, int], str, dict[str, tuple[int, int]]] | None

    @property
    def detector(self) -> int:
        return self.detector_read if self.detector_read is not None else int(self.image_header["DETECTOR"])

    @property
    def mjd(self) -> float:
        return self.mjd_read if self.mjd_read is not None else float(self.image_header["MJD-AVG"])

    @property
    def flag_bits(self) -> tuple[dict[str, int], str, dict[str, tuple[int, int]]]:
        """engine.spx.flags.resolve of the FLAGS header: (bit of every flag name, "file" | "builtin", mismatches)."""
        return self.flag_bits_read if self.flag_bits_read is not None else spxflags.resolve(self.flags_header)

    @property
    def image_header(self) -> fits.Header:
        """Parsed anew on every call (0.3 ms): read it once where it is needed, not in a loop on the event loop."""
        return fits.Header.fromstring(self.image_text)

    @property
    def flags_header(self) -> fits.Header:
        return fits.Header.fromstring(self.flags_text)


def _spacecraft(h: fits.Header) -> tuple[float, float, float] | None:
    try:
        return float(h["X_SC"]), float(h["Y_SC"]), float(h["Z_SC"])
    except (KeyError, ValueError, TypeError):
        return None


def _plain(hdu: HDU) -> PlainImage:
    h = hdu.header
    if int(h.get("NAXIS", 0)) != 2:
        raise ValueError(f"{hdu.name}: expected a 2-D image")
    return PlainImage(hdu.data_offset, int(h["NAXIS1"]), int(h["NAXIS2"]), int(h["BITPIX"]))


def _rice(hdu: HDU) -> RiceImage:
    h = hdu.header
    if str(h.get("ZCMPTYPE", "")).strip() != "RICE_1":
        raise ValueError(f"{hdu.name}: compression {h.get('ZCMPTYPE')!r} is not supported")
    nx, ny = int(h["ZNAXIS1"]), int(h["ZNAXIS2"])
    if int(h.get("ZTILE1", nx)) != nx:
        raise ValueError(f"{hdu.name}: tiles must span whole rows")
    params = {str(h[f"ZNAME{i}"]).strip().upper(): int(h[f"ZVAL{i}"]) for i in range(1, 10) if f"ZNAME{i}" in h}
    form = str(h["TFORM1"]).strip().upper()
    descriptor_bytes = 16 if "Q" in form.split("(")[0] else 8
    theap = int(h.get("THEAP", int(h["NAXIS1"]) * int(h["NAXIS2"])))
    return RiceImage(
        table_offset=hdu.data_offset,
        heap_offset=hdu.data_offset + theap,
        nx=nx,
        ny=ny,
        tile_rows=int(h.get("ZTILE2", 1)),
        blocksize=params.get("BLOCKSIZE", 32),
        bytepix=params.get("BYTEPIX", 4),
        descriptor_bytes=descriptor_bytes,
    )


def layout_from_hdus(hdus: list[HDU], file_size: int) -> Layout:
    by = {h.name: h for h in hdus}
    for need in ("IMAGE", "FLAGS"):
        if need not in by:
            raise ValueError(f"no {need} extension")
    flags_hdu, image = by["FLAGS"], by["IMAGE"]
    compressed = bool(flags_hdu.header.get("ZIMAGE", False))
    psf_hdu, psf_kind = (by["PSF"], "cube") if "PSF" in by else ((by["EPSF"], "epsf") if "EPSF" in by else (None, None))
    return Layout(
        release_layout="v27" if compressed else "qr2",
        image=_plain(image),
        flags=_rice(flags_hdu) if compressed else _plain(flags_hdu),
        variance=_plain(by["VARIANCE"]) if "VARIANCE" in by else None,
        zodi=_plain(by["ZODI"]) if "ZODI" in by else None,
        psf=TableHDU(psf_hdu.data_offset, psf_hdu.data_size, _text(psf_hdu)) if psf_hdu else None,
        psf_kind=psf_kind,  # type: ignore[arg-type]
        wave=TableHDU(by["WCS-WAVE"].data_offset, by["WCS-WAVE"].data_size, _text(by["WCS-WAVE"])) if "WCS-WAVE" in by else None,
        image_text=_text(image),
        flags_text=_text(flags_hdu),
        file_size=file_size,
        spacecraft=_spacecraft(image.header),
        detector_read=_or_none(lambda: int(image.header["DETECTOR"])),
        mjd_read=_or_none(lambda: float(image.header["MJD-AVG"])),
        flag_bits_read=_or_none(lambda: spxflags.resolve(flags_hdu.header)),
    )


def _or_none[T](read: Callable[[], T]) -> T | None:
    try:
        return read()
    except Exception:
        return None


def _text(hdu: HDU) -> str:
    """The header as read; an HDU made by hand (tests) has only the parsed header."""
    return hdu.text or hdu.header.tostring()
