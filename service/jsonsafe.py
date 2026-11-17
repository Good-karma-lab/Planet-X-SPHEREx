"""JSON without NaN or Infinity. Python's json writes them as bare NaN / Infinity, which is not JSON: a browser cannot read such a
reply (the field of ε Indi B at 5′ carried "scatter_arcsec": NaN and the lab could not open it). Everything the service writes or
answers goes through `finite` (a non-finite number becomes null); `dumps` refuses any that is left."""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
from fastapi.responses import JSONResponse


def finite(obj: Any) -> Any:
    """The same structure with every NaN, Infinity and -Infinity (Python or numpy) replaced by None."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: finite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [finite(v) for v in obj]
    if isinstance(obj, np.floating):
        v = float(obj)
        return v if math.isfinite(v) else None
    return obj


def dumps(obj: Any, **kw: Any) -> str:
    """The fast path first (almost nothing carries a NaN); only a structure that does is walked and cleaned."""
    try:
        return json.dumps(obj, allow_nan=False, **kw)
    except (ValueError, TypeError):  # a NaN, or a numpy number finite turns into a float
        return json.dumps(finite(obj), allow_nan=False, **kw)


def loads(b: str | bytes) -> Any:
    """Read JSON written before this module (it may carry NaN): the non-finite numbers come back as None. Text without NaN or
    Infinity is read as it is (walking a large meta in Python held the event loop for seconds)."""
    raw = b if isinstance(b, bytes) else b.encode()
    if b"NaN" not in raw and b"Infinity" not in raw:
        return json.loads(raw)
    return finite(json.loads(raw))


class SafeJSONResponse(JSONResponse):
    """The service's default response: never a bare NaN in a reply, whatever a stored result carries."""

    def render(self, content: Any) -> bytes:
        try:  # the fast path: a reply without a NaN is not walked
            return json.dumps(content, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        except (ValueError, TypeError):  # a NaN, or a numpy number finite turns into a float
            return json.dumps(finite(content), ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
