"""HTTP API v1 (docs/plan/05-architecture.md, 5.8)."""

from __future__ import annotations

import asyncio
import json
import multiprocessing
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
from service import __version__, cpu
from service.anomalies import Anomalies
from service.bodies import KnownBodies
from service.catalogue_check import check as catalogue_check
from service.config import Config, load
from service.coverage import coverage_map
from service.jobs.scheduler import Priority, Scheduler
from service.lightcurves import neowise as lc_neowise
from service.lightcurves import ztf as lc_ztf
from service.movers import Movers
from service.products.store import Store, product_key
from service.products.versions import VERSIONS
from service.quicklook import QuickLook
from service.ratelimit import Buckets, client_id
from service.reader.calib import WavelengthMaps
from service.reader.files import FileReader
from service.reader.s3 import S3
from service.red import find_red_async
from service.spectrum import Spectra, place_at, spectrum_params
from service.upstream.hips import HipsTiles
from service.warm import WarmField, WarmRunner, warm_set
from service.workers import fieldjob, filmjob

SERVICE_NAME = "spherex-sky"
API_PREFIX = "/api/v1"
# a second instance that takes over while the main one restarts (deploy/push.sh): it builds nothing new
STANDBY = os.environ.get("SKY_STANDBY", "0") == "1"


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
        self.index_health: dict[str, Any] | None = None
        self.quicklook: QuickLook | None = None
        self.warm: WarmRunner | None = None
        self.hips: HipsTiles | None = None
        self.coverage: dict[str, Any] | None = None
        self.spectra: Spectra | None = None
        self.movers: Movers | None = None
        self.anomalies: Anomalies | None = None
        self.bodies: KnownBodies | None = None
        self.lightcurves: dict[tuple[float, float, int, int], dict[str, Any]] = {}
        self.identified: dict[tuple[float, float, int, int], dict[str, Any]] = {}
        self.coverage_lock = asyncio.Lock()


def create_app(config: Config | None = None, scheduler: Scheduler | None = None, s3: S3 | None = None) -> FastAPI:
    cfg = config or load()
    st = State(cfg)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        st.store = Store(cfg.store_dir)
        st.hips = HipsTiles(cfg.store_dir / "hips")
        st.store.purge_stale()
        index_path = cfg.store_dir / "index" / "exposures.sqlite"
        if index_path.is_file():
            st.index = Index(index_path, readonly=True)
        st.s3 = s3 or S3()
        st.files = FileReader(st.s3, cfg.store_dir)
        st.quicklook = QuickLook(st.s3, cfg.store_dir)
        st.wavelengths = WavelengthMaps(st.s3, cfg.store_dir)
        st.movers = Movers(st.store, cfg.store_dir)
        st.bodies = KnownBodies(cfg.store_dir, st.index)
        if st.index is not None:
            st.spectra = Spectra(st.index, st.files, st.wavelengths, cfg.store_dir)
            st.anomalies = Anomalies(st.index, st.files, st.wavelengths, st.store, cfg.store_dir)
        if scheduler is None:
            workers = max(1, int(os.environ.get("SKY_WORKERS", os.cpu_count() or 2)))
            # fresh worker processes (forkserver), not copies of the API process: small, and the memory cap counts only their own work
            st.executor = ProcessPoolExecutor(
                workers,
                mp_context=multiprocessing.get_context("forkserver"),
                initializer=cpu.worker_init,
                initargs=(int(os.environ.get("SKY_WORKER_MEM_MB", "6000")),),
            )
            cpu.configure(st.executor, workers)
            st.scheduler = Scheduler(executor=st.executor)
        else:
            st.scheduler = scheduler
        st.scheduler.start()
        hips = st.hips
        asyncio.get_running_loop().run_in_executor(None, hips.count_tiles)
        warm_task = None
        if os.environ.get("SKY_WARM", "1") == "1" and st.index is not None:
            st.warm = WarmRunner(warm_set())
            warm_task = asyncio.get_running_loop().create_task(warm_loop())
        yield
        if warm_task is not None:
            warm_task.cancel()
        await st.scheduler.stop()
        await st.s3.close()
        await st.hips.close()
        if st.executor is not None:
            # do not wait for a field still being computed in a worker: its job is recomputed (or attached) after the restart
            procs = list(getattr(st.executor, "_processes", {}).values())
            st.executor.shutdown(wait=False, cancel_futures=True)
            for proc in procs:
                proc.terminate()
            cpu.configure(None, 0)
        st.store.close()
        if st.index is not None:
            st.index.close()

    def submit_field(params: dict[str, Any], priority: Priority) -> tuple[str, Any]:
        assert st.index is not None and st.store is not None and st.scheduler is not None and st.files is not None
        key = product_key(fieldjob.KIND, params, extra=(fieldjob.exposure_signature(st.index, params),))
        if st.store.is_complete(key):
            return key, None
        if STANDBY:
            # the standby instance serves while the main one restarts: finished products only; the page asks again in a moment
            raise HTTPException(503, "the sky service is restarting; ask again in a few seconds", headers={"Retry-After": "3"})
        index, files, store, wl = st.index, st.files, st.store, st.wavelengths

        async def fn(ctx: Any) -> dict[str, Any]:
            return await fieldjob.run_field(ctx, index, files, store, params, wl)

        return key, st.scheduler.submit(fieldjob.KIND, key, params, fn, priority, "heavy", fieldjob.memory_estimate_mb(params["fov"]))

    def submit_film(key: str, params: dict[str, Any], priority: Priority) -> Any:
        assert st.index is not None and st.store is not None and st.scheduler is not None and st.files is not None
        index, files, store, wl = st.index, st.files, st.store, st.wavelengths

        async def fn(ctx: Any) -> dict[str, Any]:
            return await filmjob.run_film(ctx, index, files, store, params, wl)

        return st.scheduler.submit(filmjob.KIND, key, params, fn, priority, "heavy", 1500)

    def warm_key(f: WarmField) -> tuple[str, dict[str, Any]] | None:
        """The product key of a warm field or film (None for a film with no exposures)."""
        assert st.index is not None
        if f.film_mjd is not None:
            params = filmjob.film_params(f.ra, f.dec, f.fov, f.film_mjd)
            exps, _ = filmjob.pass_exposures(st.index, params)
            if not exps:
                return None
            return product_key(filmjob.KIND, params, extra=(filmjob.signature(exps),)), params
        params = fieldjob.canonical(f.ra, f.dec, f.fov)
        return product_key(fieldjob.KIND, params, extra=(fieldjob.exposure_signature(st.index, params),)), params

    async def warm_complete(f: WarmField) -> bool:
        assert st.store is not None
        k = await asyncio.to_thread(warm_key, f)
        return k is not None and st.store.is_complete(k[0])

    async def warm_searches(key: str, fov: float) -> None:
        """The searches of a warm field, so that a visitor sees their results at once: movers always, anomalies from 10′ up."""
        if st.movers is not None:
            run = st.movers.get_or_start(key)
            while run.state == "running":
                await asyncio.sleep(2.0)
        if st.anomalies is not None and fov >= 10:
            arun = st.anomalies.get(key) or st.anomalies.start(key)
            while arun.state == "running":
                await asyncio.sleep(5.0)

    async def ensure_warm(f: WarmField) -> bool:
        if f.film_mjd is not None:
            assert st.store is not None and st.scheduler is not None
            k = await asyncio.to_thread(warm_key, f)
            if k is None:
                return True
            fkey, fparams = k
            if not st.store.is_complete(fkey):
                fjob = st.scheduler.jobs.get(st.scheduler.by_key.get(fkey, "")) or submit_film(fkey, fparams, Priority.P2)
                while fjob.state not in ("done", "error", "cancelled"):
                    await asyncio.sleep(2.0)
                if fjob.error:
                    raise RuntimeError(fjob.error)
            if st.store.is_complete(fkey):
                st.store.pin(fkey)
                meta = st.store.read_meta(filmjob.KIND, fkey)
                if st.bodies is not None and meta is not None and st.bodies.cached(fkey) is None:
                    st.bodies.start(fkey, meta)
                return True
            return False
        params = fieldjob.canonical(f.ra, f.dec, f.fov)
        key, job = submit_field(params, Priority.P2)
        while job is not None and job.state not in ("done", "error", "cancelled"):
            await asyncio.sleep(2.0)
        assert st.store is not None
        if st.store.is_complete(key):
            st.store.pin(key)
            await warm_searches(key, f.fov)
            return True
        if job is not None and job.error:
            raise RuntimeError(job.error)
        return False

    async def warm_loop() -> None:
        assert st.warm is not None
        while True:
            await st.warm.run(ensure_warm, warm_complete)
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
        # a person is working when they ask for new work (a field, a film, a spectrum, an anomaly search); looking at the globe or at
        # finished products does not pause the warm set
        path = request.url.path
        if st.scheduler is not None and (
            path in (f"{API_PREFIX}/fields", f"{API_PREFIX}/film", f"{API_PREFIX}/spectrum")
            or (path == f"{API_PREFIX}/anomalies" and request.query_params.get("start") == "1")
        ):
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
            "role": "standby" if STANDBY else "main",
            "version": __version__,
            "started_at": st.started_at,
            "uptime_s": round(time.monotonic() - st.started, 1),
            "product_versions": dict(VERSIONS),
            "index": index_health(),
            "jobs": st.scheduler.snapshot() if st.scheduler else None,
            "warm": st.warm.snapshot() if st.warm else None,
            "s3": None if st.s3 is None else {**st.s3.stats.__dict__, "breaker": st.s3.breaker.snapshot()},
            "cpu": cpu.stats(),
            "tiles": None if st.hips is None else {"cached": st.hips.count, "fetched": st.hips.fetched, "failed": st.hips.failed},
        }

    def index_health() -> dict[str, Any] | None:
        """The index's size and dates, read once (a count of 1.6 million rows is not free)."""
        if st.index is None:
            return None
        if st.index_health is None:
            meta: dict[str, Any] = dict(st.index.meta())
            rec = meta.pop("reconcile", None)
            try:
                meta["reconciled_at"] = json.loads(rec)["at"] if rec else None
            except (ValueError, KeyError, TypeError):
                meta["reconciled_at"] = None
            st.index_health = {"files": st.index.count(), **meta}
        return st.index_health

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
        job = submit_film(key, params, Priority.P0)
        return {"key": key, "params": params, "job": job.view()}

    @app.get(f"{API_PREFIX}/film/{{key}}/bodies")
    async def film_bodies(key: str) -> Any:
        """Known solar-system bodies predicted in a finished film: their place on every frame (JPL sb_ident + Horizons)."""
        if st.store is None or st.bodies is None or not key.isalnum():
            raise HTTPException(400)
        hit = st.bodies.cached(key)
        if hit is not None:
            return hit
        meta = st.store.read_meta(filmjob.KIND, key)
        if meta is None:
            return {"state": "waiting", "bodies": []}
        st.bodies.start(key, meta)
        return st.bodies.view(key)

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

    @app.get(f"{API_PREFIX}/lightcurves")
    async def lightcurves(
        ra: float = Query(..., ge=0, lt=360),
        dec: float = Query(..., ge=-90, le=90),
        pmra: float = Query(0.0, ge=-20000, le=20000),
        pmdec: float = Query(0.0, ge=-20000, le=20000),
    ) -> Any:
        """ZTF and NEOWISE light curves of a place (IRSA), each part ok / none / unavailable."""
        if st.spectra is None:
            raise HTTPException(503, "the exposure index is not loaded")
        key = (round(ra, 5), round(dec, 5), round(pmra), round(pmdec))
        hit = st.lightcurves.get(key)
        if hit is not None:
            return hit
        params = spectrum_params(ra, dec, pmra, pmdec)
        z, nw = await asyncio.gather(lc_ztf(params), lc_neowise(params))
        out = {"ztf": z, "neowise": nw}
        if z["state"] != "unavailable" and nw["state"] != "unavailable":
            st.lightcurves[key] = out
            if len(st.lightcurves) > 2000:
                st.lightcurves.pop(next(iter(st.lightcurves)))
        return out

    @app.get(f"{API_PREFIX}/identify")
    async def identify(
        ra: float = Query(..., ge=0, lt=360),
        dec: float = Query(..., ge=-90, le=90),
        pmra: float = Query(0.0, ge=-20000, le=20000),
        pmdec: float = Query(0.0, ge=-20000, le=20000),
    ) -> Any:
        """What SIMBAD and Gaia DR3 call a place (each part: ok / none / unavailable)."""
        if st.anomalies is None:
            raise HTTPException(503, "the exposure index is not loaded")
        key = (round(ra, 5), round(dec, 5), round(pmra), round(pmdec))
        hit = st.identified.get(key)
        if hit is not None:
            return hit
        out = await st.anomalies.identify(ra, dec, pmra, pmdec)
        if all(v.get("state") != "unavailable" for v in out.values()):
            st.identified[key] = out
            if len(st.identified) > 5000:
                st.identified.pop(next(iter(st.identified)))
        return out

    @app.get(f"{API_PREFIX}/anomalies")
    async def anomalies_of(
        request: Request,
        ra: float = Query(..., ge=0, lt=360),
        dec: float = Query(..., ge=-90, le=90),
        fov: float = Query(5, gt=0, le=60),
        start: int = Query(0, ge=0, le=1),
    ) -> Any:
        """The spectral anomaly map of a finished field: its state, or (start=1) the search started."""
        if st.index is None or st.store is None or st.anomalies is None:
            raise HTTPException(503, "the exposure index is not loaded")
        params = fieldjob.canonical(ra, dec, fov)
        key = product_key(fieldjob.KIND, params, extra=(fieldjob.exposure_signature(st.index, params),))
        if not st.store.is_complete(key):
            return {"state": "waiting", "error": "", "result": None}
        run = st.anomalies.get(key)
        if run is None:
            if not start:
                return {"state": "idle", "error": "", "result": None}
            wait = st.new_work.take(client_id(request, st.salt))
            if wait > 0:
                return JSONResponse(
                    {"error": "too many new searches from this address; try again shortly"}, 429, {"Retry-After": f"{wait:.0f}"}
                )
            if STANDBY:
                raise HTTPException(503, "the sky service is restarting; ask again in a few seconds", headers={"Retry-After": "3"})
            run = st.anomalies.start(key)
        return st.anomalies.view(run)

    @app.get(f"{API_PREFIX}/red")
    async def red_of(
        ra: float = Query(..., ge=0, lt=360), dec: float = Query(..., ge=-90, le=90), fov: float = Query(5, gt=0, le=60)
    ) -> Any:
        """Red sources of a finished field: bright at 3.8–5 µm, faint at 1.1–1.6 µm (cold brown dwarfs and the like)."""
        if st.index is None or st.store is None:
            raise HTTPException(503, "the exposure index is not loaded")
        params = fieldjob.canonical(ra, dec, fov)
        key = product_key(fieldjob.KIND, params, extra=(fieldjob.exposure_signature(st.index, params),))
        if not st.store.is_complete(key):
            return {"state": "waiting", "result": None}
        try:
            result = await find_red_async(st.store, key, cfg.store_dir / "red")
        except ValueError as e:
            return {"state": "failed", "error": str(e), "result": None}
        return {"state": "done", "result": result}

    @app.get(f"{API_PREFIX}/movers")
    async def movers_of(
        ra: float = Query(..., ge=0, lt=360), dec: float = Query(..., ge=-90, le=90), fov: float = Query(5, gt=0, le=60)
    ) -> Any:
        """The search between passes of a field (once the field is finished) and the Gaia check of its tracks."""
        if st.index is None or st.store is None or st.movers is None:
            raise HTTPException(503, "the exposure index is not loaded")
        params = fieldjob.canonical(ra, dec, fov)
        key = product_key(fieldjob.KIND, params, extra=(fieldjob.exposure_signature(st.index, params),))
        if not st.store.is_complete(key):
            return {"state": "waiting", "error": "", "field": key}
        return st.movers.view(st.movers.get_or_start(key))

    @app.get(f"{API_PREFIX}/spectrum/catalogue")
    async def spectrum_catalogue(
        ra: float = Query(..., ge=0, lt=360),
        dec: float = Query(..., ge=-90, le=90),
        pmra: float = Query(0.0, ge=-20000, le=20000),
        pmdec: float = Query(0.0, ge=-20000, le=20000),
        epoch: float = Query(2000.0, ge=1900, le=2100),
    ) -> Any:
        """The spectrum averaged under 2MASS and WISE filters against the catalogue magnitudes ("pending" until it is measured)."""
        if st.spectra is None:
            raise HTTPException(503, "the exposure index is not loaded")
        run = st.spectra.get(spectrum_params(ra, dec, pmra, pmdec, epoch))
        if run is None:
            raise HTTPException(404, "no such spectrum: ask for /api/v1/spectrum first")
        if run.state != "done":
            return {"state": "pending", "rows": [], "notes": []}
        if run.catalogue is None or any("unavailable" in str(r.get("note", "")) for r in run.catalogue["rows"]):
            run.catalogue = await catalogue_check(run.params, run.points, st.spectra.client)
        return run.catalogue

    @app.get(f"{API_PREFIX}/spectrum/stamps")
    async def spectrum_stamps(
        ra: float = Query(..., ge=0, lt=360),
        dec: float = Query(..., ge=-90, le=90),
        pmra: float = Query(0.0, ge=-20000, le=20000),
        pmdec: float = Query(0.0, ge=-20000, le=20000),
        epoch: float = Query(2000.0, ge=1900, le=2100),
        since: int = Query(0, ge=0, alias="from"),
    ) -> Any:
        """The pixels behind every point of a spectrum (the spectrum must have been asked for)."""
        if st.spectra is None:
            raise HTTPException(503, "the exposure index is not loaded")
        run = st.spectra.get(spectrum_params(ra, dec, pmra, pmdec, epoch))
        if run is None:
            raise HTTPException(404, "no such spectrum: ask for /api/v1/spectrum first")
        return run.stamps_view(since)

    @app.get(f"{API_PREFIX}/spectrum/quicklook")
    async def spectrum_quicklook(
        ra: float = Query(..., ge=0, lt=360),
        dec: float = Query(..., ge=-90, le=90),
        pmra: float = Query(0.0, ge=-20000, le=20000),
        pmdec: float = Query(0.0, ge=-20000, le=20000),
        epoch: float = Query(2000.0, ge=1900, le=2100),
    ) -> Any:
        """A quick look at the place's spectrum from the DR1 all-sky spectral cube (byte-range reads, a few seconds), shown until
        the spectrum measured from the exposures arrives. The place is taken at the cube's epoch (2025.9)."""
        if st.quicklook is None:
            raise HTTPException(503)
        params = spectrum_params(ra, dec, pmra, pmdec, epoch)
        qra, qdec = place_at(params, 51544.5 + (2025.9 - 2000.0) * 365.25)
        path = cfg.store_dir / "quicklook" / "spectra" / f"{qra:.5f}_{qdec:+.5f}.json"
        if path.is_file():
            return JSONResponse(json.loads(path.read_text()))
        try:
            out = await st.quicklook.spectrum(qra % 360.0, qdec)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        except Exception as e:  # the bucket did not answer: said, never cached
            raise HTTPException(502, f"the all-sky cube could not be read: {type(e).__name__}") from e
        if all(v is None for v in out["flux"]):
            out["note"] = "no value in the all-sky cube at this pixel (a saturated core or a gap of the map)"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, separators=(",", ":")))
        return out

    @app.get(f"{API_PREFIX}/spectrum")
    async def spectrum(
        request: Request,
        ra: float = Query(..., ge=0, lt=360),
        dec: float = Query(..., ge=-90, le=90),
        pmra: float = Query(0.0, ge=-20000, le=20000),
        pmdec: float = Query(0.0, ge=-20000, le=20000),
        epoch: float = Query(2000.0, ge=1900, le=2100),
        since: int = Query(0, ge=0),
    ) -> Any:
        """The spectrum of a place: the points measured so far (from `since` on); the first request starts the measurement."""
        if st.spectra is None:
            raise HTTPException(503, "the exposure index is not loaded")
        params = spectrum_params(ra, dec, pmra, pmdec, epoch)
        run = st.spectra.get(params)
        if run is None:
            wait = st.new_work.take(client_id(request, st.salt))
            if wait > 0:
                return JSONResponse(
                    {"error": "too many new measurements from this address; try again shortly"}, 429, {"Retry-After": f"{wait:.0f}"}
                )
            run = st.spectra.start(params)
        return run.view(since)

    @app.get(f"{API_PREFIX}/coverage")
    def coverage_here(
        ra: float = Query(..., ge=0, lt=360), dec: float = Query(..., ge=-90, le=90), fov: float = Query(5, gt=0, le=60)
    ) -> dict[str, Any]:
        """How many exposures of the index overlap a field, by release."""
        if st.index is None:
            raise HTTPException(503, "no exposure index")
        found = st.index.overlapping(ra, dec, fov / 60.0 * 0.7072)
        by: dict[str, int] = {}
        for e in found:
            by[e.release] = by.get(e.release, 0) + 1
        return {"ra": ra, "dec": dec, "fov": fov, "n_exposures": len(found), "by_release": by}

    @app.get(f"{API_PREFIX}/coverage/map")
    async def coverage_all() -> Response:
        """Exposure centres per HEALPix cell (nested, order 3) and release: the globe's coverage layer."""
        index_path = cfg.store_dir / "index" / "exposures.sqlite"
        if not index_path.is_file():
            raise HTTPException(503, "no exposure index")
        async with st.coverage_lock:
            if st.coverage is None:
                st.coverage = await asyncio.to_thread(coverage_map, index_path)
        return JSONResponse(st.coverage, headers={"Cache-Control": "public, max-age=3600"})

    @app.get(f"{API_PREFIX}/hips/{{survey}}/{{order}}/{{tile}}")
    async def hips(survey: str, order: int, tile: str) -> Response:
        """A tile of an all-sky survey for the globe's background (kept on disk after the first request)."""
        if st.hips is None:
            raise HTTPException(503)
        try:
            data = await st.hips.aget(survey, order, tile)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:  # the mirror is down or slow: the globe keeps the coarser tiles it has
            raise HTTPException(502, "survey tiles unavailable") from e
        if data is None:
            raise HTTPException(404)
        media = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
        return Response(data, media_type=media, headers={"Cache-Control": "public, max-age=2592000, immutable"})

    if cfg.serve_web and cfg.web_dist.is_dir():
        app.mount("/", StaticFiles(directory=cfg.web_dist, html=True), name="web")

    return app


app = create_app()
