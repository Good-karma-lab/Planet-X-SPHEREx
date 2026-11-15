"""`GET /api/v1/brightness`: the brightness of a saturated star in every pass and detector of a finished field (service.brightness)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from service.brightness import brightness_of
from service.products.store import product_key
from service.workers import fieldjob


def make_brightness_router(st: Any, prefix: str = "/api/v1") -> APIRouter:
    router = APIRouter()
    cache: dict[tuple[str, int, int], dict[str, Any]] = {}

    @router.get(f"{prefix}/brightness")
    async def brightness(
        ra: float = Query(..., ge=0, lt=360),
        dec: float = Query(..., ge=-90, le=90),
        fov: float = Query(5, gt=0, le=60),
        field_ra: float | None = Query(None, ge=0, lt=360, description="the field's own centre when it is not the star's place"),
        field_dec: float | None = Query(None, ge=-90, le=90),
        pmra: float = Query(0.0, ge=-20000, le=20000),
        pmdec: float = Query(0.0, ge=-20000, le=20000),
    ) -> Any:
        """Ring photometry outside the saturated core, per pass and detector, with the scatter that stars of the same field show."""
        if st.index is None or st.store is None:
            raise HTTPException(503, "the exposure index is not loaded")
        params = fieldjob.canonical(ra if field_ra is None else field_ra, dec if field_dec is None else field_dec, fov)
        key = product_key(fieldjob.KIND, params, extra=(fieldjob.exposure_signature(st.index, params),))
        if not st.store.is_complete(key):
            return {"state": "waiting", "error": "the field is not finished: open it first"}
        ck = (key, round(ra * 1e5), round(dec * 1e5))
        if ck not in cache:
            try:
                cache[ck] = await brightness_of(st.store, key, ra, dec, pmra, pmdec)
            except ValueError as e:
                return {"state": "failed", "error": str(e)}
            if len(cache) > 200:
                cache.pop(next(iter(cache)))
        return {"state": "done", "field": key, "result": cache[ck]}

    return router
