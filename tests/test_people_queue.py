"""People's queue for the brown-dwarf search (service/people_queue.py): one source at a time, its 5′ field built at the warm
runner's priority, the brown-dwarf search run on it, what it found kept; never while a person's job waits."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from service.jobs.scheduler import Priority
from service.people_queue import BDQueue


class _Sch:
    waiting = False

    def person_waiting(self) -> bool:
        return self.waiting


def make(tmp_path: Path) -> tuple[BDQueue, Any, list[Any], set[str]]:
    complete: set[str] = set()
    asked: list[Any] = []
    runs: dict[str, Any] = {}

    def submit(params: dict[str, Any], priority: Priority) -> tuple[str, Any]:
        asked.append((params["ra"], params["dec"], params["fov"], priority))
        return "k", object()

    def get_or_start(key: str) -> Any:
        return runs.setdefault(key, SimpleNamespace(state="running", error="", result={}))

    st = SimpleNamespace(
        cfg=SimpleNamespace(store_dir=tmp_path),
        scheduler=_Sch(),
        store=SimpleNamespace(is_complete=lambda k: k in complete),
        dwarfs=SimpleNamespace(get_or_start=get_or_start, runs=runs),
        submit_field=submit,
        index=None,
    )
    q = BDQueue(st)
    q.field_of = lambda ra, dec: (f"F{ra:.3f}", {"ra": ra, "dec": dec, "fov": 5.0})  # type: ignore[method-assign]
    return q, st, asked, complete


def test_a_queued_source_is_built_searched_and_kept(tmp_path: Path) -> None:
    q, st, asked, complete = make(tmp_path)
    waiting = [{"finding_id": "red:aa1111", "ra": 100.0, "dec": 10.0}, {"finding_id": "red:bb2222", "ra": 200.0, "dec": -5.0}]
    assert q.step(waiting) == "building red:aa1111" and asked == [(100.0, 10.0, 5.0, Priority.P2)]
    st.scheduler.waiting = True
    assert q.step(waiting) is None  # a person's job waits: the queue gives way
    st.scheduler.waiting = False
    complete.add("F100.000")
    assert q.step(waiting) == "searching red:aa1111"
    run = st.dwarfs.runs["F100.000"]
    run.state, run.result = "done", {"candidates": [{"ra": 100.0 + 2.0 / 3600.0, "dec": 10.0}]}  # 2″ away: that source
    assert q.step(waiting) == "checked red:aa1111 (found)"
    assert q.done["red:aa1111"]["found"] is True and q.done["red:aa1111"]["field"] == "F100.000"
    # kept on disk: a new instance does not look at it again
    again = BDQueue(st)
    assert "red:aa1111" in again.done
    # the next one: nothing found near it
    complete.add("F200.000")
    st.dwarfs.runs["F200.000"] = SimpleNamespace(state="done", error="", result={"candidates": [{"ra": 200.1, "dec": -5.0}]})
    assert q.step(waiting[1:]) == "checked red:bb2222" and q.done["red:bb2222"]["found"] is False


def test_a_failed_search_is_said_and_not_looked_at_again(tmp_path: Path) -> None:
    q, st, _asked, complete = make(tmp_path)
    complete.add("F100.000")
    st.dwarfs.runs["F100.000"] = SimpleNamespace(state="failed", error="the field is not finished", result={})
    assert q.step([{"finding_id": "red:aa1111", "ra": 100.0, "dec": 10.0}]) == "failed red:aa1111"
    assert q.done["red:aa1111"] == {**q.done["red:aa1111"], "state": "failed", "why": "the field is not finished"}
    assert q.step([]) is None
