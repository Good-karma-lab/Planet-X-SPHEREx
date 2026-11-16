"""The coverage API of the sky grid (service.grid): what the machine and people have done with each square.

    GET  /api/grid/meta                  {scheme, total, cell_arcmin, counts:{searched, viewed, with_finds}, rate_per_hour}
    GET  /api/grid/coverage              {cells:[{id, state, finds, viewers}]}    every square that is not "new"
    GET  /api/grid/cells?ra=&dec=&radius= {cells:[{id, ra, dec, corners, state, finds, viewers, bar, searched_at}]}
    GET  /api/grid/cell/{id}             the same + finds:[finding ids] + field_key + open:{ra, dec, fov}
    POST /api/grid/cell/{id}/viewed      {result:"nothing"|"marks"} → {viewers}

A viewer is the signed-in user when the accounts module tells who it is (`st.current_user(request)`), else an anonymous id kept in
the `sky_vid` cookie; one viewer counts once per square.
"""

from __future__ import annotations

import json
import secrets
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel

from service import grid
from service.grid import GridDB

MAX_RADIUS_DEG = 5.0
VIEWER_COOKIE = "sky_vid"


class Viewed(BaseModel):
    result: Literal["nothing", "marks"] | None = None


def make_grid_router(st: Any, db: GridDB | None = None) -> APIRouter:
    """`db`: the grid database; by default the service's own (`st.grid`, opened at start)."""
    router = APIRouter()

    def D() -> GridDB:  # noqa: N802
        g = db or getattr(st, "grid", None)
        if g is None:
            raise HTTPException(503, "the product database is not open")
        return g

    @router.get("/api/grid/meta")
    async def meta() -> Any:
        return {
            "scheme": grid.SCHEME,
            "total": grid.TOTAL,
            "cell_arcmin": grid.FIELD_FOV_ARCMIN,
            "counts": D().counts(),
            "rate_per_hour": D().rate_per_hour(),
        }

    @router.get("/api/grid/coverage")
    async def coverage(response: Response) -> Any:
        response.headers["Cache-Control"] = "no-store"
        return {"cells": D().coverage()}

    @router.get("/api/grid/cells")
    async def cells(ra: float = Query(..., ge=0, lt=360), dec: float = Query(..., ge=-90, le=90), radius: float = Query(1.0, gt=0)) -> Any:
        if radius > MAX_RADIUS_DEG:
            raise HTTPException(400, f"radius is at most {MAX_RADIUS_DEG} degrees")
        cs = grid.cells_near(ra, dec, radius)
        ids = [c.id for c in cs]
        rows, views = D().rows(ids), D().viewers(ids)
        return {"cells": [grid.view_of(c, rows.get(c.id), views.get(c.id, 0)) for c in cs]}

    def one(cid: str) -> dict[str, Any]:
        try:
            ce = grid.by_id(cid)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        row = D().get(cid)
        out = grid.view_of(ce, row, D().viewers([cid]).get(cid, 0))
        out["finds"] = json.loads((row or {}).get("find_ids") or "[]") or finds_in(st, ce)
        out["n_finds"] = int((row or {}).get("finds") or 0)
        out["field_key"] = (row or {}).get("field_key") or field_key_of(st, ce)
        out["open"] = {"ra": round(ce.ra, 5), "dec": round(ce.dec, 5), "fov": grid.FIELD_FOV_ARCMIN}
        out["error"] = (row or {}).get("error")
        return out

    @router.get("/api/grid/cell/{cid}")
    async def cell(cid: str) -> Any:
        return one(cid)

    @router.post("/api/grid/cell/{cid}/viewed")
    async def viewed(cid: str, body: Viewed, request: Request, response: Response) -> Any:
        try:
            grid.parse_id(cid)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        who = None
        cu = getattr(st, "current_user", None)
        if callable(cu):
            u = cu(request)
            who = f"user:{u['id']}" if u else None
        if who is None:
            vid = request.cookies.get(VIEWER_COOKIE)
            if not vid or len(vid) > 64:
                vid = secrets.token_urlsafe(16)
                response.set_cookie(VIEWER_COOKIE, vid, max_age=3600 * 24 * 365, httponly=True, samesite="lax")
            who = f"anon:{vid}"
        return {"viewers": D().view(cid, who, body.result)}

    return router


def field_key_of(st: Any, ce: grid.Cell) -> str | None:
    """The key of the 20′ field at the square's centre (the lab opens it), when the exposure index is loaded."""
    if getattr(st, "index", None) is None:
        return None
    from service.products.store import product_key
    from service.workers import fieldjob

    try:
        params = fieldjob.canonical(ce.ra, ce.dec, grid.FIELD_FOV_ARCMIN)
        return product_key(fieldjob.KIND, params, extra=(fieldjob.exposure_signature(st.index, params),))
    except Exception:
        return None


FIND_KINDS = ("mover", "browndwarf", "pair", "film", "peak")  # red sources and spectral anomalies are not finds of a square's search


def finds_in(st: Any, ce: grid.Cell) -> list[str]:
    """The ids of the Findings rows that lie in the square (for squares searched before the grid: their finds have no ids kept)."""
    import sqlite3

    store_dir = getattr(getattr(st, "cfg", None), "store_dir", None)
    path = store_dir / "findings.db" if store_dir is not None else None
    if path is None or not path.is_file():
        return []
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2) as c:
            q = (
                f"SELECT id FROM finds WHERE kind IN ({','.join('?' * len(FIND_KINDS))}) AND dec>=? AND dec<? AND ra>=? AND ra<?"
                " ORDER BY id LIMIT 500"
            )
            return [r[0] for r in c.execute(q, (*FIND_KINDS, ce.dec0, ce.dec1, ce.ra0, ce.ra1))]
    except sqlite3.Error:
        return []
