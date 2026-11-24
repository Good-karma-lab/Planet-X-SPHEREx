from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from service import taskfields
from service.products.store import product_key
from service.taskfields import TaskFields
from service.workers import fieldjob


class FakeStore:
    def __init__(self, fields: list[dict[str, Any]], root: Path) -> None:
        self.root = root
        self.rows = [
            {"key": product_key("field", p, extra=(p_sig(p),)), "params": p, "complete": True, "kind": "field", "created": 0.0}
            for p in fields
        ]
        self.meta: dict[str, dict[str, Any]] = {r["key"]: {"passes": [1]} for r in self.rows}
        self.listed = 0

    def listing(self, kinds: tuple[str, ...]) -> list[dict[str, Any]]:
        self.listed += 1
        return self.rows

    def read_meta(self, kind: str, key: str) -> dict[str, Any]:
        return self.meta[key]


def p_sig(p: dict[str, Any]) -> str:
    return f"sig {p['ra']} {p['dec']}"


F10 = {"ra": 50.0, "dec": 10.0, "fov": 10.0}
F20 = {"ra": 50.1, "dec": 10.1, "fov": 20.0}
F5 = {"ra": 50.02, "dec": 10.0, "fov": 5.0}


@pytest.fixture
def tf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TaskFields:
    monkeypatch.setattr(fieldjob, "exposure_signature", lambda index, p: p_sig(p))
    monkeypatch.setattr(fieldjob, "canonical", lambda ra, dec, fov: {"ra": ra, "dec": dec, "fov": fov})
    st = SimpleNamespace(store=FakeStore([F20, F5, F10], tmp_path), index=object(), spectra=None)
    return TaskFields(st)


def test_a_find_opens_on_the_smallest_built_field_that_holds_it(tf: TaskFields) -> None:
    assert tf.place(50.0, 10.0) == F10  # 10 arcmin before 20 and before 5
    assert tf.place(50.0, 10.0 + 4.5 / 60) == F20  # too near the edge of the 10 arcmin field
    assert tf.place(51.5, 10.0) is None


def test_a_stripped_or_an_older_field_is_not_one(tf: TaskFields) -> None:
    rows = tf.st.store.rows
    tf.st.store.meta[next(r["key"] for r in rows if r["params"] == F10)] = {"stripped": True}
    assert tf.place(50.0, 10.0) == F20
    older = next(r for r in rows if r["params"] == F20)
    older["key"] = "made of other exposures"
    tf.st.store.meta[older["key"]] = {"passes": [1]}
    tf._at = -1e9
    assert tf.place(50.0, 10.0) == F5


def test_what_is_built_ahead_is_the_quarter_once_and_nothing_twice(tf: TaskFields) -> None:
    finds = [{"ra": 60.0, "dec": 20.0}, {"ra": 60.01, "dec": 20.0}, {"ra": 50.0, "dec": 10.0}, {"ra": 70.0, "dec": 20.0}]
    quarter = {"ra": 60.0, "dec": 20.01}
    got = tf.wanted(finds, lambda ra, dec: quarter if ra < 65 else {"ra": 70.2, "dec": 20.0})
    assert got == [{"ra": 60.0, "dec": 20.01, "fov": 10.0}, {"ra": 70.0, "dec": 20.0, "fov": 10.0}]  # the last: outside its quarter


def test_nothing_is_built_ahead_on_a_full_disk(tf: TaskFields, monkeypatch: pytest.MonkeyPatch) -> None:
    for pct, ok in ((71.0, True), (72.0, False), (90.0, False)):
        monkeypatch.setattr(taskfields.shutil, "disk_usage", lambda root, pct=pct: SimpleNamespace(total=100.0, used=pct, free=100.0 - pct))
        assert tf.room() is ok


def test_an_anomaly_is_ready_when_its_stamps_are_kept(tf: TaskFields, tmp_path: Path) -> None:
    from service.spectrum import Spectra

    tf.st.spectra = sp = Spectra(None, None, None, tmp_path)  # type: ignore[arg-type]
    a = {"ra": 46.361388, "dec": 11.524952, "pmra": 0.0, "pmdec": 0.0}
    assert not tf.spectrum_kept(a)
    key = sp.key_of(taskfields.anomaly_params(a))
    sp.dir.mkdir(parents=True, exist_ok=True)
    (sp.dir / f"{key}.json").write_text("{}")
    assert not tf.spectrum_kept(a)
    (sp.dir / f"{key}.stamps.json").write_text("{}")
    assert tf.spectrum_kept(a)
