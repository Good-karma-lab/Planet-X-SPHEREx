from __future__ import annotations

from pathlib import Path

import pytest

from service.products import store as st
from service.products import versions


def test_key_depends_on_parameters_and_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    k1 = st.product_key("field", {"ra": 1.0, "dec": 2.0, "fov": 5})
    assert k1 == st.product_key("field", {"fov": 5, "dec": 2.0, "ra": 1.0})  # order does not matter
    assert k1 != st.product_key("field", {"ra": 1.0, "dec": 2.0, "fov": 10})
    assert k1 != st.product_key("field", {"ra": 1.0, "dec": 2.0, "fov": 5}, extra=("cell:abc",))
    monkeypatch.setitem(versions.VERSIONS, "field", "f999")
    assert k1 != st.product_key("field", {"ra": 1.0, "dec": 2.0, "fov": 5})


def test_lineage_includes_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(versions.VERSIONS, "sources", "s1")
    monkeypatch.setitem(versions.DEPENDS, "sources", ("field",))
    assert versions.lineage("sources") == ("field:f2", "sources:s1")


def test_write_complete_read_and_purge_on_bump(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = st.Store(tmp_path)
    key = st.product_key("field", {"ra": 1.0})
    s.begin("field", key, {"ra": 1.0})
    s.write_part("field", key, "quick.sxp", b"abc")
    assert not s.is_complete(key)
    s.complete(key)
    assert s.is_complete(key) and s.read_part("field", key, "quick.sxp") == b"abc"
    assert s.purge_stale() == {}
    monkeypatch.setitem(versions.VERSIONS, "field", "f999")  # a version bump
    assert s.purge_stale() == {"field": 1}
    assert not (tmp_path / "products" / "field" / "f2").exists()
    assert s.stats() == {}
