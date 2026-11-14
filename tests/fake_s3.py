"""An in-memory S3 for tests: HEAD and ranged GET over httpx.MockTransport, with request counting and injected failures."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from service.reader.s3 import S3


@dataclass
class FakeBucket:
    objects: dict[str, bytes] = field(default_factory=dict)
    fail_next: list[int] = field(default_factory=list)  # status codes to answer before serving normally
    requests: list[tuple[str, str, str | None]] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        key = request.url.path.lstrip("/")
        rng = request.headers.get("range")
        self.requests.append((request.method, key, rng))
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0))
        data = self.objects.get(key)
        if data is None:
            return httpx.Response(404)
        if request.method == "HEAD":
            return httpx.Response(200, headers={"content-length": str(len(data))})
        if rng:
            a, b = rng.removeprefix("bytes=").split("-")
            part = data[int(a) : int(b) + 1]
            return httpx.Response(206, content=part, headers={"content-length": str(len(part))})
        return httpx.Response(200, content=data)

    def s3(self) -> S3:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handler), base_url="https://bucket.test")
        return S3(base_url="https://bucket.test/", client=client, retries=1)
