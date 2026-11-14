from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

from service import cpu

WORKERS = 4


def slow(seconds: float, log: list[tuple[str, float]], name: str) -> str:
    log.append((name + ":start", time.monotonic()))
    time.sleep(seconds)
    return name


async def as_job(priority: int, fn: Any, *args: Any) -> Any:
    cpu.JOB.set(SimpleNamespace(priority=priority))
    return await cpu.run(fn, *args)


def run_with_pool(coro: Any) -> Any:
    pool = ThreadPoolExecutor(WORKERS + 2)
    cpu.configure(pool, WORKERS)
    try:
        return asyncio.run(coro)
    finally:
        cpu.configure(None, 0)
        pool.shutdown(wait=True)


def test_a_person_starts_at_once_while_background_calls_fill_the_pool() -> None:
    log: list[tuple[str, float]] = []

    async def scenario() -> float:
        background = [asyncio.create_task(as_job(2, slow, 0.6, log, f"warm{i}")) for i in range(8)]
        await asyncio.sleep(0.15)  # the background calls have taken every place they may
        t0 = time.monotonic()
        await as_job(0, slow, 0.05, log, "person")
        started = dict(log)["person:start"]
        await asyncio.gather(*background)
        return started - t0

    wait = run_with_pool(scenario())
    assert wait < 0.2, f"the person's call waited {wait:.2f} s behind background calls"


def test_background_calls_never_hold_more_than_the_workers_minus_the_reserve() -> None:
    running = 0
    peak = 0
    lock = threading.Lock()

    def counted() -> None:
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.1)
        with lock:
            running -= 1

    async def scenario() -> None:
        await asyncio.gather(*(as_job(2, counted) for _ in range(12)))

    run_with_pool(scenario())
    gate = cpu.stats()  # configured to None again: the numbers come from the peak
    assert peak <= WORKERS - max(1, WORKERS // 3) and gate == {"free": 0, "waiting": 0}


def test_people_are_served_before_background_calls_that_wait() -> None:
    log: list[tuple[str, float]] = []

    async def scenario() -> None:
        # every place is taken by people for a moment; then one background call and one person's call wait: the person's goes first
        holders = [asyncio.create_task(as_job(0, slow, 0.3, log, f"hold{i}")) for i in range(WORKERS + 1)]
        await asyncio.sleep(0.05)
        waiting = [asyncio.create_task(as_job(2, slow, 0.05, log, "warm")), asyncio.create_task(as_job(0, slow, 0.05, log, "person"))]
        await asyncio.gather(*holders, *waiting)

    run_with_pool(scenario())
    started = dict(log)
    assert started["person:start"] <= started["warm:start"]
