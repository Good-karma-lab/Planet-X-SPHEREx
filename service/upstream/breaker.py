"""A circuit breaker per upstream host (docs/plan/03-resources.md).

Closed: calls go through. Open (after `threshold` failures within `window_s`, or `consecutive` failures in a row): calls are refused for
`open_s`, doubled after a failed probe up to `max_factor` times. Half-open: one probe at a time decides.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


class BreakerOpenError(RuntimeError):
    def __init__(self, name: str, retry_after_s: float) -> None:
        super().__init__(f"{name} is not answering; trying again in {retry_after_s:.0f} s")
        self.name = name
        self.retry_after_s = retry_after_s


@dataclass
class Breaker:
    name: str
    threshold: int = 5
    window_s: float = 60.0
    consecutive: int = 3
    open_s: float = 30.0
    max_factor: int = 8
    clock: object = field(default=time.monotonic, repr=False)
    _failures: deque[float] = field(default_factory=deque, repr=False)
    _in_a_row: int = 0
    _opened_at: float | None = None
    _factor: int = 1
    _probing: bool = False

    def _now(self) -> float:
        return float(self.clock())  # type: ignore[operator]

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        return "half-open" if self._now() - self._opened_at >= self.open_s * self._factor else "open"

    def before(self) -> None:
        """Call before a request; raises BreakerOpenError when the request must not be made."""
        st = self.state
        if st == "open":
            assert self._opened_at is not None
            raise BreakerOpenError(self.name, self.open_s * self._factor - (self._now() - self._opened_at))
        if st == "half-open":
            if self._probing:
                raise BreakerOpenError(self.name, 1.0)
            self._probing = True

    def success(self) -> None:
        self._in_a_row = 0
        self._failures.clear()
        self._opened_at = None
        self._factor = 1
        self._probing = False

    def failure(self) -> None:
        now = self._now()
        if self._probing:
            self._probing = False
            self._factor = min(self._factor * 2, self.max_factor)
            self._opened_at = now
            return
        self._in_a_row += 1
        self._failures.append(now)
        while self._failures and now - self._failures[0] > self.window_s:
            self._failures.popleft()
        if self._in_a_row >= self.consecutive or len(self._failures) >= self.threshold:
            self._opened_at = now

    def snapshot(self) -> dict[str, object]:
        return {"state": self.state, "failures_in_window": len(self._failures), "factor": self._factor}
