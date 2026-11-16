"""Files of the store: atomic writes and hashed sub-folders (never one flat directory of many files)."""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
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


def load_npz(path: Path) -> dict[str, np.ndarray[Any, Any]] | None:
    try:
        with np.load(path, allow_pickle=False) as z:
            return {k: z[k] for k in z.files}
    except (OSError, ValueError):
        return None


def product_db_path(store_dir: Path) -> Path:
    """The product database (users, marks, tasks, squares and their human views): `SKY_PRODUCT_DB`, else `<store>/product.sqlite`.
    One SQLite file shared by every product module; each module keeps its own tables in it."""
    env = os.environ.get("SKY_PRODUCT_DB")
    return Path(env).resolve() if env else Path(store_dir) / "product.sqlite"
