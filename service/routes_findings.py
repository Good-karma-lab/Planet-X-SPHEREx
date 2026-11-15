# ruff: noqa: E501
"""`GET /api/v1/findings`: the finds of every searched field, read from findings.db (service.findings); nothing is computed on the request."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from service.findings import KINDS, query


def make_findings_router(st: Any, prefix: str = "/api/v1") -> APIRouter:
    router = APIRouter()

    def need() -> Any:
        f = getattr(st, "findings", None)
        if f is None:
            raise HTTPException(503, "the findings database is not loaded")
        return f

    @router.get(f"{prefix}/findings")
    async def findings(
        kind: str | None = Query(None),
        status: str | None = Query(None, pattern="^(all|unexplained|unchecked|known)$"),
        place: str | None = Query(None),
        limit: int = Query(300, ge=1, le=2000),
        offset: int = Query(0, ge=0),
    ) -> Any:
        """{count, total, counts[kind]{all, unexplained, known}, identification{pending, unavailable, catalogues}, finds[{id, kind, place, ra, dec, rate,
        snr, score, chance, status, verdict, idents[{catalogue, state, name, sep, type}], data, scans}]}."""
        if kind is not None and kind not in KINDS:
            raise HTTPException(422, f"kind is one of {', '.join(KINDS)}")
        f = need()
        body: dict[str, Any] = await asyncio.to_thread(query, f.db, kind, status, place, limit, offset)
        return body

    @router.get(f"{prefix}/findings/{{find_id}}")
    async def one(find_id: str) -> Any:
        f = need()
        body: dict[str, Any] = await asyncio.to_thread(query, f.db, None, "all", None, 5000, 0)
        for x in body["finds"]:
            if x["id"] == find_id:
                return x
        raise HTTPException(404, "no such find")

    return router
