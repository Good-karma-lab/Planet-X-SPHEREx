"""The scheduler.

Classes (strict order): P0 a job a person is watching; P1 a person's background work (a job they left, the searches of their field);
P2 the warm runner; P3 maintenance. Jobs of P2 and P3 pause at their next checkpoint while a person is active (a person request in the
last 60 s, or a P0/P1 job running or waiting); paused time does not count against a deadline.

Slots: "light" jobs (spectrum, identify, archive) and "heavy" jobs (fields, searches) have separate slots, so a small job never waits
behind a heavy one; a job beyond the slots waits with a visible phase. A job starts only when its memory estimate fits the budget.

A job with the same key as one already queued, running or done is not started again: the caller attaches to it. A P0 job that nobody has
polled for `abandon_after_s` becomes P1 ("abandoned"); beyond `max_abandoned` abandoned heavy jobs the oldest is cancelled. A poll of
an abandoned job makes it P0 again.

Job state lives in memory; what a job produces lives in the product store, so a restart loses no result (a request re-attaches to the
complete product or starts the job again).
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import time
import traceback
from collections.abc import Awaitable, Callable
from concurrent.futures import Executor
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Literal, TypeVar

from service import cpu

T = TypeVar("T")

BG_HEAVY = 2  # background heavy jobs at once: what they read and compute never buries a person's field
PERSON_QUIET_S = 60.0


class Priority(IntEnum):
    P0 = 0  # a person watches it
    P1 = 1  # a person's background work
    P2 = 2  # warm runner
    P3 = 3  # maintenance


Size = Literal["light", "heavy"]
State = Literal["queued", "running", "paused", "done", "error", "cancelled"]


class Cancelled(Exception):  # noqa: N818 (a control-flow signal, not an error)
    pass


@dataclass
class Job:
    id: str
    key: str
    kind: str
    params: dict[str, Any]
    priority: Priority
    size: Size
    mem_mb: int
    state: State = "queued"
    phase: str = "waiting for a free worker"
    done: int = 0
    total: int = 0
    parts: list[str] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    abandoned: bool = False
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    last_poll: float = field(default_factory=time.time)
    paused_s: float = 0.0
    _cancel: bool = False

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "key": self.key,
            "kind": self.kind,
            "state": self.state,
            "phase": self.phase,
            "done": self.done,
            "total": self.total,
            "parts": list(self.parts),
            "result": self.result,
            "error": self.error,
            "priority": int(self.priority),
            "abandoned": self.abandoned,
            "elapsed_s": round((self.finished or time.time()) - (self.started or self.created), 1),
        }


JobFn = Callable[["Context"], Awaitable[dict[str, Any]]]


class Context:
    """What a running job uses to report progress, publish parts, yield to a person and run compute in worker processes."""

    def __init__(self, sched: Scheduler, job: Job) -> None:
        self._s = sched
        self.job = job

    def phase(self, text: str, done: int | None = None, total: int | None = None) -> None:
        self.job.phase = text
        if done is not None:
            self.job.done = done
        if total is not None:
            self.job.total = total

    def part_ready(self, name: str) -> None:
        if name not in self.job.parts:
            self.job.parts.append(name)

    async def checkpoint(self) -> None:
        """Raise Cancelled when cancelled; wait while a person needs the machine (P2/P3 only)."""
        j = self.job
        if j._cancel:
            raise Cancelled
        if j.priority < Priority.P2 or not self._s.person_waiting():
            return
        if j.state == "paused":  # another task of this job (a block of its scan) already waits: wait with it, it restores the phase
            while self._s.person_waiting() and not j._cancel:
                await asyncio.sleep(0.5)
            if j._cancel:
                raise Cancelled
            return
        before, t0 = j.phase, time.time()
        j.state, j.phase = "paused", "paused: a person is working"
        while self._s.person_waiting() and not j._cancel:
            await asyncio.sleep(0.5)
        j.paused_s += time.time() - t0
        j.state, j.phase = "running", before
        if j._cancel:
            raise Cancelled

    async def compute(self, fn: Callable[..., T], *args: Any) -> T:
        """fn (module-level, picklable arguments) in a worker process, through the priority gate of service.cpu."""
        if self._s.executor is None:
            return fn(*args)
        return await cpu.run(fn, *args)


@dataclass
class Scheduler:
    executor: Executor | None = None
    slots: dict[str, int] = field(default_factory=lambda: {"light": 8, "heavy": 2})
    memory_budget_mb: int = 9000
    abandon_after_s: float = 20.0
    max_abandoned: int = 3
    jobs: dict[str, Job] = field(default_factory=dict)
    by_key: dict[str, str] = field(default_factory=dict)
    _fns: dict[str, JobFn] = field(default_factory=dict)
    _tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    _ids: itertools.count[int] = field(default_factory=itertools.count)
    _last_person: float = 0.0
    _wake: asyncio.Event | None = None
    _loop_task: asyncio.Task[None] | None = None

    # ---- life cycle -------------------------------------------------------------------------------------------------------------
    def start(self) -> None:
        self._wake = asyncio.Event()
        self._loop_task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        for t in list(self._tasks.values()):
            t.cancel()
        if self._loop_task:
            self._loop_task.cancel()

    # ---- people -----------------------------------------------------------------------------------------------------------------
    def saw_person(self) -> None:
        self._last_person = time.time()

    def person_waiting(self) -> bool:
        """A person's job (P0/P1) is queued or running: background jobs give way at their next checkpoint. A visitor who only looks
        at finished products costs nothing and does not stop the warm set."""
        return any(j.priority <= Priority.P1 and j.state in ("queued", "running") for j in self.jobs.values())

    def person_active(self) -> bool:
        if time.time() - self._last_person < PERSON_QUIET_S:
            return True
        return any(j.priority <= Priority.P1 and j.state in ("queued", "running") for j in self.jobs.values())

    # ---- submit and poll --------------------------------------------------------------------------------------------------------
    def submit(self, kind: str, key: str, params: dict[str, Any], fn: JobFn, priority: Priority, size: Size, mem_mb: int = 500) -> Job:
        jid = self.by_key.get(key)
        if jid is not None:
            j = self.jobs[jid]
            if j.state not in ("error", "cancelled"):
                if priority < j.priority:  # a person takes over a background job
                    j.priority = priority
                    j.abandoned = False
                j.last_poll = time.time()
                self._kick()
                return j
        j = Job(id=f"j{next(self._ids)}", key=key, kind=kind, params=params, priority=priority, size=size, mem_mb=mem_mb)
        self.jobs[j.id] = j
        self.by_key[key] = j.id
        self._fns[j.id] = fn
        self._kick()
        return j

    def poll(self, job_id: str) -> Job | None:
        j = self.jobs.get(job_id)
        if j is None:
            return None
        j.last_poll = time.time()
        if j.abandoned and j.state in ("queued", "running", "paused"):
            j.abandoned = False
            j.priority = Priority.P0
            self._kick()
        return j

    def cancel(self, job_id: str) -> None:
        j = self.jobs.get(job_id)
        if j and j.state in ("queued", "running", "paused"):
            j._cancel = True
            if j.state == "queued":
                j.state, j.phase, j.finished = "cancelled", "cancelled", time.time()
            self._kick()

    # ---- the loop ---------------------------------------------------------------------------------------------------------------
    def _kick(self) -> None:
        if self._wake is not None:
            self._wake.set()

    def _running(self, size: Size) -> list[Job]:
        return [j for j in self.jobs.values() if j.size == size and j.state in ("running", "paused")]

    def _memory_used(self, person_only: bool = False) -> int:
        return sum(
            j.mem_mb for j in self.jobs.values() if j.state in ("running", "paused") and (not person_only or j.priority <= Priority.P1)
        )

    def _abandon_check(self) -> None:
        now = time.time()
        for j in self.jobs.values():
            if j.priority == Priority.P0 and j.state in ("queued", "running", "paused") and now - j.last_poll > self.abandon_after_s:
                j.priority, j.abandoned = Priority.P1, True
        ab = sorted(
            (j for j in self.jobs.values() if j.abandoned and j.size == "heavy" and j.state in ("queued", "running", "paused")),
            key=lambda j: j.last_poll,
        )
        for j in ab[: max(0, len(ab) - self.max_abandoned)]:
            self.cancel(j.id)

    async def _run(self) -> None:
        assert self._wake is not None
        while True:
            self._abandon_check()
            for size in ("heavy", "light"):
                free = self.slots[size] - len(self._running(size))
                queued = sorted(
                    (j for j in self.jobs.values() if j.state == "queued" and j.size == size), key=lambda j: (j.priority, j.created)
                )
                for j in queued:
                    # a person's job (P0) may run beyond the slots by two: it never waits behind background searches
                    if free <= 0 and not (j.priority == Priority.P0 and free > -2):
                        j.phase = f"waiting for a free worker ({queued.index(j) + 1} ahead)" if j.phase.startswith("waiting") else j.phase
                        continue
                    # a person's job never waits behind background work: the background (P2: the warm set, the page's own automatic
                    # searches, the post-warm runs) starts only while no person's job is queued or running, and at most BG_HEAVY at a
                    # time; the lowest priority (night scans) waits while anyone uses the site at all
                    if j.priority >= Priority.P2 and (
                        self.person_waiting()
                        or (j.priority >= Priority.P3 and self.person_active())
                        or (size == "heavy" and sum(1 for r in self._running(size) if r.priority >= Priority.P2) >= BG_HEAVY)
                    ):
                        j.phase = (
                            "paused: a person is working"
                            if self.person_waiting() or j.priority >= Priority.P3
                            else "queued behind other background work"
                        )
                        continue
                    # a person's job counts only the memory of other people's jobs: the warm set's jobs give way to it at their checkpoints
                    used = self._memory_used(person_only=j.priority <= Priority.P1)
                    if used + j.mem_mb > self.memory_budget_mb and self._running(size):
                        j.phase = "waiting for memory"
                        continue
                    free -= 1
                    self._start(j)
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=1.0)

    def _start(self, j: Job) -> None:
        j.state, j.started, j.phase = "running", time.time(), "starting"
        ctx = Context(self, j)
        fn = self._fns[j.id]

        async def run() -> None:
            cpu.JOB.set(j)  # every compute call of this job (and of the tasks it starts) is gated at the job's priority
            try:
                j.result = await fn(ctx)
                j.state, j.phase = "done", "done"
            except (Cancelled, asyncio.CancelledError):
                j.state, j.phase = "cancelled", "cancelled"
            except Exception as e:  # the reason is kept on the job and shown
                j.state, j.error, j.phase = "error", f"{type(e).__name__}: {e}", "failed"
                j.result = {"traceback": traceback.format_exc(limit=5)}
            finally:
                j.finished = time.time()
                self._tasks.pop(j.id, None)
                self._kick()

        self._tasks[j.id] = asyncio.get_running_loop().create_task(run())

    def snapshot(self) -> dict[str, Any]:
        live = [j for j in self.jobs.values() if j.state in ("queued", "running", "paused")]
        return {
            "running": sum(1 for j in live if j.state == "running"),
            "paused": sum(1 for j in live if j.state == "paused"),
            "queued": sum(1 for j in live if j.state == "queued"),
            "abandoned": sum(1 for j in live if j.abandoned),
            "person_active": self.person_active(),
            "memory_mb": self._memory_used(),
        }
