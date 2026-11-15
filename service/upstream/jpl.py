"""Every request this service makes to JPL's SSD APIs (Small-Body Identification, Horizons) takes its turn here: one at a time per lane,
at least the lane's gap apart, and after a 429 none in that lane until JPL's Retry-After (or COOL_S) has passed. JPL limits each address;
several parts of the service asking at once (the films' known bodies, Findings, the Find Planet X sweep, the comet and asteroid lists)
got 429 after 429. Small-Body Identification allows 20 requests in 5 minutes, so its lane keeps one request every 15.5 s.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

GAP_S = 2.0  # Horizons, the small-body database, the comet and asteroid lists
SBIDENT_GAP_S = 15.5  # Small-Body Identification: 20 requests per 5 minutes per address
COOL_S = 60.0

_locks: dict[tuple[int, str], asyncio.Lock] = {}  # one per event loop and lane (the service has one loop; tests start one per case)
_next_at: dict[str, float] = {}


def _gap(lane: str) -> float:
    return SBIDENT_GAP_S if lane == "sbident" else GAP_S


def _the_lock(lane: str) -> asyncio.Lock:
    k = (id(asyncio.get_running_loop()), lane)
    lock = _locks.get(k)
    if lock is None:
        if len(_locks) > 16:
            _locks.clear()
        lock = _locks[k] = asyncio.Lock()
    return lock


async def turn[R](request: Callable[[], Awaitable[R]], lane: str = "ssd") -> R:
    """Run one JPL request in its turn in its lane ("sbident" for Small-Body Identification); a 429 holds the lane back for the time
    JPL asks."""
    async with _the_lock(lane):
        wait = _next_at.get(lane, 0.0) - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            r = await request()
        finally:
            _next_at[lane] = time.monotonic() + _gap(lane)
        if getattr(r, "status_code", None) == 429:
            _next_at[lane] = time.monotonic() + _retry_after(r, lane)
        return r


def _retry_after(r: Any, lane: str = "ssd") -> float:
    try:
        return max(_gap(lane), min(600.0, float(r.headers.get("Retry-After", COOL_S))))
    except (TypeError, ValueError):
        return COOL_S
