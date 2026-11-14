"""`GET /api/v1/browndwarfs`: the brown dwarf candidates of a field (service.browndwarfs)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from service.workers import fieldjob


def make_dwarfs_router(st: Any, prefix: str = "/api/v1") -> APIRouter:
    from service.products.store import product_key

    router = APIRouter()

    @router.get(f"{prefix}/browndwarfs")
    async def dwarfs_of(
        ra: float = Query(..., ge=0, lt=360), dec: float = Query(..., ge=-90, le=90), fov: float = Query(5, gt=0, le=60)
    ) -> Any:
        """Red sources of a finished field that move between the passes and that Gaia does not explain, with their evidence: motion,
        colour, S/N, the Gaia verdict and the track of the search between passes that stands there."""
        dwarfs = getattr(st, "dwarfs", None)
        if st.index is None or st.store is None or dwarfs is None:
            raise HTTPException(503, "the exposure index is not loaded")
        params = fieldjob.canonical(ra, dec, fov)
        key = product_key(fieldjob.KIND, params, extra=(fieldjob.exposure_signature(st.index, params),))
        if not st.store.is_complete(key):
            return {"state": "waiting", "error": "", "field": key}
        body: dict[str, Any] = dwarfs.view(dwarfs.get_or_start(key))
        return body

    return router
