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
    for pct, ok in ((71.0, True), (77.0, False), (90.0, False)):
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


def test_a_field_that_fails_does_not_stand_in_the_way(tf: TaskFields) -> None:
    names = ["a", "b"]
    assert [tf.first_untried(names, now=t) for t in (0.0, 60.0, 120.0)] == [0, 1, None]
    assert tf.first_untried(["a", "b", "c"], now=180.0) == 2
    assert tf.first_untried(names, now=taskfields.RETRY_S + 1.0) == 0  # two hours on it is tried again


def test_the_fields_tasks_open_on_are_pinned_and_let_go_of(tf: TaskFields) -> None:
    store = tf.st.store
    pins: dict[str, bool] = {}
    store.is_pinned = lambda k: pins.get(k, False)
    store.pin = lambda k, on=True: pins.__setitem__(k, on)
    key = {p["fov"]: next(r["key"] for r in store.rows if r["params"] == p) for p in (F10, F20)}
    finds = [{"ra": 50.0, "dec": 10.0}, {"ra": 50.0, "dec": 10.0 + 4.5 / 60}, {"ra": 80.0, "dec": 0.0}]
    assert tf.pin(finds) == (2, 0) and pins == {key[10.0]: True, key[20.0]: True}
    assert tf.pin(finds) == (0, 0)
    assert tf.pin(finds[:1]) == (0, 1) and pins == {key[10.0]: True, key[20.0]: False}
    # a field somebody else pinned (the warm set's) is never this module's to let go of
    pins[key[20.0]] = True
    assert tf.pin(finds) == (0, 0) and tf.pin(finds[:1]) == (0, 0) and pins[key[20.0]] is True


def test_the_real_store_neither_strips_nor_loses_a_pinned_field(tmp_path: Path) -> None:
    from service.products.store import Store

    s = Store(tmp_path)
    s.begin("field", "k" * 24, {"ra": 1.0, "dec": 2.0, "fov": 10.0})
    s.complete("k" * 24)
    assert not s.is_pinned("k" * 24)
    s.pin("k" * 24)
    assert s.is_pinned("k" * 24) and s.strip("field", "k" * 24) == 0


def test_nothing_is_built_ahead_while_people_fill_the_disk(tf: TaskFields, monkeypatch: pytest.MonkeyPatch) -> None:
    now, used = [0.0], [71.0]
    monkeypatch.setattr(taskfields.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(taskfields.shutil, "disk_usage", lambda root: SimpleNamespace(total=100.0, used=used[0], free=100.0 - used[0]))
    got = []
    for minute, pct in ((0, 66.0), (10, 69.0), (20, 71.0), (25, 72.5), (70, 73.0), (80, 74.0), (90, 76.9), (95, 77.0)):
        now[0], used[0] = minute * 60.0, pct
        got.append(tf.room())
    # below 72 always; at 72.5 the disk grew by 6.5 in half an hour: no; 45 min later slowly again: yes; +3.9 again: no; 77: no
    assert got == [True, True, True, False, True, True, False, False]


def _stamp(peak: float, seed: int) -> list[list[float]]:
    import numpy as np

    a = np.random.default_rng(seed).normal(100.0, 1.0, (15, 15))
    a[7, 7] += peak
    return a.tolist()


@pytest.mark.parametrize(("peak", "asked"), [(60.0, True), (8.0, False)])
def test_an_anomaly_is_asked_about_only_when_its_odd_pictures_show_the_star(
    tf: TaskFields, tmp_path: Path, peak: float, asked: bool
) -> None:
    # 27 Nov 2026 (exploratory report, С2): anomaly:bdb2bb574761 was asked "is another star touching the one in the middle?" over six
    # stamps of noise (the middle 10× the noise in the median); the neighbour of HD 21496 stands 55-164× and is judged at once
    import json

    from service.accounts import people
    from service.spectrum import Spectra

    tf.st.spectra = sp = Spectra(None, None, None, tmp_path)  # type: ignore[arg-type]
    a = {"ra": 48.300065, "dec": 5.992323, "pmra": 0.0, "pmdec": 0.0, "drivers": [{"lo": 4.2, "hi": 4.6}]}
    assert tf.star_in_stamps(a) is None  # not on disk yet
    key = sp.key_of(taskfields.anomaly_params(a))
    sp.dir.mkdir(parents=True, exist_ok=True)
    # 8 exposures in the odd bin (6 shown) and 4 outside it, whose stamps are never looked at
    points = [{"i": i, "ok": True, "flux": 1.0 + i, "err": 0.1, "lam": 4.4 if i < 8 else 1.0} for i in range(12)]
    stamps = {str(i): {"raw": _stamp(peak if i < 8 else 500.0, i)} for i in range(12)}
    (sp.dir / f"{key}.json").write_text(json.dumps({"points": points}))
    (sp.dir / f"{key}.stamps.json").write_text(json.dumps(stamps))
    assert len(taskfields.shown_stamps(points, stamps, a["drivers"])) == taskfields.STAMPS_SHOWN
    assert tf.star_in_stamps(a) is asked
    row = {"kind": "anomaly", "visible": True, "star_shown": tf.star_in_stamps(a)}
    assert people.askable(row) is asked and people.askable({"kind": "anomaly", "visible": True})  # not measured: as before
