from __future__ import annotations

import asyncio
from typing import Any

import pytest

from service.jobs import scheduler as sch
from service.jobs.scheduler import Context, Priority, Scheduler


async def until(cond, timeout: float = 5.0) -> None:  # type: ignore[no-untyped-def]
    t = 0.0
    while not cond():
        await asyncio.sleep(0.02)
        t += 0.02
        if t > timeout:
            raise AssertionError("condition not reached")


def sleeper(seconds: float, log: list[str] | None = None, name: str = "") -> Any:
    async def fn(ctx: Context) -> dict[str, Any]:
        if log is not None:
            log.append(f"start {name}")
        steps = max(1, int(seconds / 0.05))
        for k in range(steps):
            ctx.phase("working", k, steps)
            await ctx.checkpoint()
            await asyncio.sleep(0.05)
        return {"ok": name}

    return fn


def test_same_key_attaches() -> None:
    async def run() -> None:
        s = Scheduler()
        s.start()
        a = s.submit("field", "k1", {}, sleeper(0.1), Priority.P0, "heavy")
        b = s.submit("field", "k1", {}, sleeper(0.1), Priority.P0, "heavy")
        assert a is b
        await until(lambda: a.state == "done")
        assert s.submit("field", "k1", {}, sleeper(0.1), Priority.P0, "heavy") is a  # a done job is reused too
        await s.stop()

    asyncio.run(run())


def test_a_person_goes_first_and_background_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sch, "PERSON_QUIET_S", 0.3)

    async def run() -> None:
        log: list[str] = []
        s = Scheduler(slots={"light": 1, "heavy": 1})
        s.start()
        first = s.submit("field", "busy", {}, sleeper(0.3, log, "busy"), Priority.P0, "heavy")
        await until(lambda: first.state == "running")
        warm = s.submit("field", "warm", {}, sleeper(0.1, log, "warm"), Priority.P2, "heavy")
        person = s.submit("field", "person", {}, sleeper(0.1, log, "person"), Priority.P0, "heavy")
        await until(lambda: person.state == "done" and warm.state == "done", timeout=10)
        assert log.index("start person") < log.index("start warm")
        await s.stop()

    asyncio.run(run())


def test_background_pauses_while_a_person_is_active(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sch, "PERSON_QUIET_S", 0.4)

    async def run() -> None:
        s = Scheduler()
        s.start()
        warm = s.submit("field", "warm", {}, sleeper(1.0), Priority.P2, "heavy")
        await until(lambda: warm.state == "running")
        person = s.submit("field", "person", {}, sleeper(0.3), Priority.P0, "heavy")
        await until(lambda: warm.state == "paused")
        assert warm.phase == "paused: a person is working"
        await until(lambda: warm.state == "done" and person.state == "done", timeout=10)
        assert warm.paused_s > 0.1
        await s.stop()

    asyncio.run(run())


def test_abandoned_job_keeps_running_and_a_poll_brings_it_back() -> None:
    async def run() -> None:
        s = Scheduler(abandon_after_s=0.2)
        s.start()
        j = s.submit("field", "k", {}, sleeper(2.0), Priority.P0, "heavy")
        await until(lambda: j.abandoned)
        assert j.priority == Priority.P1 and j.state == "running"
        s.poll(j.id)
        assert not j.abandoned and j.priority == Priority.P0
        s.cancel(j.id)
        await until(lambda: j.state == "cancelled")
        await s.stop()

    asyncio.run(run())


def test_too_many_abandoned_jobs_cancel_the_oldest() -> None:
    async def run() -> None:
        s = Scheduler(abandon_after_s=0.1, max_abandoned=1, slots={"light": 1, "heavy": 4})
        s.start()
        a = s.submit("field", "a", {}, sleeper(3.0), Priority.P0, "heavy")
        await asyncio.sleep(0.05)
        b = s.submit("field", "b", {}, sleeper(3.0), Priority.P0, "heavy")
        await until(lambda: a.state == "cancelled", timeout=5)
        assert b.state in ("running", "paused")
        await s.stop()

    asyncio.run(run())


def test_light_jobs_do_not_wait_behind_heavy_ones_and_errors_are_kept() -> None:
    async def boom(ctx: Context) -> dict[str, Any]:
        raise ValueError("no exposures here")

    async def run() -> None:
        s = Scheduler(slots={"light": 2, "heavy": 1})
        s.start()
        s.submit("field", "h1", {}, sleeper(1.0), Priority.P0, "heavy")
        s.submit("field", "h2", {}, sleeper(1.0), Priority.P0, "heavy")
        light = s.submit("spectrum", "l", {}, sleeper(0.05), Priority.P0, "light")
        await until(lambda: light.state == "done", timeout=1.0)
        bad = s.submit("spectrum", "e", {}, boom, Priority.P0, "light")
        await until(lambda: bad.state == "error")
        assert bad.error == "ValueError: no exposures here" and bad.phase == "failed"
        await s.stop()

    asyncio.run(run())
