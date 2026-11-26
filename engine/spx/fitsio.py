"""The FITS header chain from bytes (FITS standard 4.0: 2880-byte blocks of 80-character cards)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from astropy.io import fits

BLOCK = 2880
CARD = 80


def blocks(n_bytes: int) -> int:
    """Bytes rounded up to whole FITS blocks."""
    return math.ceil(n_bytes / BLOCK) * BLOCK


def header_length(buf: bytes) -> int | None:
    """Length in bytes (whole blocks) of the header that starts at buf[0], or None if its END card is not in buf yet."""
    for i in range(0, len(buf) - CARD + 1, CARD):
        if buf[i : i + 3] == b"END" and not buf[i + 3 : i + CARD].strip():
            return blocks(i + CARD)
    return None


def header_text(buf: bytes) -> str:
    n = header_length(buf)
    if n is None:
        raise ValueError("no END card in the bytes given")
    return buf[:n].decode("ascii", errors="replace")


def parse_header(buf: bytes) -> fits.Header:
    return fits.Header.fromstring(header_text(buf))


def data_size(h: fits.Header) -> int:
    """Size of the data unit in bytes, without the padding to whole blocks (FITS 4.0, section 4.4.1)."""
    naxis = int(h.get("NAXIS", 0))
    if naxis == 0:
        return 0
    bitpix = abs(int(h["BITPIX"]))
    n = 1
    for i in range(1, naxis + 1):
        n *= int(h[f"NAXIS{i}"])
    return (bitpix // 8) * int(h.get("GCOUNT", 1)) * (int(h.get("PCOUNT", 0)) + n)


@dataclass(frozen=True)
class HDU:
    """One header-data unit: where its header and data are in the file."""

    index: int
    name: str
    header_offset: int
    data_offset: int
    data_size: int
    header: fits.Header
    text: str = ""  # the header as read (whole blocks), for those who keep it: a string the garbage collector does not walk

    @property
    def next_offset(self) -> int:
        return self.data_offset + blocks(self.data_size)


def hdu_at(index: int, offset: int, buf: bytes) -> HDU:
    """The HDU whose header starts at file offset `offset`; `buf` holds the bytes from that offset (at least its header)."""
    text = header_text(buf)
    n = len(text)  # one character per byte (ascii, an unreadable byte replaced by one character)
    h = fits.Header.fromstring(text)
    name = str(h.get("EXTNAME", "PRIMARY" if index == 0 else f"HDU{index}")).strip().upper()
    return HDU(index=index, name=name, header_offset=offset, data_offset=offset + n, data_size=data_size(h), header=h, text=text)
