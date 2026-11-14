"""`GET /api/v1/searched`: every field a search ran on, with its result in one line (service.searched)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException


def make_searched_router(st: Any, prefix: str = "/api/v1") -> APIRouter:
    router = APIRouter()

    @router.get(f"{prefix}/searched")
    async def searched() -> Any:
        """{count, fields: [{key, ra, dec, fov, state, kinds, found, nothing, best, searched_at, open}]}; cacheable for a minute."""
        s = getattr(st, "searched", None)
        if s is None or st.store is None:
            raise HTTPException(503, "the product store is not loaded")
        body: dict[str, Any] = await s.get()
        return body

    return router
