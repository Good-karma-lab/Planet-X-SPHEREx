"""A worker that dies breaks the pool for good: the gate replaces it, so the calls after it (every field) still run."""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

import pytest

from service import cpu


def die() -> int:
    os._exit(1)


def square(x: int) -> int:
    return x * x


def test_a_broken_pool_is_replaced_and_the_next_calls_run() -> None:
    made: list[ProcessPoolExecutor] = []

    def new_pool() -> ProcessPoolExecutor:
        made.append(ProcessPoolExecutor(2))
        return made[-1]

    cpu.configure(new_pool(), 2, new_pool)
    try:

        async def go() -> list[int]:
            with pytest.raises(BrokenProcessPool):
                await cpu.run(die)  # it dies again in the new pool: its own call fails, once retried
            return await asyncio.gather(*(cpu.run(square, k) for k in range(4)))

        assert asyncio.run(go()) == [0, 1, 4, 9]
        assert cpu.renewed >= 1 and len(made) >= 2
    finally:
        cpu.configure(None, 0)
        for p in made:
            p.shutdown(wait=False, cancel_futures=True)
