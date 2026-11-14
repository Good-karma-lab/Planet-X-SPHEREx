"""SXP1: one binary container for every array part (docs/plan/05-architecture.md, 5.7).

    bytes 0..3   b"SXP1"
    bytes 4..7   u32 little-endian: length H of the JSON header in bytes (the header is padded with spaces so that 8 + H is a multiple of 8)
    8 .. 8+H     JSON: {"meta": {...}, "arrays": [{"name", "dtype", "shape", "offset", "nbytes"}]}; offsets count from the end of the header
    then         the arrays, little-endian, each starting at a multiple of 8 bytes

dtypes: "f2" float16, "f4" float32, "u1" uint8, "u2" uint16, "i2" int16, "i4" int32. Image arrays are row-major with row 0 = NORTH and
column 0 = EAST (as displayed: east left), the one orientation of the whole system.
"""

from __future__ import annotations

import json
import struct
from collections.abc import Mapping
from typing import Any

import numpy as np
from numpy.typing import NDArray

MAGIC = b"SXP1"
DTYPES = {"f2": "<f2", "f4": "<f4", "u1": "u1", "u2": "<u2", "i2": "<i2", "i4": "<i4"}
_REVERSE = {np.dtype(v).str.lstrip("<|"): k for k, v in DTYPES.items()}


def _code(a: NDArray[Any]) -> str:
    key = a.dtype.newbyteorder("<").str.lstrip("<|") if a.dtype.byteorder not in "|" else a.dtype.str.lstrip("|")
    if key not in _REVERSE:
        raise ValueError(f"dtype {a.dtype} is not allowed in SXP1")
    return _REVERSE[key]


def encode(arrays: Mapping[str, NDArray[Any]], meta: Mapping[str, Any] | None = None) -> bytes:
    entries = []
    body = bytearray()
    for name, a in arrays.items():
        code = _code(np.asarray(a))
        data = np.ascontiguousarray(a, dtype=DTYPES[code]).tobytes()
        body += b"\0" * ((-len(body)) % 8)
        entries.append({"name": name, "dtype": code, "shape": list(np.shape(a)), "offset": len(body), "nbytes": len(data)})
        body += data
    header = json.dumps({"meta": dict(meta or {}), "arrays": entries}, separators=(",", ":"), ensure_ascii=False).encode()
    header += b" " * ((-(8 + len(header))) % 8)
    return MAGIC + struct.pack("<I", len(header)) + header + bytes(body)


def decode(buf: bytes) -> tuple[dict[str, Any], dict[str, NDArray[Any]]]:
    if buf[:4] != MAGIC:
        raise ValueError("not an SXP1 part")
    (h,) = struct.unpack("<I", buf[4:8])
    head = json.loads(buf[8 : 8 + h])
    base = 8 + h
    out = {}
    for e in head["arrays"]:
        a = np.frombuffer(buf, dtype=DTYPES[e["dtype"]], count=int(np.prod(e["shape"])) if e["shape"] else 1, offset=base + e["offset"])
        out[e["name"]] = a.reshape(e["shape"])
    return head["meta"], out
