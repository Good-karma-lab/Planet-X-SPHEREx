"""JPL requests take turns: one at a time, spaced, and a 429 holds the next ones back."""

import asyncio
import time

from service.upstream import jpl


class _R:
    def __init__(self, status: int, retry: str | None = None) -> None:
        self.status_code = status
        self.headers = {"Retry-After": retry} if retry else {}


def test_requests_take_turns_and_a_429_holds_the_next_back() -> None:
    jpl.GAP_S = 0.05
    jpl._next_at.clear()
    inside = 0
    peak = 0

    async def req(status: int = 200, retry: str | None = None) -> _R:
        nonlocal inside, peak
        inside += 1
        peak = max(peak, inside)
        await asyncio.sleep(0.01)
        inside -= 1
        return _R(status, retry)

    async def go() -> float:
        await asyncio.gather(*(jpl.turn(req) for _ in range(4)))
        await jpl.turn(lambda: req(429, "0.3"))
        t0 = time.monotonic()
        await jpl.turn(req)
        return time.monotonic() - t0

    held = asyncio.run(go())
    assert peak == 1 and held >= 0.25
    jpl._next_at.clear()


def test_small_body_identification_has_its_own_slower_lane() -> None:
    jpl.SBIDENT_GAP_S = 0.3
    jpl.GAP_S = 0.0
    jpl._next_at.clear()

    async def req() -> _R:
        return _R(200)

    async def go() -> tuple[float, float]:
        await jpl.turn(req, lane="sbident")
        t0 = time.monotonic()
        await jpl.turn(req)  # another lane: not held by the identification's spacing
        other = time.monotonic() - t0
        t1 = time.monotonic()
        await jpl.turn(req, lane="sbident")
        return other, time.monotonic() - t1

    other, same = asyncio.run(go())
    assert other < 0.1 and same >= 0.25
