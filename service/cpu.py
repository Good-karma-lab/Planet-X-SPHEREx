"""Compute of the service runs in worker processes, never in the API process.

The API process answers files, small JSON and job status; every numpy/scipy step (decoding rows, preparing an exposure, stacking,
searches, scoring) goes through `await run(fn, *args)`: fn is a module-level function with picklable arguments, executed in the
service's process pool. Calls pass a priority gate first: a person's work (P0/P1, or a request outside any job) is admitted before the
warm set (P2) and maintenance (P3), and the background calls never occupy more than the workers minus a reserve, so there is always a
worker free for a person's call (see `_Gate`).

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
_workers = 1
_gate: _Gate | None = None


LOW = 2  # priorities from this one on (the warm runner, maintenance) are "background": they never take the workers kept free for people


class _Gate:
    """Calls admitted into the pool, by priority, then by arrival.

    At most `workers + 1` calls are inside the pool at once (one waits behind the running ones, so no worker idles between two calls), and
    background calls (priority >= LOW) at most `workers - reserve`: the other workers are always free for a person's calls, which are
    admitted at once and start as soon as they are submitted, never queued behind a long background call.
    """

    def __init__(self, workers: int, reserve: int | None = None) -> None:
        self.total = workers + 1
        r = max(1, min(workers - 1, workers // 3 if reserve is None else reserve)) if workers > 1 else 0
        self.low_cap = max(1, workers - r)
        self.used = 0
        self.used_low = 0
        self.waiting: list[tuple[int, int, bool, asyncio.Future[None]]] = []
        self.seq = itertools.count()

    def _can(self, low: bool) -> bool:
        return self.used < self.total and (not low or self.used_low < self.low_cap)

    def _take(self, low: bool) -> None:
        self.used += 1
        self.used_low += int(low)

    async def acquire(self, priority: int) -> bool:
        """Wait for a place; -> whether the call counts as background (hand it back to `release`)."""
        low = priority >= LOW
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        heapq.heappush(self.waiting, (priority, next(self.seq), low, fut))
        self._grant()  # a place may be free for this call even when background calls wait for theirs
        try:
            await fut
        except asyncio.CancelledError:
            if fut.done() and not fut.cancelled():
                self.release(low)  # the place was handed over just as the caller went away
            raise
        return low

    def release(self, low: bool) -> None:
        self.used -= 1
        self.used_low -= int(low)
        self._grant()

    def _grant(self) -> None:
        """Hand places to the waiting calls in priority order; a background call that may not go yet does not hold up the others."""
        skipped: list[tuple[int, int, bool, asyncio.Future[None]]] = []
        while self.waiting and self.used < self.total:
            item = heapq.heappop(self.waiting)
            _, _, low, fut = item
            if fut.done():
                continue
            if low and self.used_low >= self.low_cap:
                skipped.append(item)
                continue
            self._take(low)
            fut.set_result(None)
        for item in skipped:
            heapq.heappush(self.waiting, item)

    def stats(self) -> dict[str, int]:
        return {
            "free": self.total - self.used,
            "waiting": sum(1 for _, _, _, f in self.waiting if not f.done()),
            "background": self.used_low,
            "background_cap": self.low_cap,
        }


def configure(pool: Executor | None, workers: int) -> None:
    global _pool, _gate, _workers
    _pool = pool
    _workers = max(1, workers)
    _gate = _Gate(workers) if pool is not None else None


def workers() -> int:
    """How many worker processes the pool has (1 without a pool)."""
    return _workers if _pool is not None else 1


def priority() -> int:
    job = JOB.get()
    return int(getattr(job, "priority", 0)) if job is not None else 0


async def run[R](fn: Callable[..., R], *args: Any) -> R:
    if _pool is None or _gate is None:
        return fn(*args)
    low = await _gate.acquire(priority())
    try:
        return await asyncio.get_running_loop().run_in_executor(_pool, fn, *args)
    finally:
        _gate.release(low)


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
