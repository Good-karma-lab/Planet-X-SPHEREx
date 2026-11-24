from __future__ import annotations

import gc

import pytest

from service.memwatch import GC_THRESHOLD, MemWatch, trim


def test_line_names_sizes_and_survives_a_missing_collection() -> None:
    def gone() -> int:
        raise AttributeError("not there yet")

    w = MemWatch({"kept": lambda: 7, "gone": gone})
    w.start()
    try:
        gc.collect()
        line = w.line()
    finally:
        w.stop()
    assert "'kept': 7" in line and "'gone': 'AttributeError'" in line
    assert "gc2 1 " in line  # the collection was counted, and the next line starts anew
    assert "gc2 0 " in w.line()
    assert w._on_gc not in gc.callbacks


def test_threshold_is_raised_while_it_watches_and_put_back() -> None:
    before = gc.get_threshold()
    w = MemWatch({})
    w.start()
    try:
        assert gc.get_threshold() == GC_THRESHOLD
    finally:
        w.stop()
    w.stop()  # a second stop changes nothing
    assert gc.get_threshold() == before


def test_trim_says_what_it_did_or_nothing() -> None:
    said = trim()
    assert said is None or said.startswith("trim: rss_anon_mb ")


def test_a_worker_trims_after_a_call_at_most_so_often_and_never_spoils_it(monkeypatch: pytest.MonkeyPatch) -> None:
    from service import cpu

    n: list[int] = []

    def trim_fails() -> None:
        n.append(1)
        raise OSError("no trim")

    monkeypatch.setattr(cpu, "trim", trim_fails)
    monkeypatch.setattr(cpu, "_trimmed", -1e9)
    assert [cpu._in_worker(divmod, 7, 2) for _ in range(3)] == [(3, 1)] * 3 and len(n) == 1
    with pytest.raises(ZeroDivisionError):
        cpu._in_worker(divmod, 1, 0)
