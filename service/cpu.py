"""Compute of the service runs in worker processes, never in the API process.

The API process answers files, small JSON and job status; every numpy/scipy step (decoding rows, preparing an exposure, stacking,
searches, scoring) goes through `await run(fn, *args)`: fn is a module-level function with picklable arguments, executed in the
service's process pool. Calls pass a priority gate first, so a person's work (P0/P1, or a request outside any job) gets the next free
worker before the warm set (P2) and maintenance (P3). The gate admits a few more calls than there are workers, so the pool never sits
idle and its queue never grows long enough to keep a person waiting.

Without a pool (tests, tools) `run` calls fn in place.
"""

from __future__ import annotations

import asyncio
import contextlib
import heapq
import itertools
from collections.abc import Callable
from concurrent.futures import Executor
from contextvars import ContextVar
from typing import Any

# the job running in this task (the scheduler sets it); its priority is read at call time, so a person taking over a job counts at once
JOB: ContextVar[Any] = ContextVar("sky_job", default=None)

_pool: Executor | None = None
_gate: _Gate | None = None


class _Gate:
    """Slots handed out by priority, then by arrival."""

    def __init__(self, slots: int) -> None:
        self.free = slots
        self.waiting: list[tuple[int, int, asyncio.Future[None]]] = []
        self.seq = itertools.count()

    async def acquire(self, priority: int) -> None:
        if self.free > 0 and not self.waiting:
            self.free -= 1
            return
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        heapq.heappush(self.waiting, (priority, next(self.seq), fut))
        try:
            await fut
        except asyncio.CancelledError:
            if fut.done() and not fut.cancelled():
                self.release()  # the slot was handed over just as the caller went away
            raise

    def release(self) -> None:
        while self.waiting:
            _, _, fut = heapq.heappop(self.waiting)
            if not fut.done():
                fut.set_result(None)
                return
        self.free += 1

    def stats(self) -> dict[str, int]:
        return {"free": self.free, "waiting": sum(1 for _, _, f in self.waiting if not f.done())}


def configure(pool: Executor | None, workers: int) -> None:
    global _pool, _gate
    _pool = pool
    _gate = _Gate(workers + 2) if pool is not None else None


def priority() -> int:
    job = JOB.get()
    return int(getattr(job, "priority", 0)) if job is not None else 0


async def run[R](fn: Callable[..., R], *args: Any) -> R:
    if _pool is None or _gate is None:
        return fn(*args)
    await _gate.acquire(priority())
    try:
        return await asyncio.get_running_loop().run_in_executor(_pool, fn, *args)
    finally:
        _gate.release()


def stats() -> dict[str, int]:
    return _gate.stats() if _gate is not None else {"free": 0, "waiting": 0}


def worker_init(mem_mb: int) -> None:
    """In each worker process: one BLAS thread (the pool's processes are the parallelism) and a memory cap, so a field too large for
    the machine fails its own job with MemoryError instead of taking the service down."""
    import os
    import resource

    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[var] = "1"
    if mem_mb > 0:
        cap = mem_mb * 1024 * 1024
        with contextlib.suppress(ValueError, OSError):  # not allowed here: run without the cap
            resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
