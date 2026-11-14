"""The routes of Find Planet X (docs/plan/02-methods): the distance search of one pass, the deep sum of passes, the link of passes and
the jump.

`make_planetx_router(st)` returns a router for the app (`app.include_router(make_planetx_router(st))`). The state carries `index`, `store`,
`scheduler`, `files`, `wavelengths` (as the other routes use them) and, optionally, `new_work` (the per-client bucket) and `salt`.

Job routes answer {key, state: running | done | error, phase, done, total, error, result, estimate_s, job}; the page asks again every 2–3 s.
"""

from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from service.jobs.scheduler import Job, Priority
from service.products.store import product_key
from service.ratelimit import client_id
from service.reader.psfs import PsfProvider
from service.workers import deepjob, distantjob, linkjob
from service.workers.planetx import honest_words

API_PREFIX = "/api/v1"


def _state(job: Job) -> str:
    if job.state == "done":
        return "done"
    if job.state in ("error", "cancelled"):
        return "error"
    return "running"


def make_planetx_router(st: Any, prefix: str = API_PREFIX) -> APIRouter:
    r = APIRouter()
    estimates: dict[str, int] = {}
    psfs: dict[str, PsfProvider] = {}

    def need(*names: str) -> None:
        if any(getattr(st, n, None) is None for n in names):
            raise HTTPException(503, "the exposure index is not loaded")

    def throttled(request: Request) -> JSONResponse | None:
        bucket = getattr(st, "new_work", None)
        if bucket is None:
            return None
        wait = bucket.take(client_id(request, getattr(st, "salt", "")))
        if wait > 0:
            return JSONResponse(
                {"error": "too many new searches from this address; try again shortly"}, 429, {"Retry-After": f"{wait:.0f}"}
            )
        return None

    def psf_provider() -> PsfProvider:
        if "p" not in psfs:
            psfs["p"] = PsfProvider(st.files, st.cfg.store_dir)
        return psfs["p"]

    def job_reply(key: str, job: Job, estimate: int | None) -> dict[str, Any]:
        st.scheduler.poll(job.id)  # a page that asks again keeps its job a person's job (a job nobody asks about is abandoned)
        return {
            "key": key,
            "state": _state(job),
            "phase": job.phase,
            "done": job.done,
            "total": job.total,
            "error": job.error,
            "result": None,
            "estimate_s": estimate,
            "job": job.view(),
        }

    def done_reply(kind: str, key: str, result: dict[str, Any]) -> dict[str, Any]:
        return {
            "key": key,
            "state": "done",
            "phase": "done",
            "done": 0,
            "total": 0,
            "error": None,
            "result": result,
            "estimate_s": None,
            "job": None,
        }

    @r.get(f"{prefix}/distant")
    async def distant(
        request: Request,
        ra: float = Query(..., ge=0, lt=360),
        dec: float = Query(..., ge=-90, le=90),
        fov: float = Query(18.0, gt=2, le=20),
        mjd: float | None = Query(None, ge=40000, le=80000),
        pass_: int | None = Query(None, alias="pass", ge=0, le=99),
        dmin: float = Query(30.0, ge=5, le=5000),
        dmax: float = Query(300.0, ge=5, le=5000),
        det: str = Query("123"),
        mu_scale: float = Query(1.0, ge=0.2, le=2.0),
        plan: int = Query(0, ge=0, le=1),
        bg: int = Query(0, ge=0, le=1, description="1: a background run (overnight scans): it gives way to every person's job"),
    ) -> Any:
        """Find Planet X, one pass: the distance search (create or attach); plan=1 answers at once with the number of hypotheses and the
        time."""
        need("index", "store", "scheduler", "files")
        if dmax < 1.2 * dmin:
            raise HTTPException(400, "dmax must be at least 1.2 times dmin")
        dets = tuple(sorted({int(c) for c in det if c in "123456"})) or (1, 2, 3)
        params = distantjob.distant_params(ra, dec, fov, mjd, pass_, dmin, dmax, dets, mu_scale)
        if plan:
            return await distantjob.plan_async(st.index, params)
        exps, _pno, pk, info, _ = distantjob.selection(st.index, params)
        if not exps:
            return {
                "state": "done",
                "key": None,
                "result": {"status": "no-coverage", "passes": info, "notes": ["no exposure of these detectors here"]},
            }
        key = product_key(distantjob.KIND, params, extra=(distantjob.signature(exps), str(pk)))
        if st.store.is_complete(key):
            return done_reply(distantjob.KIND, key, st.store.read_meta(distantjob.KIND, key) or {})
        job = st.scheduler.jobs.get(st.scheduler.by_key.get(key, ""))
        if job is None or job.state in ("error", "cancelled"):
            limited = throttled(request)
            if limited is not None:
                return limited
            if key not in estimates:
                estimates[key] = int((await distantjob.plan_async(st.index, params)).get("estimate_s", 0) or 0)
            index, files, store, wl, ps = st.index, st.files, st.store, getattr(st, "wavelengths", None), psf_provider()

            async def fn(ctx: Any) -> dict[str, Any]:
                return await distantjob.run_distant(ctx, index, files, store, wl, ps, params)

            job = st.scheduler.submit(distantjob.KIND, key, params, fn, Priority.P2 if bg else Priority.P0, "heavy", 2500)
        return job_reply(key, job, estimates.get(key))

    @r.get(f"{prefix}/deep")
    async def deep(
        request: Request,
        fields: str | None = Query(None, description="ra,dec,mjd;ra,dec,mjd;...  the field centre and the middle date of each pass (2-6)"),
        centre: str | None = Query(None, description="ra,dec,mjd of one place in one pass (the other centres follow from `passes`)"),
        passes: str | None = Query(None, description="mjd;mjd;...  the middle dates of the passes, with `centre`"),
        fov: float = Query(18.0, gt=2, le=60),
        dmin: float = Query(60.0, ge=5, le=5000),
        dmax: float = Query(110.0, ge=5, le=5000),
        det: str = Query("123"),
        keep: float = Query(6.0, ge=4, le=12),
        nmax: int = Query(15, ge=4, le=60),
        prior: str | None = Query(None, description="imin,imax,fmin,fmax: only prograde orbits like the predicted Planet Nine"),
        background: int = Query(0, ge=0, le=1, description="1: low priority, nobody watches (accepted from this machine only)"),
        plan: int = Query(0, ge=0, le=1),
        bg: int = Query(0, ge=0, le=1, description="1: a background run (overnight scans): it gives way to every person's job"),
    ) -> Any:
        """Find Planet X, the sum of passes: plan=1 (or `centre` + `passes` without `fields`) answers at once with the guessed field
        centres, the number of orbits and the time; `fields` starts the job."""
        need("index", "store", "scheduler", "files")
        if dmax < 1.1 * dmin:
            raise HTTPException(400, "dmax must be at least 1.1 times dmin")
        dets = tuple(sorted({int(c) for c in det if c in "123456"})) or (1, 2, 3)
        fl: list[list[float]] = []
        guessed = False
        try:
            if fields:
                for item in [x for x in fields.split(";") if x][: deepjob.MAX_FIELDS]:
                    a, d, m = (float(x) for x in item.split(","))
                    if not (0 <= a < 360 and -90 <= d <= 90 and 40000 < m < 80000):
                        raise ValueError
                    fl.append([a, d, m])
            elif centre and passes:
                ra0, de0, m0 = (float(x) for x in centre.split(","))
                ms = [float(x) for x in passes.split(";") if x][: deepjob.MAX_FIELDS]
                if not (0 <= ra0 < 360 and -90 <= de0 <= 90) or len(ms) < 2:
                    raise ValueError
                fl = deepjob.auto_fields(ra0, de0, m0, ms, math.sqrt(dmin * dmax))
                guessed = True
        except ValueError as e:
            raise HTTPException(400, "fields: ra,dec,mjd;ra,dec,mjd;...   centre: ra,dec,mjd   passes: mjd;mjd;...") from e
        if len(fl) < 2:
            raise HTTPException(400, "fields: at least two passes (ra,dec,mjd;ra,dec,mjd;...), or centre and passes")
        pri: tuple[float, float, float, float] | None = None
        if prior:
            try:
                a_, b_, c_, d_ = (float(x) for x in prior.split(","))
            except ValueError as e:
                raise HTTPException(400, "prior: imin,imax,fmin,fmax") from e
            if not (0 <= a_ < b_ <= 90 and 0 < c_ < d_ <= 1):
                raise HTTPException(400, "prior: 0 <= imin < imax <= 90, 0 < fmin < fmax <= 1")
            pri = (a_, b_, c_, d_)
        params = deepjob.deep_params(fl, fov, dmin, dmax, dets, keep, nmax, pri)
        if plan or guessed:
            return await deepjob.plan_async(st.index, params)
        sels = deepjob.selections(st.index, params)
        key = product_key(deepjob.KIND, params, extra=(deepjob.signature(sels),))
        if st.store.is_complete(key):
            return done_reply(deepjob.KIND, key, st.store.read_meta(deepjob.KIND, key) or {})
        job = st.scheduler.jobs.get(st.scheduler.by_key.get(key, ""))
        if job is None or job.state in ("error", "cancelled"):
            limited = throttled(request)
            if limited is not None:
                return limited
            if key not in estimates:
                estimates[key] = int((await deepjob.plan_async(st.index, params)).get("estimate_s") or 0)
            index, files, store, wl, ps = st.index, st.files, st.store, getattr(st, "wavelengths", None), psf_provider()

            async def fn(ctx: Any) -> dict[str, Any]:
                return await deepjob.run_deep(ctx, index, files, store, wl, ps, params)

            if background and (request.client is None or request.client.host not in ("127.0.0.1", "::1")):
                raise HTTPException(403, "background searches are started from the machine itself")
            job_priority = Priority.P3 if background else Priority.P2 if bg else Priority.P0
            job = st.scheduler.submit(deepjob.KIND, key, params, fn, job_priority, "heavy", 4000)
        return job_reply(key, job, estimates.get(key))

    @r.get(f"{prefix}/distant/{{key}}/bin")
    def distant_bin(key: str) -> Response:
        need("store")
        if not key.isalnum():
            raise HTTPException(400)
        data = st.store.read_part(distantjob.KIND, key, "bin")
        if data is None:
            raise HTTPException(404, "no such search, or it has not finished")
        return Response(data, media_type="application/octet-stream", headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @r.get(f"{prefix}/link")
    def link(
        request: Request,
        keys: str = Query(..., description="two or three keys of finished /distant searches, comma separated"),
        tol: float = Query(6.0, ge=1, le=30),
        all_: int = Query(0, alias="all", ge=0, le=1),
    ) -> Any:
        """Find Planet X, link: one orbit through the candidates of two or three passes; the distance with its error, the third pass
        confirms."""
        need("store", "scheduler")
        ks = [k for k in keys.split(",") if k]
        if not 2 <= len(ks) <= 3 or any(not k.isalnum() for k in ks):
            raise HTTPException(400, "keys: two or three keys of finished /distant searches")
        params = linkjob.link_params(ks, tol, bool(all_))
        key = product_key(linkjob.KIND, params)
        if st.store.is_complete(key):
            return done_reply(linkjob.KIND, key, st.store.read_meta(linkjob.KIND, key) or {})
        job = st.scheduler.jobs.get(st.scheduler.by_key.get(key, ""))
        if job is None or job.state in ("error", "cancelled"):
            store = st.store

            async def fn(ctx: Any) -> dict[str, Any]:
                return await linkjob.run_link(ctx, store, params)

            job = st.scheduler.submit(linkjob.KIND, key, params, fn, Priority.P0, "light", 400)
        return job_reply(key, job, 10)

    @r.get(f"{prefix}/jump")
    def jump(
        key: str = Query(...),
        i: int = Query(0, ge=0, le=99),
        mjd: float = Query(..., ge=40000, le=80000),
        corridor: float = Query(5.0, ge=0.5, le=60),
        dmin: float | None = Query(None, ge=5, le=5000),
        dmax: float | None = Query(None, ge=5, le=5000),
    ) -> Any:
        """Find Planet X, the jump: where a candidate of one pass is in the next pass (a line of places by distance) and which known
        candidates lie near it."""
        need("store")
        if not key.isalnum():
            raise HTTPException(400)
        out = linkjob.run_jump(st.store, key, i, mjd, corridor, dmin, dmax)
        if out is None:
            raise HTTPException(404, "no such one-pass search or candidate (run /distant first)")
        return out

    @r.get(f"{prefix}/planetx/honest")
    def honest() -> dict[str, Any]:
        """The wording that goes next to every Find Planet X result."""
        return honest_words("distant")

    return r
