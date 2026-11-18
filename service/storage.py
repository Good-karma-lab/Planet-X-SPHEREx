"""Files of the store: atomic writes and hashed sub-folders (never one flat directory of many files)."""

from __future__ import annotations

import hashlib
import io
import os
import struct
import tempfile
import zipfile
import zlib
from pathlib import Path
from typing import Any

import numpy as np


def digest(*parts: object) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(repr(p).encode())
        h.update(b"\x1f")
    return h.hexdigest()


def sharded(root: Path, key: str, suffix: str) -> Path:
    """root/ab/cd/<key><suffix> for a hex key."""
    return root / key[:2] / key[2:4] / f"{key}{suffix}"


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save_npz(path: Path, **arrays: Any) -> None:
    buf = io.BytesIO()
    np.savez_compressed(buf, **arrays)
    atomic_write_bytes(path, buf.getvalue())


def _npy(data: bytes | bytearray) -> np.ndarray[Any, Any]:
    """One .npy member: its header parsed, the array a view of the bytes after it (no pickled objects)."""
    f = io.BytesIO(data[: 65536 + 16])
    major, _minor = np.lib.format.read_magic(f)
    if major == 1:
        shape, fortran, dtype = np.lib.format.read_array_header_1_0(f)
    elif major in (2, 3):
        shape, fortran, dtype = np.lib.format.read_array_header_2_0(f)
    else:
        raise ValueError(f"npy version {major}")
    if dtype.hasobject:
        raise ValueError("an object array (pickled) is not read")
    count = int(np.prod(shape)) if shape else 1
    arr = np.frombuffer(data, dtype=dtype, count=count, offset=f.tell())
    return arr.reshape(shape, order="F" if fortran else "C")


def load_npz(path: Path) -> dict[str, np.ndarray[Any, Any]] | None:
    """The arrays of a .npz file, or None when it is missing or broken. Read whole in one call and each member inflated by one
    zlib.decompress over its raw deflate bytes: both let go of the GIL while they work, where np.load inflates through zipfile in
    small pieces of Python and held the API's event loop for a second at a time (the row cache reads these from a thread)."""
    try:
        with open(path, "rb") as fh:
            blob = fh.read()
        out: dict[str, np.ndarray[Any, Any]] = {}
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            for info in zf.infolist():
                off = info.header_offset
                if blob[off : off + 4] != b"PK\x03\x04":
                    raise ValueError(f"{path.name}: no local header for {info.filename}")
                n, m = struct.unpack("<HH", blob[off + 26 : off + 30])
                start = off + 30 + n + m
                raw = blob[start : start + info.compress_size]
                if info.compress_type == zipfile.ZIP_DEFLATED:
                    data: bytes | bytearray = bytearray(zlib.decompress(raw, -15, max(info.file_size, 1)))
                elif info.compress_type == zipfile.ZIP_STORED:
                    data = bytearray(raw)
                else:
                    raise ValueError(f"{path.name}: compression {info.compress_type}")
                if len(data) != info.file_size or zlib.crc32(data) != info.CRC:
                    raise ValueError(f"{path.name}: {info.filename} is damaged")
                name = info.filename[:-4] if info.filename.endswith(".npy") else info.filename
                out[name] = _npy(data)
        return out
    except (OSError, ValueError, zipfile.BadZipFile, zlib.error, struct.error, EOFError):
        return None


def product_db_path(store_dir: Path) -> Path:
    """The product database (users, marks, tasks, squares and their human views): `SKY_PRODUCT_DB`, else `<store>/product.sqlite`.
    One SQLite file shared by every product module; each module keeps its own tables in it."""
    env = os.environ.get("SKY_PRODUCT_DB")
    return Path(env).resolve() if env else Path(store_dir) / "product.sqlite"
