from __future__ import annotations

import numpy as np
import pytest

from service.products.sxp import decode, encode


def test_round_trip_and_alignment() -> None:
    arrays = {
        "picture": np.random.default_rng(0).normal(size=(3, 5, 7)).astype(np.float16),
        "valid": np.array([[1, 0, 1]], np.uint8),
        "sat": np.arange(10, dtype=np.uint8),
        "w": np.ones((2, 2), ">f4"),  # big-endian input is written little-endian
    }
    buf = encode(arrays, {"pass": 1, "grid": {"step": 1.5375}})
    assert buf[:4] == b"SXP1" and (8 + int.from_bytes(buf[4:8], "little")) % 8 == 0
    meta, got = decode(buf)
    assert meta == {"pass": 1, "grid": {"step": 1.5375}}
    for k, a in arrays.items():
        np.testing.assert_array_equal(got[k], a)
    head_end = 8 + int.from_bytes(buf[4:8], "little")
    import json

    for e in json.loads(buf[8:head_end])["arrays"]:
        assert e["offset"] % 8 == 0


def test_float64_is_refused() -> None:
    with pytest.raises(ValueError):
        encode({"x": np.zeros(3)})
