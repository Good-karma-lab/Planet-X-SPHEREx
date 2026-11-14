"""Byte-range reads from the public S3 bucket `nasa-irsa-spherex` (no account, no charge to read)."""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field

import httpx

from service.upstream.breaker import Breaker

BUCKET_URL = "https://nasa-irsa-spherex.s3.amazonaws.com/"
USER_AGENT = "spherex-sky/0.1 (+https://github.com/Good-karma-lab/Planet-X-SPHEREx)"
STALL_S = 40.0  # a read that stalls this long is dropped (counted as missing by the caller)


class ReadError(RuntimeError):
    pass


@dataclass
class ReadStats:
    requests: int = 0
    bytes: int = 0
    retries: int = 0
    failures: int = 0


@dataclass
class S3:
    """Range reads with one retry, a concurrency limit and a breaker. `client` may be given (tests use a mock transport)."""

    base_url: str = BUCKET_URL
    max_in_flight: int = 64
    retries: int = 1
    client: httpx.AsyncClient | None = None
    breaker: Breaker = field(default_factory=lambda: Breaker("s3", threshold=12, consecutive=8))
    stats: ReadStats = field(default_factory=ReadStats)
    _sem: asyncio.Semaphore | None = field(default=None, repr=False)

    def _client(self) -> httpx.AsyncClient:
        if self.client is None:
            self.client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=10.0, read=STALL_S, write=10.0, pool=STALL_S),
                limits=httpx.Limits(max_connections=self.max_in_flight, max_keepalive_connections=self.max_in_flight),
                headers={"User-Agent": USER_AGENT},
            )
        return self.client

    def _semaphore(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.max_in_flight)
        return self._sem

    async def close(self) -> None:
        if self.client is not None:
            await self.client.aclose()
            self.client = None

    async def _request(self, method: str, key: str, headers: dict[str, str]) -> httpx.Response:
        url = self.base_url + key
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            self.breaker.before()
            try:
                async with self._semaphore():
                    r = await self._client().request(method, url, headers=headers)
                if r.status_code in (200, 206):
                    self.breaker.success()
                    return r
                if r.status_code == 404:
                    self.breaker.success()
                    raise ReadError(f"{key}: not in the bucket (404)")
                last = ReadError(f"{key}: S3 answered {r.status_code}")
            except httpx.HTTPError as e:
                last = ReadError(f"{key}: {type(e).__name__}")
            self.breaker.failure()
            self.stats.failures += 1
            if attempt < self.retries:
                self.stats.retries += 1
                await asyncio.sleep(0.3 * (attempt + 1) + random.random() * 0.2)
        assert last is not None
        raise last

    async def size(self, key: str) -> int:
        r = await self._request("HEAD", key, {})
        return int(r.headers["content-length"])

    async def get_range(self, key: str, start: int, end: int) -> bytes:
        """Bytes [start, end) of the object."""
        if end <= start:
            return b""
        r = await self._request("GET", key, {"Range": f"bytes={start}-{end - 1}"})
        data = r.content
        if r.status_code == 200:  # a server that ignores Range
            data = data[start:end]
        if len(data) != end - start:
            raise ReadError(f"{key}: asked for {end - start} bytes, got {len(data)}")
        self.stats.requests += 1
        self.stats.bytes += len(data)
        return data
