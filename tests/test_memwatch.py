from __future__ import annotations

import gc

import pytest

from service.memwatch import GC_THRESHOLD, MemWatch, census, refreeze, settle, trim


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
    assert [cpu._in_worker(divmod, 7, 2) for _ in range(3)] == [((3, 1), False)] * 3 and len(n) == 1
    monkeypatch.setattr(cpu, "trim", lambda: "trim: done")
    monkeypatch.setattr(cpu, "_trimmed", -1e9)
    assert [cpu._in_worker(divmod, 7, 2)[1] for _ in range(2)] == [True, False]
    with pytest.raises(ZeroDivisionError):
        cpu._in_worker(divmod, 1, 0)


def test_a_stall_names_the_full_collection_within_it() -> None:
    import time

    from service.loopwatch import LoopWatch

    before = time.monotonic()
    w = MemWatch({})
    w.start()
    try:
        gc.collect()
    finally:
        w.stop()
    assert "a full collection stopped every thread" in LoopWatch._collected(before)
    assert LoopWatch._collected(time.monotonic()) == ""


def test_the_line_says_what_the_collector_walks_and_what_is_frozen() -> None:
    line = MemWatch({}).line()
    assert " gc_count (" in line and " tracked " in line and f" frozen {gc.get_freeze_count()} " in line


def test_census_names_the_most_numerous_types_first() -> None:
    class Many:
        pass

    kept = [Many() for _ in range(200_000)]
    said = census(3)
    assert said.startswith("census: tracked ") and f"{Many.__module__}.{Many.__qualname__} 200000" in said
    assert said.count(", ") == 2
    del kept


def test_settle_freezes_what_is_alive_once_and_a_frozen_one_that_leaves_is_freed() -> None:
    import weakref

    class Kept:
        pass

    before = gc.get_freeze_count()  # the interpreter's own (375 on 3.12)
    kept = [Kept() for _ in range(1000)]
    gone = weakref.ref(kept[0])
    try:
        said = settle(True)
        assert "census: tracked " in said and "; frozen " in said
        assert gc.get_freeze_count() >= before + 1000
        assert all(o is not kept[0] for o in gc.get_objects())  # left out of the walks
        kept.pop(0)  # a layout that leaves its cache: freed by its count, frozen or not
        assert gone() is None
    finally:
        gc.unfreeze()


def test_settle_off_counts_and_freezes_nothing() -> None:
    before = gc.get_freeze_count()
    said = settle(False)
    assert said.startswith("census: ") and "freeze off" in said and gc.get_freeze_count() == before


def test_settle_waits_for_warm_then_for_no_persons_job_and_not_past_its_limit(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import asyncio

    from service import memwatch

    done: list[bool] = []
    monkeypatch.setattr(memwatch, "settle", lambda freeze: done.append(freeze) or "census: x")
    asks = iter([False, RuntimeError("not there yet"), True])
    people = iter([1, 1, 0])

    def ready() -> bool:
        a = next(asks)
        if isinstance(a, Exception):
            raise a
        return a

    def quiet() -> bool:
        assert next(asks, None) is None  # asked only once warm
        return next(people) == 0

    with caplog.at_level("WARNING", logger="sky.mem"):
        assert asyncio.run(memwatch.settle_when(ready, True, quiet=quiet, max_s=10.0, every_s=0.01)) == "census: x" and done == [True]
    assert next(people, None) is None and "s for warm" in caplog.text and "s for no person's job" in caplog.text
    # neither ever comes: settled anyway, each wait no longer than its limit
    t = __import__("time").monotonic()
    assert asyncio.run(memwatch.settle_when(lambda: False, False, quiet=lambda: False, max_s=0.05, every_s=0.01)) == "census: x"
    assert done == [True, False] and __import__("time").monotonic() - t < 1.0


def test_tracked_is_counted_only_with_the_freeze_on() -> None:
    from service.memwatch import tracked

    assert tracked() == "-" and " tracked - " in MemWatch({}).line()  # SKY_GC_FREEZE=0: no walk of every object each line
    gc.freeze()
    try:
        import re

        assert isinstance(tracked(), int) and re.search(r" tracked \d+ frozen ", MemWatch({}).line())
    finally:
        gc.unfreeze()
    assert tracked() == "-"


def test_refreeze_takes_the_garbage_among_the_frozen_and_freezes_what_lives() -> None:
    import weakref

    class Node:
        other: object = None

    a, b, kept = Node(), Node(), Node()
    a.other, b.other = b, a  # a cycle: only a full collection frees it
    gone = weakref.ref(a)
    gc.freeze()
    try:
        del a, b  # garbage now, but frozen: a collection does not see it
        gc.collect()
        assert gone() is not None
        said = refreeze()
        assert said.startswith("refreeze: collected ") and gone() is None  # taken: no cycle stays for good
        assert all(o is not kept for o in gc.get_objects())  # what lives is frozen again
    finally:
        gc.unfreeze()


def test_keep_frozen_freezes_again_every_so_often_and_skips_a_busy_hour(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import asyncio

    from service import memwatch

    done: list[str] = []
    state = {"busy": False, "asked": 0}

    def fake_refreeze() -> str:
        done.append("refreeze")
        state["busy"] = len(done) == 2  # after the first refreeze a person's job runs for a while
        return "refreeze: x"

    def quiet() -> bool:
        if state["busy"]:
            state["asked"] += 1
            state["busy"] = state["asked"] < 12  # longer than one wait (again_s / every_s = 5 asks)
            return False
        return True

    monkeypatch.setattr(memwatch, "settle", lambda freeze: done.append("settle") or "census: x")
    monkeypatch.setattr(memwatch, "refreeze", fake_refreeze)
    with caplog.at_level("WARNING", logger="sky.mem"):
        asyncio.run(memwatch.keep_frozen(lambda: True, True, quiet=quiet, max_s=1.0, every_s=0.01, again_s=0.05, rounds=4))
    assert done == ["settle", "refreeze", "refreeze", "refreeze"] or done == ["settle", "refreeze", "refreeze"]
    assert "refreeze: skipped, a person's job ran" in caplog.text
    done.clear()
    asyncio.run(memwatch.keep_frozen(lambda: True, False, max_s=1.0, every_s=0.01, again_s=0.01, rounds=3))
    assert done == ["settle"]  # SKY_GC_FREEZE=0: never frozen, never again
