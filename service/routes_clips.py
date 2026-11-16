"""`GET /api/findings/{id}/clip.webp`: the looping time-lapse clip of a finding (service.clips), rendered once and kept.

    200 image/webp     the clip (immutable: the address carries the clip version)
    202 {state}        its field is being built; ask again (Retry-After)
    404 {state, reason} no clip can be made (no SPHEREx field covers the place) or no such finding

`GET /api/findings/{id}/clip` says the same as JSON, with what the clip shows (field, window, the dates of its frames).
Both also answer under /api/v1.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from service.clips import ClipPending, Clips, ClipUnavailable, clips_dir
from service.findings import find_row
from service.jobs.scheduler import Priority
from service.products.store import Store, product_key
from service.workers import fieldjob


def clips_of(st: Any) -> Clips:
    """The service's clips, made at the first use (the store exists only once the service has started)."""
    c: Clips | None = getattr(st, "clips", None)
    if c is not None:
        return c
    if st.store is None:
        raise HTTPException(503, "the product store is not loaded")

    async def build(own: Store, params: dict[str, Any]) -> str | None:
        """A 5′ field for a clip, built by the ordinary field job into the clips' own store, in the background queue."""
        if st.index is None or st.scheduler is None or st.files is None:
            return None
        p = fieldjob._rounded(params["ra"], params["dec"], params["fov"])
        key = "c" + product_key(fieldjob.KIND, p, extra=(fieldjob.exposure_signature(st.index, p),))[1:]
        if own.is_complete(key):
            return key
        index, files, wl = st.index, st.files, st.wavelengths

        async def fn(ctx: Any) -> dict[str, Any]:
            return await fieldjob.run_field(ctx, index, files, own, p, wl)

        job = st.scheduler.submit("clipfield", key, p, fn, Priority.P2, "heavy", fieldjob.memory_estimate_mb(p["fov"]))
        while job.state not in ("done", "error", "cancelled"):
            await asyncio.sleep(1.0)
            st.scheduler.poll(job.id)
        if job.state != "done":
            raise RuntimeError(job.error or job.state)
        return key

    st.clips = Clips(st.store, clips_dir(st.cfg.store_dir), build)
    return st.clips  # type: ignore[no-any-return]


def make_clips_router(st: Any) -> APIRouter:
    router = APIRouter()

    def the_find(find_id: str) -> dict[str, Any]:
        f = getattr(st, "findings", None)
        if f is None:
            raise HTTPException(503, "the findings database is not loaded")
        row = find_row(f.db, find_id)
        if row is None:
            raise HTTPException(404, "no such finding")
        return row

    async def clip(find_id: str) -> Any:
        clips = clips_of(st)
        find = await asyncio.to_thread(the_find, find_id)
        try:
            path = await clips.get(find)
        except ClipPending:
            return JSONResponse({"state": "building"}, 202, headers={"Retry-After": "5", "Cache-Control": "no-store"})
        except ClipUnavailable as e:
            return JSONResponse({"state": "none", "reason": str(e)}, 404, headers={"Cache-Control": "no-store"})
        return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "public, max-age=31536000, immutable"})

    async def clip_info(find_id: str) -> Any:
        clips = clips_of(st)
        find = await asyncio.to_thread(the_find, find_id)
        return {**clips.summary(find), "shows": clips.info(find_id)}

    for prefix in ("/api", "/api/v1"):
        router.add_api_route(f"{prefix}/findings/{{find_id}}/clip.webp", clip, methods=["GET"], include_in_schema=prefix == "/api")
        router.add_api_route(f"{prefix}/findings/{{find_id}}/clip", clip_info, methods=["GET"], include_in_schema=prefix == "/api")
    return router
