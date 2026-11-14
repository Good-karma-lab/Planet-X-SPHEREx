"""A minimal PNG writer (8-bit RGB) for validation renders, with nearest-neighbour enlargement."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import numpy as np


def write_png(path: Path, rgb: np.ndarray, scale: int = 1) -> None:
    """rgb: (h, w, 3) floats in 0..1 or uint8."""
    a = rgb if rgb.dtype == np.uint8 else (np.clip(rgb, 0, 1) * 255 + 0.5).astype(np.uint8)
    if scale > 1:
        a = a.repeat(scale, axis=0).repeat(scale, axis=1)
    h, w, _ = a.shape
    raw = b"".join(b"\x00" + a[y].tobytes() for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 6))
    path.write_bytes(png + chunk(b"IEND", b""))
