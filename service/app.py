"""HTTP API v1 (docs/plan/05-architecture.md, 5.8)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import multiprocessing
import os
import secrets
import shutil
import time
from collections.abc import AsyncIterator, Callable, Coroutine
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.staticfiles import StaticFiles

from indexer.store import Index
from service import __version__, cpu, exposure_flags, jsonsafe, people_queue, quarters, sweep_runner
from service import grid as grid_mod
from service.accounts import get as accounts_get
from service.accounts import register_accounts
from service.anomalies import Anomalies
from service.bodies import KnownBodies
from service.browndwarfs import BrownDwarfs
from service.catalogue_check import check as catalogue_check
from service.config import Config, load
from service.context import CatalogueContext
from service.coverage import coverage_payload, index_key
from service.findings import Findings
from service.fpx_known import FpxIdentifier
from service.grid import GridDB, import_searched
from service.jobs.scheduler import Priority, Scheduler
from service.jsonsafe import SafeJSONResponse as JSONResponse
from service.known import Known
from service.lightcurves import neowise as lc_neowise
from service.lightcurves import ztf as lc_ztf
from service.loopwatch import LoopWatch
from service.movers import Movers
from service.pairs import Pairs
from service.products.store import Store, product_key
from service.products.versions import VERSIONS
from service.quicklook import QuickLook
from service.ratelimit import Buckets, client_id
from service.reader.calib import WavelengthMaps
from service.reader.files import FileReader
from service.reader.s3 import S3
from service.red import find_red_async
from service.routes_brightness import make_brightness_router
from service.routes_checks import checks_of, make_checks_router
from service.routes_clips import make_clips_router
from service.routes_dwarfs import make_dwarfs_router
from service.routes_export import make_export_router
from service.routes_extras import register as register_extras
from service.routes_findings import make_findings_router
from service.routes_grid import make_grid_router
from service.routes_known import make_known_router
from service.routes_known_field import make_known_field_router
from service.routes_known_field import prewarm as known_prewarm
from service.routes_legal import make_legal_router
from service.routes_pairs import make_pairs_router
from service.routes_planetx import make_planetx_router
from service.routes_search import make_router
from service.routes_searched import make_searched_router
from service.searched import Searched
from service.spectrum import Spectra, place_at, spectrum_params
from service.storage import product_db_path
from service.upstream.hips import HipsTiles
from service.warm import WarmField, WarmRunner, warm_set
from service.workers import fieldjob, filmjob, filmsearchjob

SERVICE_NAME = "spherex-sky"
API_PREFIX = "/api/v1"
# a second instance that takes over while the main one restarts (deploy/push.sh): it builds nothing new
STANDBY = os.environ.get("SKY_STANDBY", "0") == "1"
HEAVY_SLOTS = 6  # field builds at once (17 Nov 2026: 3 before; the builds wait on the archive more than they compute)
MEMORY_BUDGET_MB = 24000  # of the VM's 48 GB
# a local run on a snapshot of the production finds (tools/findings_snapshot.py): Findings only reads its database and the Find Planet X
# identifier does not ask JPL, while every field and search is built as usual (a standby would build nothing new)
FINDINGS_READONLY = os.environ.get("SKY_FINDINGS_READONLY", "0") == "1"
LIGHTCURVES_WAIT_S = 3.0  # IRSA may take a minute for NEOWISE: the light curves route answers within this, the rest `pending`


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
        self.warm_searches: dict[str, Any] = {}
        # the square quarters built ahead (service/quarters.py): set by the tasks router, the stock loop's last step for /health
        self.quarters: Any = None
        self.upcoming_quarters: Any = None
        self.submit_field: Any = None
        self.quarter_stock: dict[str, Any] = {}
        # people's queue for the brown-dwarf search (service/people_queue.py) and its last step for /health
        self.bd_queue: Any = None
        self.sweep: Any = None  # the background sweep of the grid (service/sweep_runner.py)
        self.bd_queue_last: dict[str, Any] = {}
        self.catalogue: CatalogueContext | None = None
        self.known: Known | None = None
        self.pairs: Pairs | None = None
        self.dwarfs: BrownDwarfs | None = None
        self.searched: Searched | None = None
        self.warm: WarmRunner | None = None
        self.hips: HipsTiles | None = None
        self.coverage: tuple[bytes, str, str] | None = None
        self.spectra: Spectra | None = None
        self.movers: Movers | None = None
        self.anomalies: Anomalies | None = None
        self.bodies: KnownBodies | None = None
        self.lightcurves: dict[tuple[Any, ...], dict[str, Any]] = {}  # answered parts: (place, part name)
        self.lc_tasks: dict[tuple[Any, ...], asyncio.Task[dict[str, Any]]] = {}  # parts still being asked of IRSA
        self.identified: dict[tuple[float, float, int, int, str | None], dict[str, Any]] = {}
        self.coverage_lock = asyncio.Lock()
        self.coverage_task: asyncio.Task[None] | None = None
        self.findings: Findings | None = None
        self.fpx_known: FpxIdentifier | None = None
        self.loopwatch: LoopWatch | None = None
        self.grid: GridDB | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.known_warmed: set[str] = set()
        self.grid_import: asyncio.Task[None] | None = None


class _NoAnomalies:
    """Stands in for the anomaly search when there is no exposure index (nothing to read from it)."""

    def key_of(self, field_key: str) -> str:
        return "none"


def create_app(config: Config | None = None, scheduler: Scheduler | None = None, s3: S3 | None = None) -> FastAPI:
    cfg = config or load()
    st = State(cfg)

    async def grid_import_loop() -> None:
        """Marks the grid squares of the searched places: the list kept in data/searched_seed.json (the places searched before the
        grid existed) once, then the service's own list of searched places every ten minutes."""
        seed = Path(__file__).resolve().parent.parent / "data" / "searched_seed.json"
        with contextlib.suppress(Exception):
            if st.grid is not None and seed.is_file():
                await asyncio.to_thread(import_searched, st.grid, json.loads(seed.read_text()).get("fields", []))
        while True:
            await asyncio.sleep(30)
            with contextlib.suppress(Exception):
                if st.searched is not None and st.grid is not None:
                    body = await st.searched.get()
                    await asyncio.to_thread(import_searched, st.grid, body.get("fields", []))
            await asyncio.sleep(570)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        st.loop = asyncio.get_running_loop()
        st.store = Store(cfg.store_dir)
        st.grid = GridDB(product_db_path(cfg.store_dir))
        if not STANDBY:  # the places searched before the grid existed, once (and the service's own searches, every ten minutes)
            st.grid_import = asyncio.get_running_loop().create_task(grid_import_loop())
        st.hips = HipsTiles(cfg.store_dir / "hips")
        st.store.purge_stale()
        index_path = cfg.store_dir / "index" / "exposures.sqlite"
        if index_path.is_file():
            st.index = Index(index_path, readonly=True)
        st.s3 = s3 or S3()
        st.files = FileReader(st.s3, cfg.store_dir)
        st.quicklook = QuickLook(st.s3, cfg.store_dir)
        st.catalogue = CatalogueContext()
        st.known = Known(cfg.store_dir, st.index)
        st.pairs = Pairs(st.store, cfg.store_dir, st.catalogue)
        st.wavelengths = WavelengthMaps(st.s3, cfg.store_dir)
        st.movers = Movers(st.store, cfg.store_dir)
        st.movers.catalogue = st.catalogue
        st.dwarfs = BrownDwarfs(st.store, cfg.store_dir, st.movers, st.catalogue)
        st.bodies = KnownBodies(cfg.store_dir, st.index)
        if st.index is not None:
            st.spectra = Spectra(st.index, st.files, st.wavelengths, cfg.store_dir)
            st.anomalies = Anomalies(st.index, st.files, st.wavelengths, st.store, cfg.store_dir)
            st.anomalies.catalogue = st.catalogue
        if scheduler is None:
            workers = max(1, int(os.environ.get("SKY_WORKERS", os.cpu_count() or 2)))
            # fresh worker processes (forkserver), not copies of the API process: small, and the memory cap counts only their own work
            mem_mb = int(os.environ.get("SKY_WORKER_MEM_MB", "6000"))

            def new_pool(workers: int = workers, mem_mb: int = mem_mb) -> ProcessPoolExecutor:
                return ProcessPoolExecutor(
                    workers, mp_context=multiprocessing.get_context("forkserver"), initializer=cpu.worker_init, initargs=(mem_mb,)
                )

            st.executor = new_pool()
            cpu.configure(st.executor, workers, new_pool)  # a pool a dying worker broke is replaced by a new one
            # heavy jobs at once: a field build mostly waits on the archive, so more run side by side than there are cores for them
            # (6 by default, SKY_HEAVY_SLOTS overrides); their memory together stays within SKY_MEMORY_BUDGET_MB (24 GB by default)
            heavy = int(os.environ.get("SKY_HEAVY_SLOTS", str(HEAVY_SLOTS)))
            budget = int(os.environ.get("SKY_MEMORY_BUDGET_MB", str(MEMORY_BUDGET_MB)))
            st.scheduler = Scheduler(executor=st.executor, slots={"light": 8, "heavy": max(2, heavy)}, memory_budget_mb=budget)
        else:
            st.scheduler = scheduler
        st.scheduler.start()
        st.loopwatch = LoopWatch()
        st.loopwatch.start()
        st.searched = Searched(st.store, cfg.store_dir, st.scheduler, st.movers, st.pairs, st.dwarfs)
        st.searched.prime()
        if not STANDBY:
            store = st.store

            def reopen_unread() -> None:
                """Fields kept by a build whose reader failed (nothing read, a black field) are made again when next asked for."""
                for row in store.listing((fieldjob.KIND,)):
                    if row["complete"] and fieldjob.unread(store.read_meta(fieldjob.KIND, row["key"])):
                        store.reopen(row["key"])

            asyncio.get_running_loop().run_in_executor(None, reopen_unread)
        anomalies = st.anomalies or _NoAnomalies()
        writer = not (STANDBY or FINDINGS_READONLY)
        st.findings = Findings(st.store, cfg.store_dir, st.catalogue, st.movers, st.pairs, st.dwarfs, anomalies, writer=writer)
        st.findings.start()
        st.fpx_known = FpxIdentifier(st.store, cfg.store_dir / "fpx_known")
        if not (STANDBY or FINDINGS_READONLY):  # one process asks JPL (the standby only reads the kept answers): two would double the rate
            st.fpx_known.start()
        checks_of(st).resume()  # check jobs a restart interrupted run again at once, not on the first request
        hips = st.hips
        asyncio.get_running_loop().run_in_executor(None, hips.count_tiles)
        if st.index is not None:
            # the coverage map is ready before the first visitor asks for it (from the disk copy, or computed once)
            async def prime_coverage() -> None:
                with contextlib.suppress(Exception):
                    st.coverage = await asyncio.to_thread(coverage_payload, index_path)

            st.coverage_task = asyncio.get_running_loop().create_task(prime_coverage())
        # the index's size for /health, counted once in a thread (the async health route never counts 1.6 million rows itself)
        asyncio.get_running_loop().run_in_executor(None, index_health)
        # the fields the service has: a request a few arcseconds off one of them is that field (fieldjob.canonical snaps to them)
        for wf in warm_set():
            if wf.film_mjd is None:
                fieldjob.remember(wf.ra, wf.dec, wf.fov, warm=True)
        for row in st.store.listing((fieldjob.KIND,)) if st.store is not None else []:
            pp = row["params"]
            if row["complete"] and {"ra", "dec", "fov"} <= pp.keys():
                fieldjob.remember(float(pp["ra"]), float(pp["dec"]), float(pp["fov"]))
        warm_task = None
        if os.environ.get("SKY_WARM", "1") == "1" and st.index is not None:
            st.warm = WarmRunner(warm_set())
            warm_task = asyncio.get_running_loop().create_task(warm_loop())
        if getattr(st, "quarters", None) is not None:
            st.quarters.bind(asyncio.get_running_loop())
        stock_task = None
        bd_task = None
        sweep_task = None
        flags_task = asyncio.get_running_loop().create_task(exposure_flags_loop())
        background = st.index is not None and not STANDBY
        if os.environ.get("SKY_WARM", "1") == "1" and background and quarters.STOCK > 0:
            stock_task = asyncio.get_running_loop().create_task(quarter_stock_loop())
            st.bd_queue = people_queue.BDQueue(st)
            bd_task = asyncio.get_running_loop().create_task(bd_queue_loop())
        # the background sweep of the grid (service/sweep_runner.py): on wherever the service has its exposure index and is not the
        # standby, unless SKY_SWEEP=0; it gives way to people, the stock, the disk and the archive by itself
        sweep_env = os.environ.get("SKY_SWEEP", "1")
        if background and (sweep_env == "force" or (sweep_env == "1" and os.environ.get("SKY_WARM", "1") == "1")):
            st.sweep = sweep_runner.SweepRunner(st, sweep_searches, sweep_planetx)
            sweep_task = asyncio.get_running_loop().create_task(sweep_loop())
        yield
        if warm_task is not None:
            warm_task.cancel()
        if stock_task is not None:
            stock_task.cancel()
        if bd_task is not None:
            bd_task.cancel()
        if sweep_task is not None:
            sweep_task.cancel()
        flags_task.cancel()
        if st.grid_import is not None:
            st.grid_import.cancel()
        if st.findings is not None:
            await st.findings.stop()
        if st.fpx_known is not None:
            await st.fpx_known.stop()
        if st.loopwatch is not None:
            st.loopwatch.stop()
        await st.scheduler.stop()
        await st.s3.close()
        await st.hips.close()
        await st.catalogue.close()
        ex = cpu.pool() or st.executor
        if ex is not None:
            # do not wait for a field still being computed in a worker: its job is recomputed (or attached) after the restart
            procs = list(getattr(ex, "_processes", {}).values())
            ex.shutdown(wait=False, cancel_futures=True)
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
            if not fieldjob.unread(st.store.read_meta(fieldjob.KIND, key)) or STANDBY:
                return key, None
            st.store.reopen(key)  # kept by a build whose worker pool had broken: no exposure was read, so it is made again
        if STANDBY:
            # the standby instance serves while the main one restarts: finished products only; the page asks again in a moment
            raise HTTPException(503, "the sky service is restarting; ask again in a few seconds", headers={"Retry-After": "3"})
        index, files, store, wl = st.index, st.files, st.store, st.wavelengths

        async def fn(ctx: Any) -> dict[str, Any]:
            return await fieldjob.run_field(ctx, index, files, store, params, wl)

        return key, st.scheduler.submit(fieldjob.KIND, key, params, fn, priority, "heavy", fieldjob.memory_estimate_mb(params["fov"]))

    st.submit_field = submit_field  # the square quarters build ahead with it (service/quarters.py)

    async def exposure_flags_loop() -> None:
        """The exposures people agreed are broken, read every minute (service/exposure_flags.py)."""
        while True:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(exposure_flags.refresh, st)
            await asyncio.sleep(60.0)

    async def bd_queue_loop() -> None:
        """The sources people sent to the brown-dwarf search, looked at one at a time (service/people_queue.py)."""
        await asyncio.sleep(90.0)
        while True:
            try:
                q = st.bd_queue
                if q is not None:
                    waiting = await asyncio.to_thread(q.queue)  # the database is read in a thread
                    said = q.step(waiting)  # on the loop: the scheduler is not thread-safe
                    if said:
                        st.bd_queue_last = {"last": said, "at": time.time(), "waiting": len(waiting), "done": len(q.done)}
            except Exception as e:  # said in /health; the next round tries again
                st.bd_queue_last = {"error": f"{type(e).__name__}: {e}"[:200], "at": time.time()}
            await asyncio.sleep(60.0)

    def warm_ready(qs: Any, ahead: list[tuple[str, str]]) -> None:
        for c, q in ahead:
            qs.ready(c, q)

    async def quarter_stock_loop() -> None:
        """Keeps a few quarters ready for the people who will take a square next (service/quarters.py), one build at a time."""
        await asyncio.sleep(30.0)
        while True:
            try:
                qs, upcoming = getattr(st, "quarters", None), getattr(st, "upcoming_quarters", None)
                if qs is not None and upcoming is not None:
                    qs.bind(asyncio.get_running_loop())
                    order = await asyncio.to_thread(upcoming, accounts_get(st))  # the database is read in a thread
                    # which quarters are built: the exposure index is asked in a thread (asking it for dozens of quarters on the
                    # loop held it for a second); the step below then reads the kept answers
                    await asyncio.to_thread(warm_ready, qs, order[: 2 * quarters.STOCK])
                    started = qs.stock_step(order)  # on the loop: the scheduler is not thread-safe
                    if started:
                        st.quarter_stock = {"last": started, "at": time.time()}
            except Exception as e:  # said in /health; the next round tries again
                st.quarter_stock = {"error": f"{type(e).__name__}: {e}"[:200], "at": time.time()}
            await asyncio.sleep(60.0)

    def submit_film(key: str, params: dict[str, Any], priority: Priority) -> Any:
        assert st.index is not None and st.store is not None and st.scheduler is not None and st.files is not None
        index, files, store, wl = st.index, st.files, st.store, st.wavelengths

        async def fn(ctx: Any) -> dict[str, Any]:
            return await filmjob.run_film(ctx, index, files, store, params, wl)

        return st.scheduler.submit(filmjob.KIND, key, params, fn, priority, "heavy", fieldjob.film_memory_mb(float(params["fov"])))

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

    class _Background:
        priority = Priority.P2  # what service.cpu reads: the warm searches give way to a person's work

    async def give_way() -> None:
        while st.scheduler is not None and st.scheduler.person_waiting():
            await asyncio.sleep(2.0)

    async def warm_field_searches(f: WarmField) -> None:
        """Everything a visitor of a test field asks for, kept on disk: movers, pairs that change in place, red sources, the anomaly
        search (every size), the asteroid search of the middle pass, the spectra of the test target and of every anomaly, and the
        anomalies' passports."""
        assert st.store is not None and st.index is not None
        k = await asyncio.to_thread(warm_key, f)
        if k is None or not st.store.is_complete(k[0]):
            return
        key = k[0]
        await give_way()
        if st.movers is not None:
            mrun = st.movers.get_or_start(key)
            while mrun.state == "running":
                await asyncio.sleep(2.0)
        await give_way()
        if st.pairs is not None:
            prun = st.pairs.get_or_start(key)
            while prun.state == "running":
                await asyncio.sleep(2.0)
        await give_way()
        if st.dwarfs is not None:
            drun = st.dwarfs.get_or_start(key)
            while drun.state == "running":
                await asyncio.sleep(2.0)
        await give_way()
        with contextlib.suppress(Exception):  # a field without enough passes: the visitor's button says why
            await find_red_async(st.store, key, cfg.store_dir / "red")
        await give_way()
        anomalies: list[dict[str, Any]] = []
        if st.anomalies is not None:
            arun = st.anomalies.get(key) or st.anomalies.start(key)
            while arun.state in ("running", "waiting"):
                await asyncio.sleep(5.0)
            anomalies = list((arun.result or {}).get("anomalies", []))
        await give_way()
        await warm_asteroid_search(f, key)
        if st.spectra is None:
            return
        wanted = [
            spectrum_params(float(a["ra"]), float(a["dec"]), float(a.get("pmra") or 0.0), float(a.get("pmdec") or 0.0)) for a in anomalies
        ]
        t = targets_by_label.get(f.label)
        if t is not None:
            wanted.insert(
                0,
                spectrum_params(
                    float(t["ra_deg"]),
                    float(t["dec_deg"]),
                    float(t["pmra_mas_yr"]),
                    float(t["pmdec_mas_yr"]),
                    float(t.get("epoch", 2000.0)),
                ),
            )
        for params in wanted:
            await give_way()
            skey = st.spectra.key_of(params)
            if skey not in st.spectra.runs and (st.spectra.dir / f"{skey}.json").is_file():
                continue  # kept: done (reading the whole spectrum here held the event loop for seconds)
            srun = st.spectra.get(params) or st.spectra.start(params)
            while srun.state == "running":
                await asyncio.sleep(3.0)

    async def sweep_searches(key: str, ce: Any) -> dict[str, Any]:
        """The searches of a sweep square's 20′ field (what a visitor's field gets, without the spectra): movers, pairs, brown
        dwarfs, red sources, anomalies, the asteroid search of the middle pass. Returns the ids of its finds (the ids Findings gives
        them) and what the searches measured, for the grid's bar."""
        from service.findings import find_id
        from service.movers import MOVER_SHIFT_ARCSEC, MOVER_SIGMA

        assert st.store is not None
        store = st.store
        ids: list[str] = []
        bar: dict[str, Any] = {"mover_sigma": MOVER_SIGMA, "mover_shift_arcsec": MOVER_SHIFT_ARCSEC}
        await give_way()
        if st.movers is not None:
            mrun = st.movers.get_or_start(key)
            while mrun.state == "running":
                await asyncio.sleep(2.0)
            res = mrun.result or {}
            tracks = res.get("tracks", [])
            ids += [find_id("mover", float(t["ra_2025_9"]), float(t["dec_2025_9"])) for t in tracks
                    if t.get("ra_2025_9") is not None and t.get("check") != "same"]  # fmt: skip
            bar |= {"known_movers": sum(1 for t in tracks if t.get("check") == "same"), "chance_any": (res.get("chance") or {}).get("any")}
        await give_way()
        if st.pairs is not None:
            prun = st.pairs.get_or_start(key)
            while prun.state == "running":
                await asyncio.sleep(2.0)
        await give_way()
        if st.dwarfs is not None:
            drun = st.dwarfs.get_or_start(key)
            while drun.state == "running":
                await asyncio.sleep(2.0)
            cands = (drun.result or {}).get("candidates", [])
            ids += [find_id("browndwarf", float(x["ra"]), float(x["dec"])) for x in cands if x.get("ra") is not None]
            bar["browndwarfs"] = drun.state
        await give_way()
        with contextlib.suppress(Exception):
            red = await find_red_async(store, key, cfg.store_dir / "red")
            bar["red_sources"] = len((red or {}).get("red") or [])
        await give_way()
        if st.anomalies is not None:
            arun = st.anomalies.get(key) or st.anomalies.start(key)
            while arun.state in ("running", "waiting"):
                await asyncio.sleep(5.0)
            bar["anomalies"] = len((arun.result or {}).get("anomalies", []))
        await give_way()
        with contextlib.suppress(Exception):  # the asteroid search of the middle pass (its film): a failure leaves the rest
            await warm_asteroid_search(WarmField("sweep", float(ce.ra), float(ce.dec), float(grid_mod.FIELD_FOV_ARCMIN)), key)
        return {"find_ids": ids, "bar": bar}

    async def sweep_planetx(ce: Any, key: str) -> dict[str, Any]:
        """Find Planet X on a swept square of the Planet Nine band: the four searches the lab runs by itself (the middle pass,
        30-300 au and 250-2000 au, detectors 1-3 and 4), asked of the service's own route as background runs (bg=1: every person's
        job goes first). Returns the leads each found, for the grid's bar."""
        assert st.store is not None
        meta = await asyncio.to_thread(st.store.read_meta, fieldjob.KIND, key) or {}
        passes = meta.get("passes") or []
        if not passes:
            return {}
        mjd = float(passes[len(passes) // 2]["mjd_mid"])
        out: dict[str, Any] = {}
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://sweep", timeout=120) as c:
            for dmin, dmax, det in ((30, 300, "123"), (30, 300, "4"), (250, 2000, "123"), (250, 2000, "4")):
                q = {"ra": round(ce.ra, 5), "dec": round(ce.dec, 5), "fov": 18, "dmin": dmin, "dmax": dmax, "det": det, "bg": 1, "mjd": mjd}
                body: dict[str, Any] = {}
                for _ in range(720):  # at most an hour a search; it waits behind people's jobs
                    r = await c.get(f"{API_PREFIX}/distant", params=q)
                    body = r.json() if r.status_code == 200 else {}
                    if body.get("state") in ("done", "failed", "error"):
                        break
                    await asyncio.sleep(5.0)
                res = body.get("result") or {}
                out[f"{dmin}-{dmax}au D{det}"] = len(res.get("candidates") or []) if body.get("state") == "done" else body.get("state")
        return out

    async def sweep_loop() -> None:
        cpu.JOB.set(_Background())  # every compute call of the sweep gives way to a person's work
        gdb = st.grid
        if gdb is not None:  # a square left "searching" by a restart is searched again
            with contextlib.suppress(Exception):
                await asyncio.to_thread(
                    lambda: gdb._conn().execute("UPDATE grid_cells SET state='new' WHERE state='searching' AND source='sweep'")
                )
        assert st.sweep is not None
        await st.sweep.loop()

    async def warm_asteroid_search(f: WarmField, field_key: str) -> None:
        """The lab's own asteroid search of a field, at the key the lab asks for: the film of the middle pass, its known bodies, then
        the search for unknown movers in it."""
        assert st.store is not None and st.scheduler is not None and st.index is not None and st.files is not None
        fmeta = st.store.read_meta(fieldjob.KIND, field_key) or {}
        passes = fmeta.get("passes") or []
        if not passes:
            return
        mjd = float(passes[len(passes) // 2]["mjd_mid"])
        k = await asyncio.to_thread(warm_key, WarmField(f.label, f.ra, f.dec, f.fov, round(mjd, 1)))
        if k is None:
            return
        fkey, fparams = k
        if not st.store.is_complete(fkey):
            fjob = st.scheduler.jobs.get(st.scheduler.by_key.get(fkey, "")) or submit_film(fkey, fparams, Priority.P2)
            while fjob.state not in ("done", "error", "cancelled"):
                await asyncio.sleep(3.0)
        if not st.store.is_complete(fkey):
            return
        meta = st.store.read_meta(filmjob.KIND, fkey)
        if st.bodies is not None and meta is not None and st.bodies.cached(fkey) is None:
            st.bodies.start(fkey, meta)
        if meta is None:
            return
        await give_way()
        params = meta["params"]
        skey = product_key(filmsearchjob.KIND, params, extra=(fkey,))
        if st.store.is_complete(skey):
            return
        job = st.scheduler.jobs.get(st.scheduler.by_key.get(skey, ""))
        if job is None or job.state in ("error", "cancelled"):
            index, files, store, wl = st.index, st.files, st.store, st.wavelengths

            async def fn(ctx: Any) -> dict[str, Any]:
                return await filmsearchjob.run_filmsearch(ctx, index, files, store, fkey, params, wl)

            job = st.scheduler.submit(
                filmsearchjob.KIND, skey, params, fn, Priority.P2, "heavy", fieldjob.film_memory_mb(float(params["fov"]))
            )
        while job.state not in ("done", "error", "cancelled"):
            await asyncio.sleep(3.0)

    targets_by_label: dict[str, dict[str, Any]] = {}

    async def warm_loop() -> None:
        assert st.warm is not None
        cpu.JOB.set(_Background())
        try:
            data = json.loads((Path(__file__).resolve().parent.parent / "data" / "targets.json").read_text(encoding="utf-8"))
            targets_by_label.update({t["label"]: t for t in data["targets"]})
        except (OSError, ValueError, KeyError):
            pass
        while True:
            await st.warm.run(ensure_warm, warm_complete)
            if st.warm.state == "done":
                # then the searches of every warm field, smallest first (each is skipped at once when already on disk)
                fields = sorted((f for f in st.warm.fields if f.film_mjd is None), key=lambda f: f.fov)
                st.warm_searches = {"done": 0, "total": len(fields), "current": ""}
                for f in fields:
                    st.warm_searches["current"] = f"{f.label} {f.fov:g}′"
                    try:
                        await warm_field_searches(f)
                    except Exception as e:  # said in /health, the next round tries again
                        st.warm_searches.setdefault("failed", {})[f"{f.label} {f.fov:g}′"] = f"{type(e).__name__}: {e}"[:200]
                    st.warm_searches["done"] += 1
                st.warm_searches["current"] = ""
            await asyncio.sleep(60.0 if st.warm.state != "done" else 600.0)

    app = FastAPI(
        default_response_class=jsonsafe.SafeJSONResponse,  # never a bare NaN in a reply (service/jsonsafe.py)
        title="SPHEREx Sky API",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=f"{API_PREFIX}/openapi.json",
        lifespan=lifespan,
    )
    app.state.sky = st
    # the searches' own routes: what else is known at a place, comets and asteroids, the movers route with pairs, Find Planet X
    app.include_router(make_router(st))
    app.include_router(make_known_router(st))
    app.include_router(make_known_field_router(st))  # the known objects of a frame at each epoch (stream B2)
    app.include_router(make_checks_router(st))  # the checks before submit (stream B2)
    app.include_router(make_pairs_router(st))
    app.include_router(make_dwarfs_router(st))
    app.include_router(make_searched_router(st))
    app.include_router(make_findings_router(st))
    app.include_router(make_clips_router(st))  # the time-lapse clips of findings (stream F3)
    app.include_router(make_brightness_router(st))
    app.include_router(make_export_router(st))
    app.include_router(make_planetx_router(st))
    app.include_router(make_grid_router(st))  # the sky grid and its coverage (product flow)
    app.include_router(make_legal_router())  # /privacy and /terms, plain server pages (Google's consent screen fetches them)
    register_accounts(app, st)  # accounts, sign-in, marks, tasks, investigations, submit (stream B3)
    # the extra data sources: deep references, cloud cross-match, tracked stack, ADES export, SSOIS, Gaia DR4 readiness
    register_extras(app)

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
        path = request.url.path
        if path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        elif path.startswith("/assets/"):
            # the build's files carry their content hash in the name
            response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        elif path == "/" or path.endswith(".html"):
            # the page itself is asked again every time, so a new release shows at once (never an old look from the cache)
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    @app.get(f"{API_PREFIX}/health")
    async def health() -> dict[str, Any]:
        """Cheap liveness and state. Async on purpose: it never queues for the thread pool behind field lookups, so Caddy's health check
        (2 s) does not mistake a busy main instance for a dead one and send visitors to the standby, which cannot build fields."""
        du = shutil.disk_usage(cfg.store_dir) if cfg.store_dir.exists() else None
        return {
            "ok": True,
            "service": SERVICE_NAME,
            "role": "standby" if STANDBY else "main",
            # the disk of the store (the keeper cleans it every hour above 75%, tools/disk_keeper.py)
            "disk": None
            if du is None
            else {"free_gb": round(du.free / 1e9, 1), "used_pct": round(100.0 * (du.total - du.free) / du.total, 1)},
            "version": __version__,
            "started_at": st.started_at,
            "uptime_s": round(time.monotonic() - st.started, 1),
            "product_versions": dict(VERSIONS),
            "index": index_health(),
            "jobs": st.scheduler.snapshot() if st.scheduler else None,
            "warm": {**st.warm.snapshot(), "searches": st.warm_searches} if st.warm else None,
            "quarter_stock": {"target": quarters.STOCK, **st.quarter_stock} if st.quarters is not None else None,
            "bd_queue": {"done": len(st.bd_queue.done), **st.bd_queue_last} if st.bd_queue is not None else None,
            "sweep": st.sweep.snapshot() if st.sweep is not None else None,
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

    def warm_known(key: str, ra: float, dec: float, fov: float, meta: dict[str, Any] | None) -> None:
        """The first time a finished field is asked for, its known objects are asked of every catalogue in the background (the lab's
        Known objects button then answers from the cache). The lab asks with the address's place, size and the passes' dates: so does
        this."""
        if key in st.known_warmed or st.loop is None or not meta or os.environ.get("SKY_KNOWN_PREWARM", "1") != "1":
            return
        st.known_warmed.add(key)
        if len(st.known_warmed) > 5000:
            st.known_warmed.clear()
        mjds = [float(q["mjd_mid"]) for q in meta.get("passes") or [] if q.get("mjd_mid") is not None]
        st.loop.call_soon_threadsafe(known_prewarm, st, ra, dec, fov, mjds)

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
            meta = fieldjob.annotate(st.store.read_meta(fieldjob.KIND, key))
            warm_known(key, ra, dec, fov, meta)
            return {"key": key, "state": "done", "params": params, "meta": meta}
        if key not in st.scheduler.by_key or st.scheduler.jobs[st.scheduler.by_key[key]].state in ("error", "cancelled"):
            wait = st.new_work.take(client_id(request, st.salt))
            if wait > 0:
                return JSONResponse(
                    {"error": "too many new fields from this address; try again shortly"}, 429, {"Retry-After": f"{wait:.0f}"}
                )
        _, job = submit_field(params, Priority.P0)
        fieldjob.remember(params["ra"], params["dec"], params["fov"])  # a request near this one joins this field
        if job is None:
            return {"key": key, "state": "done", "params": params, "meta": fieldjob.annotate(st.store.read_meta(fieldjob.KIND, key))}
        return {"key": key, "params": params, "job": job.view()}

    @app.get(f"{API_PREFIX}/film")
    def film(
        request: Request,
        ra: float = Query(..., ge=0, lt=360),
        dec: float = Query(..., ge=-90, le=90),
        fov: float = Query(20, gt=0, le=60),
        mjd: float | None = Query(None, ge=60000, le=70000),
        bg: int = Query(0, ge=0, le=1),
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
        # bg=1: the lab's own asteroid search asks for the film by itself, not a person: it must not delay a person's field
        job = submit_film(key, params, Priority.P1 if bg else Priority.P0)
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
        params = spectrum_params(ra, dec, pmra, pmdec)

        async def part(name: str, fn: Callable[[dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]]) -> dict[str, Any]:
            """One survey's answer: kept when it answered, else asked (one request at a time) and waited for LIGHTCURVES_WAIT_S. A part that
            has not answered by then is `pending` and keeps running, so the next ask finds it."""
            pk = (*key, name)
            hit = st.lightcurves.get(pk)
            if hit is not None:
                return hit
            task = st.lc_tasks.get(pk)
            if task is None:
                task = asyncio.create_task(fn(params))
                st.lc_tasks[pk] = task

                def landed(t: asyncio.Task[dict[str, Any]], pk: tuple[Any, ...] = pk) -> None:
                    st.lc_tasks.pop(pk, None)
                    if t.cancelled() or t.exception() is not None:
                        return
                    res = t.result()
                    if res["state"] != "unavailable":  # an answer that failed is said, never kept
                        st.lightcurves[pk] = res
                        if len(st.lightcurves) > 4000:
                            st.lightcurves.pop(next(iter(st.lightcurves)))

                task.add_done_callback(landed)
            done, _ = await asyncio.wait({task}, timeout=LIGHTCURVES_WAIT_S)
            if not done:
                return {"state": "pending", "points": []}
            if task.exception() is not None:
                return {"state": "unavailable", "error": type(task.exception()).__name__, "points": []}
            return task.result()

        z, nw = await asyncio.gather(part("ztf", lc_ztf), part("neowise", lc_neowise))
        return {"ztf": z, "neowise": nw}

    @app.get(f"{API_PREFIX}/resolve")
    async def resolve(name: str = Query(..., min_length=1, max_length=80)) -> Any:
        """Where SIMBAD puts a name (the globe's catalogue search for a star the snapshot lacks): {found, name (as typed), main_id,
        type, ra, dec, pmra, pmdec}; {found: false} when SIMBAD does not know it, 503 when no SIMBAD mirror answers."""
        if st.catalogue is None:
            raise HTTPException(503, "the catalogues are not ready")
        try:
            found = await st.catalogue.resolve(name)
        except RuntimeError as e:
            raise HTTPException(503, str(e)) from e
        # an unknown name is an answer too ({"found": false}), so the page does not go on to ask anyone else
        return JSONResponse(found or {"found": False, "name": name}, headers={"Cache-Control": "public, max-age=86400"})

    @app.get(f"{API_PREFIX}/identify")
    async def identify(
        ra: float = Query(..., ge=0, lt=360),
        dec: float = Query(..., ge=-90, le=90),
        pmra: float = Query(0.0, ge=-20000, le=20000),
        pmdec: float = Query(0.0, ge=-20000, le=20000),
        only: str | None = Query(None, pattern="^(simbad|gaia)$"),
    ) -> Any:
        """What SIMBAD and Gaia DR3 call a place (each part: ok / none / pending / unavailable). `only` asks one catalogue (the page asks
        both at once and shows each as it arrives); one that has not answered in 3 s is `pending` and is answered on the next ask."""
        if st.anomalies is None:
            raise HTTPException(503, "the exposure index is not loaded")
        key = (round(ra, 5), round(dec, 5), round(pmra), round(pmdec), only)
        hit = st.identified.get(key)
        if hit is not None:
            return hit
        # the place is where the source stands at the passes' epoch: the catalogues' places are carried to it with their own motions
        out = await (st.catalogue.identify(ra, dec, only=only) if st.catalogue is not None else st.anomalies.identify(ra, dec, pmra, pmdec))
        if all(v.get("state") not in ("unavailable", "pending") for v in out.values()):
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
        filled = sum(1 for v in out["flux"] if v is not None)
        if filled < 0.5 * len(out["flux"]):
            out["note"] = "most planes of the all-sky cube are empty at this pixel (a saturated core or a gap of the map)"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(jsonsafe.dumps(out, separators=(",", ":")))
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
        finding: str | None = Query(None, max_length=80, pattern=r"^[a-z]+:[0-9a-f]{6,40}$"),
    ) -> Any:
        """The spectrum of a place: the points measured so far (from `since` on); the first request starts the measurement. The
        exposures people agreed are broken are marked left out (service/exposure_flags.py); with `finding`, also those people saw a
        neighbour star touch for that find."""
        if st.spectra is None:
            raise HTTPException(503, "the exposure index is not loaded")
        params = spectrum_params(ra, dec, pmra, pmdec, epoch)
        neighbour: set[str] = set()
        if finding is not None:  # people agreed a neighbour star touches this find in some exposures: those are left out of its spectrum
            with contextlib.suppress(Exception):
                from service.accounts import people as people_mod

                neighbour = await asyncio.to_thread(people_mod.left_out, st, finding)
        run = st.spectra.get(params)
        if run is None:
            wait = st.new_work.take(client_id(request, st.salt))
            if wait > 0:
                return JSONResponse(
                    {"error": "too many new measurements from this address; try again shortly"}, 429, {"Retry-After": f"{wait:.0f}"}
                )
            run = st.spectra.start(params)
        view = run.view(since)
        if exposure_flags.bad() or neighbour:
            view["points"] = exposure_flags.mark_points(view.get("points") or [], neighbour)
        return view

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
    async def coverage_all(request: Request) -> Response:
        """Exposure centres per HEALPix cell (nested, order 3) and release: the globe's and the Index page's coverage layer.
        Kept in memory and on disk for the current state of the index, served with an ETag (304 when the browser has it)."""
        index_path = cfg.store_dir / "index" / "exposures.sqlite"
        if not index_path.is_file():
            raise HTTPException(503, "no exposure index")
        if st.coverage is None or st.coverage[2] != index_key(index_path):
            async with st.coverage_lock:
                if st.coverage is None or st.coverage[2] != index_key(index_path):
                    st.coverage = await asyncio.to_thread(coverage_payload, index_path)
        body, etag, _ = st.coverage
        headers = {"Cache-Control": "public, max-age=300, must-revalidate", "ETag": etag}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return Response(body, media_type="application/json", headers=headers)

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
