"""No NaN or Infinity in any JSON the service writes or answers (service/jsonsafe.py): the field of ε Indi B at 5′ carried
"scatter_arcsec": NaN in its meta and the lab could not read it."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from service import jsonsafe
from service.products.store import Store


def test_finite_replaces_every_non_finite_number() -> None:
    got = jsonsafe.finite({"a": math.nan, "b": [1.0, math.inf, -math.inf, np.float32("nan"), np.float64(2.5)], "c": (3, "x"), "d": None})
    assert got == {"a": None, "b": [1.0, None, None, None, 2.5], "c": [3, "x"], "d": None}
    assert json.loads(jsonsafe.dumps({"x": math.nan})) == {"x": None}
    with pytest.raises(ValueError):
        json.dumps({"x": math.nan}, allow_nan=False)  # what dumps would refuse without finite


def test_a_meta_is_written_clean_and_an_old_one_is_read_clean(tmp_path: Path) -> None:
    st = Store(tmp_path)
    st.begin("field", "k" * 24, {"ra": 331.0, "dec": -56.8, "fov": 5.0})
    st.write_meta("field", "k" * 24, {"passes": [{"registration": {"scatter_arcsec": math.nan, "dx": 0.1}}]})
    raw = (st.path("field", "k" * 24) / "meta.json").read_text()
    assert "NaN" not in raw and json.loads(raw)["passes"][0]["registration"]["scatter_arcsec"] is None
    # a meta kept from before the fix carries a bare NaN: it is read as null
    (st.path("field", "k" * 24) / "meta.json").write_text('{"passes":[{"registration":{"scatter_arcsec":NaN,"dx":Infinity}}]}')
    meta = st.read_meta("field", "k" * 24)
    assert meta == {"passes": [{"registration": {"scatter_arcsec": None, "dx": None}}]}
    st.close()


def test_a_reply_never_carries_nan() -> None:
    app = FastAPI(default_response_class=jsonsafe.SafeJSONResponse)

    @app.get("/x")
    def x() -> dict[str, object]:
        return {"scatter": float("nan"), "ok": 1.5}

    r = TestClient(app).get("/x")
    assert r.status_code == 200 and "NaN" not in r.text and r.json() == {"scatter": None, "ok": 1.5}


def test_the_fast_paths() -> None:
    """Text without NaN is read as it is; a reply with a numpy number or a NaN still comes out clean."""
    assert jsonsafe.loads(b'{"a": 1.5}') == {"a": 1.5} and jsonsafe.loads('{"a": NaN}') == {"a": None}
    assert json.loads(jsonsafe.dumps({"a": np.float32(1.5), "b": math.inf})) == {"a": 1.5, "b": None}
    assert jsonsafe.SafeJSONResponse({"a": np.float64(2.0), "b": [math.nan]}).body == b'{"a":2.0,"b":[null]}'
