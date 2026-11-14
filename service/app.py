"""HTTP API v1 (docs/plan/05-architecture.md, 5.8)."""

from __future__ import annotations

import asyncio
import os
import secrets
import time
from collections.abc import AsyncIterator
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from indexer.store import Index
from service import __version__
from service.config import Config, load
from service.jobs.scheduler import Priority, Scheduler
from service.products.store import Store, product_key
from service.products.versions import VERSIONS
from service.ratelimit import Buckets, client_id
from service.reader.calib import WavelengthMaps
from service.reader.files import FileReader
from service.reader.s3 import S3
from service.warm import WarmField, WarmRunner, warm_set
from service.workers import fieldjob, filmjob

SERVICE_NAME = "spherex-sky"
API_PREFIX = "/api/v1"


class State:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.started = time.monotonic()
        self.started_at = datetime.now(UTC).isoformat(timespec="seconds")
        self.salt = secrets.token_hex(8)
        self.new_work = Buckets(capacity=60, per_second=60 / 600)  # 60 new jobs per 10 minutes per client
        self.index: Index | None = None
        self.store: Store | None = None
        self.files: FileReader | None = None
        self.wavelengths: WavelengthMaps | None = None
        self.s3: S3 | None = None
        self.scheduler: Scheduler | None = None
        self.executor: ProcessPoolExecutor | None = None
        self.warm: WarmRunner | None = None


def create_app(config: Config | None = None, scheduler: Scheduler | None = None, s3: S3 | None = None) -> FastAPI:
    cfg = config or load()
    st = State(cfg)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        st.store = Store(cfg.store_dir)
        st.store.purge_stale()
        index_path = cfg.store_dir / "index" / "exposures.sqlite"
        if index_path.is_file():
            st.index = Index(index_path, readonly=True)
        st.s3 = s3 or S3()
        st.files = FileReader(st.s3, cfg.store_dir)
        st.wavelengths = WavelengthMaps(st.s3, cfg.store_dir)
        if scheduler is None:
            st.executor = ProcessPoolExecutor(max(1, int(os.environ.get("SKY_WORKERS", os.cpu_count() or 2))))
            st.scheduler = Scheduler(executor=st.executor)
        else:
            st.scheduler = scheduler
        st.scheduler.start()
        warm_task = None
        if os.environ.get("SKY_WARM", "1") == "1" and st.index is not None:
            st.warm = WarmRunner(warm_set())
            warm_task = asyncio.get_running_loop().create_task(warm_loop())
        yield
        if warm_task is not None:
            warm_task.cancel()
        await st.scheduler.stop()
        await st.s3.close()
        if st.executor is not None:
            st.executor.shutdown(cancel_futures=True)
        st.store.close()
        if st.index is not None:
            st.index.close()

    def submit_field(params: dict[str, Any], priority: Priority) -> tuple[str, Any]:
        assert st.index is not None and st.store is not None and st.scheduler is not None and st.files is not None
        key = product_key(fieldjob.KIND, params, extra=(fieldjob.exposure_signature(st.index, params),))
        if st.store.is_complete(key):
            return key, None
        index, files, store, wl = st.index, st.files, st.store, st.wavelengths

        async def fn(ctx: Any) -> dict[str, Any]:
            return await fieldjob.run_field(ctx, index, files, store, params, wl)

        return key, st.scheduler.submit(fieldjob.KIND, key, params, fn, priority, "heavy", fieldjob.memory_estimate_mb(params["fov"]))

    async def ensure_warm(f: WarmField) -> bool:
        params = fieldjob.canonical(f.ra, f.dec, f.fov)
        key, job = submit_field(params, Priority.P2)
        while job is not None and job.state not in ("done", "error", "cancelled"):
            await asyncio.sleep(2.0)
        assert st.store is not None
        if st.store.is_complete(key):
            st.store.pin(key)
            return True
        if job is not None and job.error:
            raise RuntimeError(job.error)
        return False

    async def warm_loop() -> None:
        assert st.warm is not None
        while True:
            await st.warm.run(ensure_warm)
            await asyncio.sleep(60.0 if st.warm.state != "done" else 600.0)

    app = FastAPI(
        title="SPHEREx Sky API",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=f"{API_PREFIX}/openapi.json",
        lifespan=lifespan,
    )
    app.state.sky = st

    @app.middleware("http")
    async def headers_and_people(request: Request, call_next: Any) -> Response:
        if request.url.path.startswith("/api/") and request.url.path != f"{API_PREFIX}/health" and st.scheduler is not None:
            st.scheduler.saw_person()
        response: Response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.get(f"{API_PREFIX}/health")
    def health() -> dict[str, Any]:
        """Cheap liveness and state."""
        return {
            "ok": True,
            "service": SERVICE_NAME,
            "version": __version__,
            "started_at": st.started_at,
            "uptime_s": round(time.monotonic() - st.started, 1),
            "product_versions": dict(VERSIONS),
            "index": None if st.index is None else {"files": st.index.count(), **st.index.meta()},
            "jobs": st.scheduler.snapshot() if st.scheduler else None,
            "warm": st.warm.snapshot() if st.warm else None,
            "s3": None if st.s3 is None else {**st.s3.stats.__dict__, "breaker": st.s3.breaker.snapshot()},
        }

    @app.get(f"{API_PREFIX}/fields")
    def field(
        request: Request, ra: float = Query(..., ge=0, lt=360), dec: float = Query(..., ge=-90, le=90), fov: float = Query(5, gt=0, le=60)
    ) -> Any:
        """The field at (ra, dec): its key and either the finished product or the job that builds it (create or attach)."""
        if st.index is None or st.store is None or st.scheduler is None or st.files is None:
            raise HTTPException(503, "the exposure index is not loaded")
        params = fieldjob.canonical(ra, dec, fov)
        key = product_key(fieldjob.KIND, params, extra=(fieldjob.exposure_signature(st.index, params),))
        if st.store.is_complete(key):
            return {"key": key, "state": "done", "params": params, "meta": st.store.read_meta(fieldjob.KIND, key)}
        if key not in st.scheduler.by_key or st.scheduler.jobs[st.scheduler.by_key[key]].state in ("error", "cancelled"):
            wait = st.new_work.take(client_id(request, st.salt))
            if wait > 0:
                return JSONResponse(
                    {"error": "too many new fields from this address; try again shortly"}, 429, {"Retry-After": f"{wait:.0f}"}
                )
        _, job = submit_field(params, Priority.P0)
        if job is None:
            return {"key": key, "state": "done", "params": params, "meta": st.store.read_meta(fieldjob.KIND, key)}
        return {"key": key, "params": params, "job": job.view()}

    @app.get(f"{API_PREFIX}/film")
    def film(
        request: Request,
        ra: float = Query(..., ge=0, lt=360),
        dec: float = Query(..., ge=-90, le=90),
        fov: float = Query(20, gt=0, le=60),
        mjd: float | None = Query(None, ge=60000, le=70000),
    ) -> Any:
        """The film of one pass at (ra, dec): the finished product or the job that makes it (create or attach)."""
        if st.index is None or st.store is None or st.scheduler is None or st.files is None:
            raise HTTPException(503, "the exposure index is not loaded")
        params = filmjob.film_params(ra, dec, fov, mjd)
        exps, _ = filmjob.pass_exposures(st.index, params)
        if not exps:
            return {"state": "none", "params": params, "note": "no exposures of detectors D1–D3 here"}
        key = product_key(filmjob.KIND, params, extra=(filmjob.signature(exps),))
        if st.store.is_complete(key):
            return {"key": key, "state": "done", "params": params, "meta": st.store.read_meta(filmjob.KIND, key)}
        if key not in st.scheduler.by_key:
            wait = st.new_work.take(client_id(request, st.salt))
            if wait > 0:
                return JSONResponse(
                    {"error": "too many new jobs from this address; try again shortly"}, 429, {"Retry-After": f"{wait:.0f}"}
                )
        index, files, store, wl = st.index, st.files, st.store, st.wavelengths

        async def fn(ctx: Any) -> dict[str, Any]:
            return await filmjob.run_film(ctx, index, files, store, params, wl)

        job = st.scheduler.submit(filmjob.KIND, key, params, fn, Priority.P0, "heavy", 1500)
        return {"key": key, "params": params, "job": job.view()}

    @app.get(f"{API_PREFIX}/film/{{key}}/parts/{{name}}")
    def film_part(key: str, name: str) -> Response:
        if st.store is None or not key.isalnum() or name not in ("film", "film.sxp", "meta.json"):
            raise HTTPException(400)
        data = st.store.read_part(filmjob.KIND, key, "film.sxp" if name == "film" else name)
        if data is None:
            raise HTTPException(404, "part not ready or unknown")
        return Response(
            data,
            media_type="application/octet-stream",
            headers={"Cache-Control": "public, max-age=31536000, immutable" if st.store.is_complete(key) else "no-store"},
        )

    @app.get(f"{API_PREFIX}/jobs/{{job_id}}")
    def job(job_id: str) -> Any:
        if st.scheduler is None:
            raise HTTPException(503)
        j = st.scheduler.poll(job_id)
        if j is None:
            raise HTTPException(404, "no such job (the service may have restarted: ask for the product again)")
        return j.view()

    @app.get(f"{API_PREFIX}/fields/{{key}}/parts/{{name}}")
    def part(key: str, name: str) -> Response:
        if st.store is None or not key.isalnum() or not all(c.isalnum() or c in "_." for c in name):
            raise HTTPException(400)
        data = st.store.read_part(fieldjob.KIND, key, name if name.endswith((".sxp", ".json")) else f"{name}.sxp")
        if data is None:
            raise HTTPException(404, "part not ready or unknown")
        immutable = st.store.is_complete(key)
        return Response(
            data,
            media_type="application/json" if name.endswith(".json") else "application/octet-stream",
            headers={"Cache-Control": "public, max-age=31536000, immutable" if immutable else "no-store"},
        )

    if cfg.serve_web and cfg.web_dist.is_dir():
        app.mount("/", StaticFiles(directory=cfg.web_dist, html=True), name="web")

    return app


app = create_app()
