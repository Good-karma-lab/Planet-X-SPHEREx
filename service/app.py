"""HTTP API v1 (docs/plan/05-architecture.md, 5.8)."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

from service import __version__
from service.config import Config, load

SERVICE_NAME = "spherex-sky"
API_PREFIX = "/api/v1"


def create_app(config: Config | None = None) -> FastAPI:
    cfg = config or load()
    app = FastAPI(
        title="SPHEREx Sky API",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=f"{API_PREFIX}/openapi.json",
    )
    started = time.monotonic()
    started_at = datetime.now(UTC).isoformat(timespec="seconds")

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        response: Response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.get(f"{API_PREFIX}/health")
    def health() -> dict[str, Any]:
        """Cheap liveness and state. Fields are added as the parts they describe are built."""
        return {
            "ok": True,
            "service": SERVICE_NAME,
            "version": __version__,
            "started_at": started_at,
            "uptime_s": round(time.monotonic() - started, 1),
            "product_versions": {},
        }

    if cfg.serve_web and cfg.web_dist.is_dir():
        app.mount("/", StaticFiles(directory=cfg.web_dist, html=True), name="web")

    return app


app = create_app()
