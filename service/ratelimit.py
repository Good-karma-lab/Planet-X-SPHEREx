"""Token buckets per client (docs/plan/05-architecture.md 5.13). Only starting new work is charged; polling never is."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field

from fastapi import Request


def client_id(request: Request, salt: str) -> str:
    """A salted hash of the client address (Cloudflare's header when present); the address itself is never stored."""
    ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "unknown")
    return hashlib.sha256((salt + ip).encode()).hexdigest()[:16]


@dataclass
class Buckets:
    capacity: float
    per_second: float
    _state: dict[str, tuple[float, float]] = field(default_factory=dict)

    def take(self, who: str, cost: float = 1.0) -> float:
        """0 when allowed, else the seconds to wait."""
        now = time.monotonic()
        tokens, t = self._state.get(who, (self.capacity, now))
        tokens = min(self.capacity, tokens + (now - t) * self.per_second)
        if tokens >= cost:
            self._state[who] = (tokens - cost, now)
            return 0.0
        self._state[who] = (tokens, now)
        return (cost - tokens) / self.per_second
