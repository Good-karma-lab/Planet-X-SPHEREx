"""Every request this service makes to JPL's SSD APIs (Small-Body Identification, Horizons) takes its turn here: one at a time, at least
GAP_S apart, and after a 429 none until JPL's Retry-After (or COOL_S) has passed. JPL limits each address; several parts of the service
asking at once (the films' known bodies, Findings, the Find Planet X sweep, the comet and asteroid lists) got 429 after 429.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

GAP_S = 2.0
COOL_S = 60.0

_locks: dict[int, asyncio.Lock] = {}  # one per event loop (the service has one; tests start a loop per case)
_next_at = 0.0


def _the_lock() -> asyncio.Lock:
    loop = id(asyncio.get_running_loop())
    lock = _locks.get(loop)
    if lock is None:
        if len(_locks) > 8:
            _locks.clear()
        lock = _locks[loop] = asyncio.Lock()
    return lock


async def turn[R](request: Callable[[], Awaitable[R]]) -> R:
    """Run one JPL request in its turn; a 429 answer holds every later request back for the time JPL asks."""
    global _next_at
    async with _the_lock():
        wait = _next_at - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            r = await request()
        finally:
            _next_at = time.monotonic() + GAP_S
        status = getattr(r, "status_code", None)
        if status == 429:
            _next_at = time.monotonic() + _retry_after(r)
        return r


def _retry_after(r: Any) -> float:
    try:
        return max(GAP_S, min(600.0, float(r.headers.get("Retry-After", COOL_S))))
    except (TypeError, ValueError):
        return COOL_S
