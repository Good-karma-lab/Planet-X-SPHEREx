"""Decoding rows of the IMAGE, FLAGS, VARIANCE and ZODI planes from fetched bytes."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from engine.spx.layout import PlainImage, RiceImage

_DTYPES = {-32: ">f4", -64: ">f8", 32: ">i4", 16: ">i2", 8: "u1"}


def plain_rows(img: PlainImage, buf: bytes, n_rows: int) -> NDArray[np.generic]:
    """Rows of a plain image, in native byte order; buf holds exactly n_rows whole rows."""
    dt = np.dtype(_DTYPES[img.bitpix])
    a = np.frombuffer(buf, dtype=dt, count=n_rows * img.nx).reshape(n_rows, img.nx)
    return a.astype(dt.newbyteorder("="))


def descriptors(img: RiceImage, buf: bytes) -> NDArray[np.int64]:
    """(n_tiles, 2) array of (compressed length, offset in the heap) from the descriptor bytes of consecutive tiles."""
    dt = ">i4" if img.descriptor_bytes == 8 else ">i8"
    return np.frombuffer(buf, dtype=dt).reshape(-1, 2).astype(np.int64)


def heap_span(desc: NDArray[np.int64]) -> tuple[int, int]:
    """[a, b) relative to the heap start that covers every tile of `desc`."""
    return int(desc[:, 1].min()), int((desc[:, 1] + desc[:, 0]).max())


def rice_rows(img: RiceImage, desc: NDArray[np.int64], heap: bytes, heap_start: int, t0: int) -> NDArray[np.int32]:
    """Decode consecutive tiles t0.. whose descriptors are `desc`; `heap` holds heap bytes from relative offset `heap_start`.

    Returns the rows of those tiles, shape (n_tiles * tile_rows, nx) clipped at the image's last row.
    """
    from astropy.io.fits.hdu.compressed._codecs import Rice1  # astropy 8.x; pinned in pyproject.toml

    out = []
    for k, (n, off) in enumerate(desc):
        rows = min(img.tile_rows, img.ny - (t0 + k) * img.tile_rows)
        codec = Rice1(blocksize=img.blocksize, bytepix=img.bytepix, tilesize=rows * img.nx)
        a = int(off) - heap_start
        raw = codec.decode(np.frombuffer(heap, dtype=np.uint8, count=int(n), offset=a))
        out.append(np.frombuffer(raw, dtype=f"i{img.bytepix}").reshape(rows, img.nx).astype(np.int32))
    return np.concatenate(out, axis=0)
