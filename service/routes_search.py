"""The routes of the searches: catalogue context, the film's found tracks, pairs, Find Planet X and the known bodies.

`make_router(st)` takes the service state and returns a router for the app (`app.include_router(make_router(st))`); the state carries
the objects the routes use: `st.catalogue` (service.context.CatalogueContext), `st.index`, `st.store`, `st.scheduler`, `st.files`,
`st.wavelengths`.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from engine.filmsearch import match_known
from service.context import CatalogueContext
from service.jobs.scheduler import Priority
from service.products.store import product_key
from service.workers import filmjob, filmsearchjob

API_PREFIX = "/api/v1"
RETRY_AFTER_S = 120.0  # a failed search is searched again by a request after this long


def make_router(st: Any, prefix: str = API_PREFIX) -> APIRouter:
    r = APIRouter()

    @r.get(f"{prefix}/context")
    async def context(
        ra: float = Query(..., ge=0, lt=360),
        dec: float = Query(..., ge=-90, le=90),
        epoch: float = Query(2025.9, ge=1900, le=2100),
        radius: float = Query(6.0, gt=0, le=60),
    ) -> Any:
        """What SIMBAD, Gaia DR3, 2MASS, AllWISE, CatWISE, VSX and NED know about the place at the epoch (ok / none / unavailable each)."""
        cat: CatalogueContext | None = getattr(st, "catalogue", None)
        if cat is None:
            raise HTTPException(503, "the catalogue client is not running")
        return await cat.context(ra, dec, epoch, radius)

    def film_track_view(film_key: str) -> dict[str, Any]:
        """The found tracks of a finished film, the job that finds them, or why not yet."""
        if st.store is None or st.scheduler is None or st.index is None or st.files is None:
            raise HTTPException(503, "the exposure index is not loaded")
        fmeta = st.store.read_meta(filmjob.KIND, film_key)
        if fmeta is None or not st.store.is_complete(film_key):
            return {"state": "running", "phase": "the film is not finished", "error": None, "tracks": [], "control": None}
        params = fmeta["params"]
        key = product_key(filmsearchjob.KIND, params, extra=(film_key,))
        if st.store.is_complete(key):
            meta = st.store.read_meta(filmsearchjob.KIND, key) or {}
            return {"state": "done", "phase": "done", "error": None, **track_reply(st, film_key, meta)}
        job = st.scheduler.jobs.get(st.scheduler.by_key.get(key, ""))
        if job is not None:
            st.scheduler.poll(job.id)  # a page that asks again keeps its job a person's job (a job nobody asks about is abandoned)
        if job is not None and job.state == "error" and time.time() - (job.finished or 0.0) < RETRY_AFTER_S:
            return {"state": "error", "phase": job.phase, "error": job.error, "tracks": [], "control": None}  # said once, asked again later
        if job is None or job.state in ("error", "cancelled"):
            index, files, store, wl = st.index, st.files, st.store, st.wavelengths

            async def fn(ctx: Any) -> dict[str, Any]:
                return await filmsearchjob.run_filmsearch(ctx, index, files, store, film_key, params, wl)

            job = st.scheduler.submit(filmsearchjob.KIND, key, params, fn, Priority.P1, "heavy", 1500)
        if job.state == "error":
            return {"state": "error", "phase": job.phase, "error": job.error, "tracks": [], "control": None}
        return {"state": "running", "phase": job.phase, "done": job.done, "total": job.total, "error": None, "tracks": [], "control": None}

    @r.get(f"{prefix}/film/{{key}}/tracks")
    async def film_tracks(key: str) -> Any:
        """Unknown movers found in the film of one pass: straight-line tracks through the candidates of the motion maps, with the chance
        control."""
        if not key.isalnum():
            raise HTTPException(400)
        return film_track_view(key)

    return r


def track_reply(st: Any, film_key: str, meta: dict[str, Any]) -> dict[str, Any]:
    """The tracks of a film search with the known body that explains each (when the film's known bodies are on disk) and ids."""
    tracks = list(meta.get("tracks", []))
    bodies: list[dict[str, Any]] = []
    kb = getattr(st, "bodies", None)
    hit = kb.cached(film_key) if kb is not None else None
    if hit is not None:
        bodies = hit.get("bodies", [])
    known = match_known(tracks, bodies, float(meta["grid"]["step_arcsec"])) if tracks else []
    out = []
    for i, (tr, k) in enumerate(zip(tracks, known, strict=True)):
        out.append(
            {
                "id": i,
                **tr,
                "dist_au": tr.get("dist_au"),
                "known": k,
                "pts": [{"f": p["f"], "x": p["x"], "y": p["y"], "z": p["z"], "how": p["how"]} for p in tr["pts"]],
            }
        )
    return {
        "tracks": out,
        "control": meta.get("control"),
        "grid": meta.get("grid"),
        "candidates": meta.get("candidates"),
        "bodies_known": hit is not None,
    }
